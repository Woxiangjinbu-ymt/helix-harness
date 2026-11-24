"""单次被测运行的受信入口。启动独立进程消费原生 Agent.run()。"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Protocol, TypedDict
import uuid

from mewcode.agent import ErrorEvent, LoopComplete, PermissionReply, PermissionRequest, PermissionResponse, ToolUseEvent
from mewcode.client import create_client
from mewcode.config import ProviderConfig, load_config
from mewcode.conversation import ConversationManager
from mewcode.permissions import DangerousCommandDetector, PathSandbox, PermissionMode, RuleEngine
from mewcode.tools import create_default_registry
from mewcode.tools.load_skill import LoadSkill

from .contracts import EvalCell, FrozenContext, digest
from .fixtures import BENCHMARK, file_digest, tree_digest, verify_lock
from .strategy import FrozenSkillLoader, assemble_strategy
from .trajectory import RecordingClient, TrajectoryCollector
from .workspace import CellAgent, PUBLIC_TEST, PublicTestTool, WorkspacePermissions, prepare_task, remove_container, seal_artifact


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class RunSpec:
    evaluation_id: str
    case_id: str
    run_index: int = 1
    version: int = 1
    max_turns: int = 12
    timeout_seconds: int = 90
    strategy_dir: Path | None = None
    strategy_digest: str | None = None
    expected_fingerprint: str | None = None


class RunOutcome(TypedDict):
    cell_id: str
    session_id: str
    status: str
    run_fingerprint: str
    artifact_digest: str | None
    trace_ref: str | None
    trace_digest: str | None


class CodingRuntime(Protocol):
    async def run(self, spec: RunSpec, provider: ProviderConfig) -> RunOutcome: ...


def read_outcome(run: Path, cell: EvalCell) -> RunOutcome:
    if not (run / "outcome.json").is_file():
        raise RuntimeError("cell 已登记且尚未封存，不能重复启动")
    outcome = json.loads((run / "outcome.json").read_text())
    identity = json.loads((run / "identity.json").read_text())
    if (outcome["cell_id"] != cell.cell_id or outcome["run_fingerprint"] != cell.run_fingerprint
            or outcome["session_id"] != identity["session_id"] or identity["cell"] != asdict(cell)):
        raise ValueError("cell 身份不匹配")
    if outcome["trace_digest"] and file_digest(run / "events.jsonl") != outcome["trace_digest"]:
        raise ValueError("轨迹封存摘要不匹配")
    if outcome["artifact_digest"] and tree_digest(run / "artifact") != outcome["artifact_digest"]:
        raise ValueError("产物封存摘要不匹配")
    return outcome


def record_interruption(run, cell, session, reason):
    if (run / "outcome.json").exists():
        return
    # 硬中断可能留下半行事件，保留原件供维护者诊断，不签发可评分的轨迹。
    result = dict(cell_id=cell.cell_id, session_id=session, run_fingerprint=cell.run_fingerprint,
                  status=reason, artifact_digest=None, trace_digest=None, trace_ref=None)
    temporary = run / "interrupted.tmp"
    temporary.write_text(json.dumps(result) + "\n")
    os.replace(temporary, run / "outcome.json")


def runtime_context(lock, policy, provider, max_turns, timeout_seconds) -> FrozenContext:
    adapter = {name: file_digest(Path(__file__).with_name(name)) for name in
               ("runtime.py", "strategy.py", "trajectory.py", "workspace.py")}
    return FrozenContext(
        lock["benchmark_digest"], lock["evaluator_digest"], lock["policy_digest"],
        digest({"mewcode": lock["runtime_digest"], "adapter": adapter}), policy["image"], policy["platform"],
        provider.name, provider.model,
        digest({"protocol": provider.protocol, "endpoint": provider.base_url, "thinking": provider.thinking,
                "context_window": provider.get_context_window(), "max_output": provider.get_max_output_tokens()}),
        digest({"max_turns": max_turns, "timeout_seconds": timeout_seconds}),
    )


class MewCodeRuntime:
    def __init__(self, runs_dir: Path):
        self.runs_dir = runs_dir.resolve()
        if self.runs_dir.is_relative_to(ROOT):
            raise ValueError("运行记录需位于 MewCode 源码目录之外")

    async def run(self, spec: RunSpec, provider: ProviderConfig) -> RunOutcome:
        if any(type(n) is not int or n < 1 for n in (spec.max_turns, spec.timeout_seconds)):
            raise ValueError("运行预算需要为正整数")
        lock = await asyncio.to_thread(verify_lock, ROOT)
        cases = {c["case_id"]: c for c in lock["benchmark"]["cases"]}
        if spec.case_id not in cases:
            raise ValueError("未知 case")
        policy = json.loads((ROOT / "evaluation/policy/docker.json").read_text())
        image = await asyncio.create_subprocess_exec(
            "docker", "image", "inspect", policy["image"], "--format", "{{.Os}}/{{.Architecture}} {{.Id}}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            info, _ = await asyncio.wait_for(image.communicate(), 15)
        except BaseException:
            if image.returncode is None:
                image.kill()
            await image.wait()
            raise
        platform, _, image_id = info.decode().strip().partition(" ")
        if image.returncode or platform != policy["platform"] or not image_id.startswith("sha256:"):
            raise ValueError("冻结的任务镜像不可用")
        if (spec.strategy_dir is None) != (spec.strategy_digest is None):
            raise ValueError("自定义策略必须同时指定目录和内容摘要")
        strategy_dir = spec.strategy_dir or ROOT / "evaluation/strategies/default-v1"
        strategy_id = spec.strategy_digest or lock["initial_strategy_digest"]
        strategy = assemble_strategy(strategy_dir, strategy_id)
        context = runtime_context(lock, policy, provider, spec.max_turns, spec.timeout_seconds)
        cell = EvalCell(spec.evaluation_id, spec.case_id, spec.run_index, spec.version,
                        context.run_fingerprint(strategy_id))
        if spec.expected_fingerprint is not None and cell.run_fingerprint != spec.expected_fingerprint:
            raise ValueError("运行条件已偏离预期指纹")
        run = self.runs_dir / cell.cell_id
        self.runs_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            run.mkdir(mode=0o700)
        except FileExistsError:
            return read_outcome(run, cell)
        session = uuid.uuid4().hex
        case = cases[spec.case_id]
        meta = {"cell": asdict(cell), "cell_id": cell.cell_id, "session_id": session,
                "repo_commit": case["repo_commit"], "strategy_digest": strategy_id,
                "context": asdict(context), "image_id": image_id, "instructions_digest": digest(strategy.instructions)}
        (run / "identity.json").write_text(json.dumps(meta, indent=2) + "\n")
        # 凭据只经匿名管道传给受信 Python 进程，不出现在 argv、轨迹或任务容器内。
        config = {k: v for k, v in asdict(provider).items() if not k.startswith("_")}
        config.update(api_key=provider.resolve_api_key(), context_window=provider.get_context_window())
        payload = {"meta": meta, "case": case, "run": str(run), "provider": config,
                   "strategy": asdict(strategy), "policy": policy, "turns": spec.max_turns,
                   "timeout": spec.timeout_seconds}
        with tempfile.TemporaryDirectory(prefix="mewcode-cell-") as scratch:
            payload["scratch"] = str(Path(scratch).resolve())
            await self._launch(run, cell, session, payload)
        return read_outcome(run, cell)

    async def _launch(self, run, cell, session, payload):
        entry = "import sys; sys.path.insert(0,sys.argv[1]); from mewcode.rsi.runtime import worker; worker()"
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-B", "-c", entry, str(ROOT), cwd=run,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(proc.communicate(json.dumps(payload).encode()), payload["timeout"] + 25)
        except (asyncio.CancelledError, TimeoutError) as exc:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            record_interruption(run, cell, session, "cancelled" if isinstance(exc, asyncio.CancelledError) else "worker_lost")
            await remove_container("mewcode-rsi-" + session)
            if isinstance(exc, asyncio.CancelledError):
                raise
        if not (run / "outcome.json").is_file():
            record_interruption(run, cell, session, "worker_lost")


async def run_worker(job, scratch: Path):
    meta = job["meta"]
    case = job["case"]
    run = Path(job["run"])
    task = scratch / "task"
    public = ROOT / "evaluation" / BENCHMARK / "public" / case["case_id"]
    trace = TrajectoryCollector(run, meta["session_id"], set())
    status = "infrastructure_error"
    error = None
    artifact = None
    deadline = None
    try:
        prepare_task(public, task, case["repo_commit"])
        statement = (public / "statement.md").read_bytes()
        if hashlib.sha256(statement).hexdigest() != case["statement_digest"]:
            raise ValueError("公开题面与已冻结版本不一致")
        trace.public_paths = {p.relative_to(task).as_posix() for p in task.rglob("*") if p.is_file()}
        # 只有此子进程切换 cwd。-I 隔离导入路径，宿主不会从任务目录导入 Python 模块。
        os.chdir(task)
        provider = ProviderConfig(**job["provider"])
        registry = create_default_registry()
        test_tool = PublicTestTool(task, job["policy"], "mewcode-rsi-" + meta["session_id"])
        registry.register(test_tool)
        checker = WorkspacePermissions(DangerousCommandDetector(), PathSandbox(str(task)),
                                       RuleEngine(), PermissionMode.ACCEPT_EDITS)
        agent = CellAgent(RecordingClient(create_client(provider), trace, provider.name), registry, provider.protocol,
                          work_dir=str(task), max_iterations=job["turns"], permission_checker=checker,
                          context_window=provider.get_context_window(), instructions_content=job["strategy"]["instructions"])
        agent.session_id = meta["session_id"]
        if job["strategy"]["skills"]:
            loader = FrozenSkillLoader(job["strategy"]["skills"])
            load_skill = LoadSkill()
            load_skill.set_loader(loader)
            load_skill.set_agent(agent)
            registry.register(load_skill)
            agent.set_skill_catalog("Available Skills (LoadSkill):\n" +
                                    "\n".join(f"- {n}: {d}" for n, d in loader.get_catalog()))
        conv = ConversationManager()
        conv.add_user_message(statement.decode())
        trace.record("started")
        async with asyncio.timeout(job["timeout"]) as deadline:
            async for event in agent.run(conv):
                if isinstance(event, PermissionRequest):
                    allowed = event.tool_name == "Bash" and event.description == PUBLIC_TEST
                    decision = PermissionResponse.ALLOW if allowed else PermissionResponse.DENY
                    event.future.set_result(PermissionReply(decision))
                    trace.observe(event, decision.value)
                elif isinstance(event, ToolUseEvent):
                    tool = registry.get(event.tool_name)
                    effect = checker.check(tool, event.arguments).effect if tool else "deny"
                    trace.observe(event, effect)
                else:
                    trace.observe(event)
                if test_tool.infrastructure_error:
                    raise RuntimeError("容器启动失败")
                if isinstance(event, LoopComplete):
                    if trace.pending:
                        raise ValueError("结束时仍有未配对工具事件")
                    status = "completed"
                if isinstance(event, ErrorEvent) and event.message == f"Agent reached maximum iterations ({job['turns']})":
                    status = "turn_limit"
    except TimeoutError:
        if deadline is not None and deadline.expired():
            status = "timed_out"
        else:
            status, error = "infrastructure_error", "TimeoutError"
    except Exception as exc:
        error = type(exc).__name__
        status = "infrastructure_error"
    finally:
        os.chdir(scratch)
    if status in {"completed", "turn_limit", "timed_out"}:
        try:
            artifact = seal_artifact(task, run)
        except (OSError, ValueError) as exc:
            status, error = "infrastructure_error", type(exc).__name__
    trace.seal(status, cell_id=meta["cell_id"], run_fingerprint=meta["cell"]["run_fingerprint"],
               artifact_digest=artifact, error_type=error)


def worker():
    job = json.load(sys.stdin)
    scratch = Path(job["scratch"])
    os.environ.setdefault("DOCKER_CONFIG", str(Path.home() / ".docker"))
    cell_home = scratch / "home"
    cell_home.mkdir(mode=0o700)
    os.environ.update(HOME=str(cell_home), XDG_CONFIG_HOME=str(cell_home / "config"),
                      XDG_CACHE_HOME=str(cell_home / "cache"))
    asyncio.run(run_worker(job, scratch))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_id")
    parser.add_argument("--evaluation", required=True)
    parser.add_argument("--run-index", type=int, default=1)
    parser.add_argument("--provider-config", type=Path, required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--runs-dir", type=Path, required=True)
    args = parser.parse_args()
    providers = [p for p in load_config(args.provider_config).providers if p.name == args.provider]
    if len(providers) != 1:
        parser.error("需要一个唯一匹配的 Provider")
    result = asyncio.run(MewCodeRuntime(args.runs_dir).run(
        RunSpec(args.evaluation, args.case_id, args.run_index), providers[0],
    ))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] in {"completed", "turn_limit", "timed_out"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
