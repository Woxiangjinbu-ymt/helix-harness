"""仓库级隔离运行时与三层客观评测器（repo-suite-v1）。

三层客观评分体系（总分 0-100）：
1. Layer 1 · 硬断言得分（S_checks）    —— command（构建/类型检查/单测）+ file_assert + playwright DOM 几何断言
2. Layer 2 · 轨迹工具分（S_trajectory）—— 基于 events.jsonl 的 100% 确定性计算（工具报错率、先读后改率、改后自测、收敛效率）
3. Layer 3 · 冻结二元核查（S_rubric）  —— 可选 0/1 核查清单（无 rubric 项时权重自动归零并重归一）
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time
from typing import Any, Callable

from mtcode.agent import (
    Agent,
    ErrorEvent,
    HookEvent,
    LoopComplete,
    ToolResultEvent,
    ToolUseEvent,
    TurnComplete,
    UsageEvent,
)
from mtcode.client import create_client
from mtcode.conversation import ConversationManager
from mtcode.hooks.engine import HookEngine
from mtcode.permissions import (
    DangerousCommandDetector,
    PathSandbox,
    PermissionChecker,
    PermissionMode,
    RuleEngine,
)
from mtcode.tools import ToolRegistry
from mtcode.tools.base import TextDelta
from mtcode.tools.bash import Bash
from mtcode.tools.edit_file import EditFile
from mtcode.tools.glob import Glob
from mtcode.tools.grep import Grep
from mtcode.tools.load_skill import LoadSkill
from mtcode.tools.read_file import ReadFile
from mtcode.tools.write_file import WriteFile
from ..strategy import FrozenSkillLoader
from .harness import GuidedTool, RepoHarnessInputs, build_guidance_hooks
from .store import RepoRSIStore

_VERIFY_CMD_RE = re.compile(
    r"\b(pytest|unittest|npm\s+run\s+(build|test|lint|typecheck|check)|pnpm\s+(build|test|lint|typecheck)|yarn\s+(build|test)|tsc|vite\s+build|eslint|ruff|mypy|playwright|vitest|jest|cargo\s+test|go\s+test|make\s+(test|check|build)|python3?\s+\S*(test|verify|check|smoke)\S*|node\s+\S*(test|verify|check)\S*)\b",
    re.IGNORECASE,
)


@dataclass
class CaseEvalResult:
    case_id: str
    run_index: int
    version: int
    score: float
    checks_score: float
    trajectory_score: float
    rubric_score: float | None
    terminal: str
    duration_ms: int
    turns: int
    checks_detail: list[dict[str, Any]] = field(default_factory=list)
    trajectory_detail: dict[str, Any] = field(default_factory=dict)
    rubric_detail: list[dict[str, Any]] = field(default_factory=list)
    trace_path: str = ""
    assistant_output: str = ""

    def to_public_observation(self) -> dict[str, Any]:
        """转换为可安全送给优化器的公开观测摘要（不含私密凭据）。"""
        failed_checks = [
            {
                "id": c["id"],
                "type": c["type"],
                "description": c.get("description", ""),
                "reason": c.get("reason", ""),
            }
            for c in self.checks_detail
            if not c.get("passed")
        ]
        failed_rubric = [
            {"id": r["id"], "criterion": r["criterion"], "evidence": r.get("evidence", "")}
            for r in self.rubric_detail
            if not r.get("passed")
        ]
        return {
            "case_id": self.case_id,
            "run": self.run_index,
            "version": self.version,
            "score": self.score,
            "checks_score": self.checks_score,
            "trajectory_score": self.trajectory_score,
            "rubric_score": self.rubric_score,
            "terminal": self.terminal,
            "turns": self.turns,
            "duration_ms": self.duration_ms,
            "failed_checks": failed_checks,
            "failed_rubric": failed_rubric,
            "trajectory_metrics": self.trajectory_detail,
        }


def prepare_isolated_workspace(
    repo_root: Path,
    base_commit: str,
    dest: Path,
    *,
    base_patch: str = "",
    link_caches: tuple[str, ...] = ("node_modules", ".venv"),
) -> Path:
    """用 git archive 从固定提交导出干净工作树，避免污染用户当前工作目录且支持并发评测。"""
    dest.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(
        ["git", "archive", "--format=tar", base_commit],
        cwd=repo_root,
        capture_output=True,
        timeout=30,
    )
    if archive.returncode != 0:
        raise RuntimeError(f"git archive 失败（commit={base_commit}）：{archive.stderr.decode(errors='ignore')}")
    extract = subprocess.run(
        ["tar", "-xf", "-"],
        cwd=dest,
        input=archive.stdout,
        capture_output=True,
        timeout=30,
    )
    if extract.returncode != 0:
        raise RuntimeError(f"解包隔离工作树失败：{extract.stderr.decode(errors='ignore')}")

    if base_patch.strip():
        patch = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            cwd=dest,
            input=base_patch.encode("utf-8"),
            capture_output=True,
            timeout=20,
        )
        if patch.returncode != 0:
            raise RuntimeError(f"应用 base_patch 失败：{patch.stderr.decode(errors='ignore')}")

    # 软链重型只读依赖缓存（避免每题 npm install），仅在目标不存在且宿主存在时创建
    for cache_name in link_caches:
        host_cache = repo_root / cache_name
        target_cache = dest / cache_name
        if host_cache.is_dir() and not target_cache.exists():
            try:
                os.symlink(host_cache, target_cache)
            except OSError:
                pass
    return dest


def score_trajectory(events: list[dict[str, Any]], terminal: str) -> tuple[float, dict[str, Any]]:
    """Layer 2：从事件流 100% 确定性计算工具调用与执行纪律健康分（0-100）。"""
    total_calls = 0
    tool_errors = 0
    read_files: set[str] = set()
    edit_total = 0
    edit_after_read = 0
    last_write_seq = -1
    verified_after_write = False
    hook_rejects = 0
    seen_failed_sigs: set[str] = set()
    duplicate_failed_calls = 0
    calls_by_id: dict[str, dict[str, Any]] = {}

    for seq, ev in enumerate(events):
        kind = ev.get("type")
        if kind == "tool_use":
            total_calls += 1
            tid = str(ev.get("tool_id", seq))
            tool = ev.get("tool", "")
            fpath = str(ev.get("file_path") or "")
            cmd = str(ev.get("command") or "")
            calls_by_id[tid] = {"tool": tool, "file_path": fpath, "command": cmd, "seq": seq}
            if tool == "EditFile":
                edit_total += 1
                if not fpath or fpath in read_files:
                    edit_after_read += 1
        elif kind == "tool_result":
            tid = str(ev.get("tool_id", ""))
            info = calls_by_id.get(tid, {})
            tool = info.get("tool", "")
            fpath = info.get("file_path", "")
            cmd = info.get("command", "")
            is_err = bool(ev.get("is_error"))
            if is_err:
                tool_errors += 1
                sig = f"{tool}:{fpath}:{cmd[:80]}"
                if sig in seen_failed_sigs:
                    duplicate_failed_calls += 1
                seen_failed_sigs.add(sig)
                if "Hook" in str(ev.get("output_preview", "")) or "禁止" in str(ev.get("output_preview", "")):
                    hook_rejects += 1
            else:
                if tool == "ReadFile" and fpath:
                    read_files.add(fpath)
                elif tool in ("EditFile", "WriteFile"):
                    last_write_seq = info.get("seq", seq)
                elif tool == "Bash" and last_write_seq >= 0 and info.get("seq", seq) > last_write_seq:
                    if _VERIFY_CMD_RE.search(cmd):
                        verified_after_write = True

    tool_error_rate = tool_errors / max(1, total_calls)
    tool_error_score = max(0.0, 100.0 * (1.0 - tool_error_rate))

    read_before_edit_score = (
        100.0 if edit_total == 0 else 100.0 * (edit_after_read / edit_total)
    )
    self_verify_score = 100.0 if (last_write_seq < 0 or verified_after_write) else 25.0

    efficiency_score = 100.0 if terminal == "completed" else 30.0
    efficiency_score = max(0.0, efficiency_score - duplicate_failed_calls * 15.0)

    total = round(
        0.35 * tool_error_score
        + 0.25 * read_before_edit_score
        + 0.25 * self_verify_score
        + 0.15 * efficiency_score,
        2,
    )
    metrics = {
        "total_tool_calls": total_calls,
        "tool_errors": tool_errors,
        "tool_error_rate": round(tool_error_rate, 3),
        "edit_total": edit_total,
        "blind_edits": edit_total - edit_after_read,
        "verified_after_write": verified_after_write,
        "duplicate_failed_calls": duplicate_failed_calls,
        "hook_rejects": hook_rejects,
        "subscores": {
            "tool_error": round(tool_error_score, 2),
            "read_before_edit": round(read_before_edit_score, 2),
            "self_verify": round(self_verify_score, 2),
            "efficiency": round(efficiency_score, 2),
        },
    }
    return total, metrics


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _run_single_check(workspace: Path, check: dict[str, Any]) -> dict[str, Any]:
    cid = str(check.get("id", "check"))
    ctype = str(check.get("type", "command"))
    weight = float(check.get("weight", 1.0))
    desc = str(check.get("description", ""))

    if ctype == "file_assert":
        rel = str(check.get("path", ""))
        target = (workspace / rel).resolve()
        if not target.is_relative_to(workspace.resolve()):
            return {"id": cid, "type": ctype, "weight": weight, "description": desc, "passed": False, "reason": "路径越界"}
        exists_expected = bool(check.get("exists", True))
        if not target.is_file():
            passed = not exists_expected
            return {
                "id": cid,
                "type": ctype,
                "weight": weight,
                "description": desc,
                "passed": passed,
                "reason": "" if passed else f"文件不存在：{rel}",
            }
        content = target.read_text(encoding="utf-8", errors="ignore")
        for pat in check.get("must_contain_regex", []) or []:
            if not re.search(pat, content, re.MULTILINE | re.DOTALL):
                return {
                    "id": cid,
                    "type": ctype,
                    "weight": weight,
                    "description": desc,
                    "passed": False,
                    "reason": f"文件 {rel} 未匹配必需模式：{pat}",
                }
        for pat in check.get("must_not_contain_regex", []) or []:
            if re.search(pat, content, re.MULTILINE | re.DOTALL):
                return {
                    "id": cid,
                    "type": ctype,
                    "weight": weight,
                    "description": desc,
                    "passed": False,
                    "reason": f"文件 {rel} 命中禁止模式：{pat}",
                }
        return {"id": cid, "type": ctype, "weight": weight, "description": desc, "passed": True, "reason": ""}

    if ctype == "command":
        cmd = str(check.get("command", "")).strip()
        timeout = int(check.get("timeout", 60))
        if not cmd:
            return {"id": cid, "type": ctype, "weight": weight, "description": desc, "passed": False, "reason": "空命令"}
        try:
            proc = subprocess.run(
                ["bash", "-lc", cmd],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return {"id": cid, "type": ctype, "weight": weight, "description": desc, "passed": False, "reason": f"命令超时（>{timeout}s）"}
        combined = (proc.stdout + "\n" + proc.stderr).strip()
        expected_code = int(check.get("expected_exit_code", 0))
        if proc.returncode != expected_code:
            return {
                "id": cid,
                "type": ctype,
                "weight": weight,
                "description": desc,
                "passed": False,
                "reason": f"退出码 {proc.returncode} != {expected_code}：{combined[-600:]}",
            }
        for pat in check.get("stdout_regex", []) or []:
            if not re.search(pat, proc.stdout, re.MULTILINE | re.DOTALL):
                return {
                    "id": cid,
                    "type": ctype,
                    "weight": weight,
                    "description": desc,
                    "passed": False,
                    "reason": f"stdout 未匹配：{pat}",
                }
        return {"id": cid, "type": ctype, "weight": weight, "description": desc, "passed": True, "reason": ""}

    if ctype == "playwright":
        # 启动服务并执行确定性 Playwright Node 校验脚本（检查 DOM 布局/几何/无报错）
        node_bin = shutil.which("node")
        if not node_bin:
            return {"id": cid, "type": ctype, "weight": weight, "description": desc, "passed": False, "reason": "本机未安装 node，无法执行 playwright 校验"}
        start_cmd = str(check.get("start_command", "")).strip()
        url_path = str(check.get("url_path", "/"))
        script_body = str(check.get("script", "")).strip()
        timeout = int(check.get("timeout", 45))
        port = _free_port()
        server_proc = None
        try:
            if start_cmd:
                resolved_cmd = start_cmd.replace("{port}", str(port))
                server_proc = subprocess.Popen(
                    ["bash", "-lc", resolved_cmd],
                    cwd=workspace,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                deadline = time.monotonic() + int(check.get("ready_timeout", 15))
                ready = False
                while time.monotonic() < deadline:
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                            ready = True
                            break
                    except OSError:
                        time.sleep(0.25)
                if not ready:
                    return {"id": cid, "type": ctype, "weight": weight, "description": desc, "passed": False, "reason": f"前端预览服务在端口 {port} 启动超时"}
            target_url = f"http://127.0.0.1:{port}{url_path}" if start_cmd else f"file://{workspace / url_path.lstrip('/')}"
            runner_js = (
                "const assert = require('assert');\n"
                "(async () => {\n"
                "  const { chromium } = require('playwright');\n"
                "  const browser = await chromium.launch();\n"
                "  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });\n"
                "  const errors = [];\n"
                "  page.on('pageerror', e => errors.push(String(e)));\n"
                "  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });\n"
                f"  await page.goto({json.dumps(target_url)}, {{ waitUntil: 'networkidle' }});\n"
                f"  {script_body}\n"
                "  assert.strictEqual(errors.length, 0, 'Console/Page errors: ' + errors.join('; '));\n"
                "  await browser.close();\n"
                "})().catch(e => { console.error(e && e.stack ? e.stack : String(e)); process.exit(1); });\n"
            )
            with tempfile.NamedTemporaryFile("w", suffix=".cjs", dir=workspace, delete=False) as tf:
                tf.write(runner_js)
                script_file = Path(tf.name)
            try:
                proc = subprocess.run(
                    [node_bin, str(script_file)],
                    cwd=workspace,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            finally:
                script_file.unlink(missing_ok=True)
            if proc.returncode != 0:
                err = (proc.stderr or proc.stdout).strip()[-600:]
                return {"id": cid, "type": ctype, "weight": weight, "description": desc, "passed": False, "reason": f"Playwright 断言失败：{err}"}
            return {"id": cid, "type": ctype, "weight": weight, "description": desc, "passed": True, "reason": ""}
        finally:
            if server_proc is not None:
                server_proc.terminate()
                try:
                    server_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server_proc.kill()

    return {"id": cid, "type": ctype, "weight": weight, "description": desc, "passed": False, "reason": f"未知校验类型：{ctype}"}


def run_checks(workspace: Path, checks: list[dict[str, Any]]) -> tuple[float, list[dict[str, Any]]]:
    """Layer 1：执行全部声明式硬断言并返回加权百分制分数与明细。"""
    if not checks:
        return 100.0, []
    details = [_run_single_check(workspace, c) for c in checks]
    total_weight = sum(max(0.0, d["weight"]) for d in details)
    if total_weight <= 0:
        return 100.0, details
    passed_weight = sum(max(0.0, d["weight"]) for d in details if d["passed"])
    return round(100.0 * passed_weight / total_weight, 2), details


RUBRIC_JUDGE_SYSTEM = """你是严格、客观的二元核查裁判（Binary Rubric Judge）。
请仅根据提供的任务说明、改动后的代码摘要与执行检查结果，对每条核查项判定通过（true）或不通过（false）。
禁止模糊给分，只返回严格 JSON（无 Markdown 围栏）：
{
  "items": [
    {"id": "r1", "passed": true, "evidence": "基于实际文件或输出的简短事实依据"}
  ]
}"""


def _collect_workspace_summary(workspace: Path, max_bytes: int = 24_000) -> str:
    """收集工作区关键文本文件摘要供 Rubric Judge 审阅。"""
    chunks: list[str] = []
    size = 0
    skip_dirs = {".git", "node_modules", ".venv", "__pycache__", "dist", "build", ".next"}
    for path in sorted(workspace.rglob("*")):
        if not path.is_file() or any(part in skip_dirs for part in path.parts):
            continue
        if path.suffix.lower() not in (
            ".py", ".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".json", ".yaml", ".yml", ".md"
        ):
            continue
        rel = path.relative_to(workspace).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")[:3000]
        except OSError:
            continue
        piece = f"--- {rel} ---\n{text}\n"
        if size + len(piece) > max_bytes:
            break
        chunks.append(piece)
        size += len(piece)
    return "\n".join(chunks)


async def evaluate_rubric(
    workspace: Path,
    case: dict[str, Any],
    checks_detail: list[dict[str, Any]],
    *,
    judge_provider: Any | None = None,
    rubric_judge: Callable[[Path, dict[str, Any], list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
) -> tuple[float | None, list[dict[str, Any]]]:
    """Layer 3：执行冻结的 0/1 二元核查清单。"""
    rubric = case.get("rubric") or []
    if not rubric:
        return None, []
    if rubric_judge is not None:
        raw_items = rubric_judge(workspace, case, checks_detail)
    elif judge_provider is not None:
        payload = {
            "prompt": case["prompt"],
            "rubric": rubric,
            "checks_detail": checks_detail,
            "workspace_files": _collect_workspace_summary(workspace),
        }
        conv = ConversationManager()
        conv.add_user_message(json.dumps(payload, ensure_ascii=False))
        text = ""
        last_exc: Exception | None = None
        for attempt in range(3):
            chunks: list[str] = []
            try:
                async with asyncio.timeout(60):
                    async for ev in create_client(judge_provider).stream(
                        conv, system=RUBRIC_JUDGE_SYSTEM, tools=[]
                    ):
                        if isinstance(ev, TextDelta):
                            chunks.append(ev.text)
                text = "".join(chunks).strip()
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))
        if last_exc is not None:
            raise last_exc
        from .optimizer import extract_json_payload

        parsed = extract_json_payload(text)
        raw_items = parsed.get("items", []) if isinstance(parsed, dict) else []
    else:
        return None, []

    by_id = {str(item.get("id")): item for item in raw_items if isinstance(item, dict)}
    results: list[dict[str, Any]] = []
    total_weight = 0.0
    passed_weight = 0.0
    for r in rubric:
        rid = str(r["id"])
        weight = float(r.get("weight", 1.0))
        judged = by_id.get(rid, {})
        passed = bool(judged.get("passed", False))
        total_weight += max(0.0, weight)
        if passed:
            passed_weight += max(0.0, weight)
        results.append(
            {
                "id": rid,
                "criterion": r["criterion"],
                "weight": weight,
                "passed": passed,
                "evidence": str(judged.get("evidence", "")),
            }
        )
    score = round(100.0 * passed_weight / total_weight, 2) if total_weight > 0 else 100.0
    return score, results


def combine_scores(
    checks_score: float,
    trajectory_score: float,
    rubric_score: float | None,
    weights: dict[str, float],
) -> float:
    """按配置权重合成三层得分；当无 rubric 得分时自动将其权重按比例重归一至硬断言与轨迹分。"""
    w_checks = max(0.0, float(weights.get("checks", 0.6)))
    w_traj = max(0.0, float(weights.get("trajectory", 0.2)))
    w_rubric = max(0.0, float(weights.get("rubric", 0.2))) if rubric_score is not None else 0.0
    denom = w_checks + w_traj + w_rubric
    if denom <= 0:
        return round(checks_score, 2)
    total = (
        w_checks * checks_score
        + w_traj * trajectory_score
        + (w_rubric * rubric_score if rubric_score is not None else 0.0)
    ) / denom
    return round(total, 2)


_CWD_LOCK = asyncio.Lock()


class RepoSuiteEvaluator:
    """在隔离工作树中运行指定版本 Harness 并产出三层客观评测回执。"""

    def __init__(
        self,
        store: RepoRSIStore,
        *,
        provider: Any | None = None,
        judge_provider: Any | None = None,
        client_factory: Callable[[Any, RepoHarnessInputs, Path], Any] | None = None,
        rubric_judge: Callable[[Path, dict[str, Any], list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
        max_turns: int = 20,
        timeout_seconds: int = 180,
    ) -> None:
        self.store = store
        self.provider = provider
        self.judge_provider = judge_provider
        self.client_factory = client_factory
        self.rubric_judge = rubric_judge
        self.max_turns = max_turns
        self.timeout_seconds = timeout_seconds

    def _build_registry(
        self, harness: RepoHarnessInputs
    ) -> tuple[ToolRegistry, LoadSkill | None]:
        registry = ToolRegistry()
        load_skill: LoadSkill | None = LoadSkill() if harness.skills else None
        tools: list[Any] = [ReadFile(), EditFile(), WriteFile(), Glob(), Grep(), Bash()]
        if load_skill is not None:
            tools.append(load_skill)
        for tool in tools:
            overlay = harness.description_overlays.get(tool.name)
            registry.register(GuidedTool(tool, overlay) if overlay else tool)
        return registry, load_skill

    async def evaluate_case(
        self,
        case: dict[str, Any],
        harness: RepoHarnessInputs,
        *,
        version: int,
        run_id: str,
        run_index: int = 1,
    ) -> CaseEvalResult:
        meta = self.store.meta()
        repo_root = Path(meta["repo_root"])
        cell_dir = self.store.runs_dir / run_id / f"v{version}-{case['case_id']}-r{run_index}"
        if cell_dir.exists():
            shutil.rmtree(cell_dir)
        workspace = cell_dir / "workspace"
        prepare_isolated_workspace(
            repo_root,
            case["base_commit"],
            workspace,
            base_patch=case.get("base_patch", ""),
        )

        started = time.monotonic()
        events: list[dict[str, Any]] = []
        assistant_chunks: list[str] = []
        terminal = "completed"
        turns = 0

        registry, load_skill = self._build_registry(harness)
        hook_engine = (
            HookEngine(build_guidance_hooks(harness.tool_hooks))
            if harness.tool_hooks
            else None
        )
        checker = PermissionChecker(
            DangerousCommandDetector(),
            PathSandbox(str(workspace)),
            RuleEngine(),
            PermissionMode.ACCEPT_EDITS,
            sandbox_enabled=True,
        )
        # 在隔离工作树内自动放行常规读/写/校验命令
        checker.check = lambda tool, args: _isolated_allow(workspace, tool, args)

        if self.client_factory is not None:
            client = self.client_factory(self.provider, harness, workspace)
            protocol = getattr(self.provider, "protocol", "anthropic") if self.provider else "anthropic"
            context_window = (
                self.provider.get_context_window()
                if self.provider and hasattr(self.provider, "get_context_window")
                else 128_000
            )
        else:
            if self.provider is None:
                raise ValueError("评测需要提供 provider 或 client_factory")
            client = create_client(self.provider)
            protocol = self.provider.protocol
            context_window = self.provider.get_context_window()

        agent = Agent(
            client=client,
            registry=registry,
            protocol=protocol,
            work_dir=str(workspace),
            max_iterations=self.max_turns,
            permission_checker=checker,
            hook_engine=hook_engine,
            context_window=context_window,
            instructions_content=harness.instructions,
        )
        if harness.skills and load_skill is not None:
            loader = FrozenSkillLoader(harness.skills)
            load_skill.set_loader(loader)
            load_skill.set_agent(agent)
            agent.set_skill_catalog(
                "\n".join(f"- {name}: {desc}" for name, desc in loader.get_catalog())
            )

        conv = ConversationManager()
        conv.add_user_message(case["prompt"])
        async with _CWD_LOCK:
            prev_cwd = os.getcwd()
            os.chdir(workspace)
            try:
                for attempt in range(3):
                    try:
                        async with asyncio.timeout(self.timeout_seconds):
                            async for ev in agent.run(conv):
                                if isinstance(ev, TextDelta):
                                    assistant_chunks.append(ev.text)
                                elif isinstance(ev, ToolUseEvent):
                                    args = ev.arguments or {}
                                    events.append(
                                        {
                                            "type": "tool_use",
                                            "tool_id": ev.tool_id,
                                            "tool": ev.tool_name,
                                            "file_path": str(args.get("file_path") or args.get("path") or ""),
                                            "command": str(args.get("command") or ""),
                                        }
                                    )
                                elif isinstance(ev, ToolResultEvent):
                                    events.append(
                                        {
                                            "type": "tool_result",
                                            "tool_id": ev.tool_id,
                                            "is_error": ev.is_error,
                                            "elapsed_ms": round(ev.elapsed * 1000),
                                            "output_preview": str(ev.output)[:240],
                                        }
                                    )
                                elif isinstance(ev, UsageEvent):
                                    events.append(
                                        {
                                            "type": "usage",
                                            "input_tokens": ev.input_tokens,
                                            "output_tokens": ev.output_tokens,
                                        }
                                    )
                                elif isinstance(ev, HookEvent):
                                    events.append(
                                        {"type": "hook", "hook_id": ev.hook_id, "output": ev.output[:200]}
                                    )
                                elif isinstance(ev, TurnComplete):
                                    turns = ev.turn
                                    events.append({"type": "turn_complete", "turn": ev.turn})
                                elif isinstance(ev, LoopComplete):
                                    turns = ev.total_turns
                                    events.append({"type": "loop_complete", "turn": ev.total_turns})
                                    if ev.total_turns >= self.max_turns:
                                        terminal = "turn_limit"
                                elif isinstance(ev, ErrorEvent):
                                    terminal = "error"
                                    events.append({"type": "error", "message": ev.message[:300]})
                        break
                    except TimeoutError:
                        terminal = "timed_out"
                        events.append({"type": "terminal", "status": "timed_out"})
                        break
                    except Exception as exc:
                        msg = str(exc).lower()
                        transient = any(
                            k in msg
                            for k in (
                                "peer closed",
                                "incomplete chunked",
                                "connection",
                                "reset by peer",
                                "timeout",
                                "502",
                                "503",
                                "429",
                            )
                        )
                        if not transient or attempt >= 2:
                            raise
                        # 瞬态流中断：清空并重建干净工作树与对话后重试
                        events.clear()
                        assistant_chunks.clear()
                        terminal = "completed"
                        turns = 0
                        os.chdir(prev_cwd)
                        shutil.rmtree(workspace, ignore_errors=True)
                        prepare_isolated_workspace(
                            repo_root,
                            case["base_commit"],
                            workspace,
                            base_patch=case.get("base_patch", ""),
                        )
                        os.chdir(workspace)
                        conv = ConversationManager()
                        conv.add_user_message(case["prompt"])
                        await asyncio.sleep(2.0 * (attempt + 1))
            finally:
                os.chdir(prev_cwd)

        events.append({"type": "terminal", "status": terminal})
        trace_file = cell_dir / "events.jsonl"
        trace_file.write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events),
            encoding="utf-8",
        )

        checks_score, checks_detail = await asyncio.to_thread(
            run_checks, workspace, case.get("checks") or []
        )
        trajectory_score, trajectory_detail = score_trajectory(events, terminal)
        rubric_score, rubric_detail = await evaluate_rubric(
            workspace,
            case,
            checks_detail,
            judge_provider=self.judge_provider,
            rubric_judge=self.rubric_judge,
        )
        total_score = combine_scores(
            checks_score, trajectory_score, rubric_score, case.get("weights") or {}
        )
        duration_ms = round((time.monotonic() - started) * 1000)

        self.store.save_receipt(
            run_id=run_id,
            version=version,
            case_id=case["case_id"],
            run_index=run_index,
            score=total_score,
            checks_score=checks_score,
            trajectory_score=trajectory_score,
            rubric_score=rubric_score,
            details={
                "checks": checks_detail,
                "trajectory": trajectory_detail,
                "rubric": rubric_detail,
                "terminal": terminal,
                "turns": turns,
                "duration_ms": duration_ms,
            },
            trace_path=str(trace_file),
        )
        return CaseEvalResult(
            case_id=case["case_id"],
            run_index=run_index,
            version=version,
            score=total_score,
            checks_score=checks_score,
            trajectory_score=trajectory_score,
            rubric_score=rubric_score,
            terminal=terminal,
            duration_ms=duration_ms,
            turns=turns,
            checks_detail=checks_detail,
            trajectory_detail=trajectory_detail,
            rubric_detail=rubric_detail,
            trace_path=str(trace_file),
            assistant_output="".join(assistant_chunks),
        )

    async def calibrate_case(
        self,
        case_id: str,
        *,
        max_baseline_score: float = 85.0,
    ) -> dict[str, Any]:
        """借鉴 Penguin benchmark-design：用当前活动 Harness 跑一次校准，确认能复现痛点后冻结用例。"""
        case = self.store.get_case(case_id)
        head = self.store.head()
        harness = self.store.snapshots.load(head.digest)
        result = await self.evaluate_case(
            case,
            harness,
            version=head.version,
            run_id=f"calibrate-{case_id}",
            run_index=1,
        )
        if result.score >= max_baseline_score:
            self.store.update_case_status(case_id, "draft", baseline_score=result.score)
            return {
                "case_id": case_id,
                "calibrated": False,
                "reason": "calibration_too_easy",
                "baseline_score": result.score,
                "threshold": max_baseline_score,
                "observation": result.to_public_observation(),
            }
        frozen = self.store.update_case_status(
            case_id, "frozen", baseline_score=result.score
        )
        return {
            "case_id": case_id,
            "calibrated": True,
            "status": frozen["status"],
            "baseline_score": result.score,
            "threshold": max_baseline_score,
            "observation": result.to_public_observation(),
        }


def _isolated_allow(workspace: Path, tool: Any, args: dict[str, Any]):
    from mtcode.permissions import Decision

    # 严格限制文件类工具在隔离工作树内，阻断危险删除命令
    fpath = args.get("file_path") or args.get("path")
    if isinstance(fpath, str) and fpath:
        resolved = (workspace / fpath).resolve() if not os.path.isabs(fpath) else Path(fpath).resolve()
        if not resolved.is_relative_to(workspace.resolve()):
            return Decision("deny", f"隔离评测禁止越界访问：{fpath}")
    if tool.name == "Bash":
        cmd = str(args.get("command") or "")
        if re.search(r"\brm\s+-rf\s+/|\bsudo\b|\bcurl\b|\bwget\b", cmd):
            return Decision("deny", "隔离评测禁止危险或联网命令")
    return Decision("allow", "隔离评测工作树放行")
