"""仓库回归用例合成器（F23 落地 + 借鉴 Penguin OpenSkill 独立虚拟验证器 + 基线校准）。

将真实的交互纠偏/失败信号转化为：
- 固定起始代码状态（base_commit + 可选 base_patch）
- 不含事后剧透的原始任务提示（测进化后的 Harness 能否第一轮做对）
- 三层客观评测包：
  1. checks（command / file_assert / playwright DOM 几何与交互硬断言）
  2. trajectory（自动从 events.jsonl 客观计算的工具健康分）
  3. rubric（可选 3-6 条明确 0/1 二元核查项）
并通过旧版 Harness 基线校准，确认旧版确实暴露不足后才冻结入库。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
import subprocess
from typing import Any

from mewcode.client import create_client
from mewcode.conversation import ConversationManager
from mewcode.tools.base import TextDelta
from .store import RepoRSIStore


def git_head_commit(repo_root: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if proc.returncode != 0:
        raise ValueError(f"无法读取仓库 Git HEAD：{proc.stderr.strip()}")
    return proc.stdout.strip()


def git_uncommitted_patch(repo_root: Path) -> str:
    proc = subprocess.run(
        ["git", "diff", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return proc.stdout if proc.returncode == 0 else ""


def _slugify(text: str, prefix: str = "case") -> str:
    ascii_part = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:32]
    if ascii_part and ascii_part[0].isalpha():
        return ascii_part
    return f"{prefix}-{abs(hash(text)) % 100000:05d}"


VERIFIER_SYNTHESIS_SYSTEM = """你是独立的仓库回归评测用例设计器（Independent Verifier，借鉴 OpenSkill）。
给定一次真实开发中的「原始用户需求」「仓库背景」「用户在交付后提出的修改/纠偏意见」，请将其转化为一条客观可复现的 Repo-Eval Case。

核心原则：
1. `prompt` 只还原用户当时的原始需求与必要规格，**不要**把“上次你做错了什么”或内部评测细节写进 prompt，因为我们要检验进化后的 Harness 能否一次做对。
2. `checks` 尽量用确定性硬断言捕获用户痛点，支持三种类型：
   - {"id": "...", "type": "command", "command": "npm run build", "weight": 2.0, "description": "..."}
   - {"id": "...", "type": "file_assert", "path": "src/App.tsx", "must_contain_regex": [...], "must_not_contain_regex": [...], "weight": 1.0, "description": "..."}
   - {"id": "...", "type": "playwright", "start_command": "npm run preview -- --port {port}", "url_path": "/", "ready_timeout": 15, "script": "<Node Playwright 校验函数体，可使用 page, expect, assert>", "weight": 3.0, "description": "..."}
3. `rubric` 只放确实难以纯脚本表达的设计/体验项（0–5 条），每条必须能用严格的 Yes/No（通过/不通过）判定：
   - {"id": "r1", "criterion": "明确的二元核查陈述", "weight": 1.0}

返回严格 JSON 对象（禁止 Markdown 代码围栏）：
{
  "case_id": "lowercase-kebab-id",
  "title": "简短标题",
  "prompt": "给被测 Coding Agent 的任务提示词",
  "checks": [...],
  "rubric": [...]
}"""


def _default_repo_checks(repo_root: Path) -> list[dict[str, Any]]:
    """根据仓库技术栈自动补充基础确定性构建/测试硬检查。"""
    checks: list[dict[str, Any]] = []
    pkg_json = repo_root / "package.json"
    if pkg_json.is_file():
        try:
            pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
            scripts = pkg.get("scripts") or {}
            if "typecheck" in scripts:
                checks.append(
                    {
                        "id": "npm-typecheck",
                        "type": "command",
                        "command": "npm run typecheck",
                        "weight": 1.5,
                        "description": "TypeScript 类型检查通过",
                    }
                )
            if "build" in scripts:
                checks.append(
                    {
                        "id": "npm-build",
                        "type": "command",
                        "command": "npm run build",
                        "weight": 2.0,
                        "description": "前端生产构建通过",
                    }
                )
            elif "test" in scripts:
                checks.append(
                    {
                        "id": "npm-test",
                        "type": "command",
                        "command": "npm test",
                        "weight": 2.0,
                        "description": "前端单元测试通过",
                    }
                )
        except (OSError, ValueError):
            pass
    elif (repo_root / "pyproject.toml").is_file() or (repo_root / "pytest.ini").is_file():
        checks.append(
            {
                "id": "python-tests",
                "type": "command",
                "command": "python3 -m pytest -q",
                "weight": 2.0,
                "description": "Python 测试套件通过",
            }
        )
    return checks


async def synthesize_case_from_friction(
    store: RepoRSIStore,
    friction_id: str,
    *,
    case_id: str | None = None,
    provider: Any | None = None,
    extra_checks: list[dict[str, Any]] | None = None,
    extra_rubric: list[dict[str, Any]] | None = None,
    base_commit: str | None = None,
    base_patch: str = "",
    auto_approve: bool = False,
) -> dict[str, Any]:
    """从一条已记录的 FrictionEvent 合成 Repo-Eval Case（支持无 LLM 规则模式与 LLM 独立验证器模式）。"""
    meta = store.meta()
    repo_root = Path(meta["repo_root"])
    friction = store.get_friction(friction_id)
    ev = friction.get("evidence") or {}
    orig_prompt = (ev.get("original_prompt") or friction["summary"]).strip()
    user_feedback = (ev.get("user_feedback") or friction["summary"]).strip()
    commit = base_commit or friction.get("base_commit") or git_head_commit(repo_root)

    checks: list[dict[str, Any]] = []
    rubric: list[dict[str, Any]] = []
    title = friction["summary"][:64]
    prompt = orig_prompt
    generated_id = case_id

    if provider is not None:
        req = {
            "repo_name": meta["name"],
            "friction_kind": friction["kind"],
            "category": friction["category"],
            "original_prompt": orig_prompt,
            "user_feedback": user_feedback,
            "previous_modified_files": ev.get("previous_modified_files", []),
            "suggested_checks": ev.get("suggested_checks", []),
            "suggested_rubric": ev.get("suggested_rubric", []),
        }
        conv = ConversationManager()
        conv.add_user_message(json.dumps(req, ensure_ascii=False, indent=2))
        chunks: list[str] = []
        async with asyncio.timeout(60):
            async for event in create_client(provider).stream(
                conv, system=VERIFIER_SYNTHESIS_SYSTEM, tools=[]
            ):
                if isinstance(event, TextDelta):
                    chunks.append(event.text)
        raw = "".join(chunks).strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z]*\n|\n```$", "", raw).strip()
        parsed = json.loads(raw)
        if not generated_id and parsed.get("case_id"):
            generated_id = str(parsed["case_id"])
        title = str(parsed.get("title") or title)
        prompt = str(parsed.get("prompt") or prompt)
        checks = list(parsed.get("checks") or [])
        rubric = list(parsed.get("rubric") or [])
    else:
        checks = _default_repo_checks(repo_root)
        # 若 friction 里带有建议的 rubric，转为标准二元条文
        for i, item in enumerate(ev.get("suggested_rubric", []) or [], 1):
            if isinstance(item, str) and item.strip():
                rubric.append({"id": f"r{i}", "criterion": item.strip(), "weight": 1.0})
            elif isinstance(item, dict) and item.get("criterion"):
                rubric.append(
                    {
                        "id": str(item.get("id") or f"r{i}"),
                        "criterion": str(item["criterion"]).strip(),
                        "weight": float(item.get("weight", 1.0)),
                    }
                )
        if not rubric and friction["kind"] == "user_correction":
            rubric.append(
                {
                    "id": "r1",
                    "criterion": f"交付产物直接满足用户纠偏要求：{user_feedback[:240]}",
                    "weight": 1.0,
                }
            )

    if extra_checks:
        checks.extend(extra_checks)
    if extra_rubric:
        rubric.extend(extra_rubric)

    final_id = generated_id or _slugify(title, prefix="repo-case")
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,63}", final_id):
        final_id = _slugify(final_id, prefix="repo-case")

    saved = store.save_case(
        case_id=final_id,
        title=title,
        base_commit=commit,
        base_patch=base_patch,
        prompt=prompt,
        checks=checks,
        rubric=rubric,
        status="calibrated" if auto_approve else "draft",
        origin_friction_id=friction_id,
    )
    if friction.get("issue_id"):
        store.upsert_eit_issue(
            category=friction["category"],
            title=title,
            description=friction["summary"],
            issue_id=friction["issue_id"],
            linked_case=final_id,
        )
    return saved


def approve_case(
    store: RepoRSIStore,
    case_id: str,
    *,
    checks: list[dict[str, Any]] | None = None,
    rubric: list[dict[str, Any]] | None = None,
    prompt: str | None = None,
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """用户 10 秒快速勾选/微调 Rubric 与断言后确认用例，等待或直接进入基线校准。"""
    current = store.get_case(case_id)
    return store.save_case(
        case_id=case_id,
        title=current["title"],
        base_commit=current["base_commit"],
        base_patch=current["base_patch"],
        prompt=prompt if prompt is not None else current["prompt"],
        checks=checks if checks is not None else current["checks"],
        rubric=rubric if rubric is not None else current["rubric"],
        weights=weights if weights is not None else current["weights"],
        status="calibrated",
        baseline_score=current["baseline_score"],
        origin_friction_id=current["origin_friction_id"],
    )
