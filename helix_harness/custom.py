"""Public-project adapter. Each invocation runs in a dedicated job process."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import uuid

from mewcode.agent import (
    LoopComplete,
    PermissionRequest,
    PermissionReply,
    PermissionResponse,
    ToolUseEvent,
    ErrorEvent,
)
from mewcode.client import create_client
from mewcode.conversation import ConversationManager
from mewcode.permissions import (
    DangerousCommandDetector,
    PathSandbox,
    PermissionMode,
    RuleEngine,
)
from mewcode.tools import create_default_registry
from mewcode.tools.load_skill import LoadSkill
from mewcode.rsi.fixtures import copy_files, tree_digest
from mewcode.rsi.strategy import assemble_strategy, FrozenSkillLoader
from mewcode.rsi.trajectory import RecordingClient, TrajectoryCollector
from mewcode.rsi.workspace import (
    CellAgent,
    PUBLIC_TEST,
    WorkspacePermissions,
    PublicTestTool,
    prepare_task,
    seal_artifact,
    remove_container,
)
from .catalog import repo_path
from .store import ROOT, emit, read


async def run(job, state, cfg, provider, strategy):
    source = repo_path(cfg["repo"])
    pin = read(source.parent / "pin.json")
    if tree_digest(source) != pin["digest"]:
        raise ValueError("Public snapshot changed")
    policy = read(ROOT / "evaluation/policy/docker.json")
    sid = uuid.uuid4().hex
    cid = hashlib.sha256(job.encode()).hexdigest()
    folder = state / "runs" / cid
    folder.mkdir(parents=True)
    identity = dict(
        cell_id=cid,
        session_id=sid,
        repo_commit=pin["commit"],
        strategy_digest=cfg["digest"],
        context=dict(provider_id=provider.name, model_id=provider.model),
        cell=dict(
            evaluation_id=job,
            case_id=cfg["repo"],
            expected_version=cfg["start_version"],
            run_index=1,
        ),
    )
    (folder / "identity.json").write_text(json.dumps(identity))
    content = assemble_strategy(strategy, cfg["digest"])
    with tempfile.TemporaryDirectory(prefix="rsi-project-") as tmp:
        scratch = Path(tmp).resolve()
        task = scratch / "task"
        prepare_task(source.parent, task, pin["commit"])
        trace = TrajectoryCollector(
            folder,
            sid,
            {p.relative_to(task).as_posix() for p in task.rglob("*") if p.is_file()},
        )
        checker = WorkspacePermissions(
            DangerousCommandDetector(),
            PathSandbox(str(task)),
            RuleEngine(),
            PermissionMode.ACCEPT_EDITS,
        )
        public_test = PublicTestTool(task, policy, "mewcode-rsi-" + sid)
        registry = create_default_registry()
        registry.register(public_test)
        agent = CellAgent(
            RecordingClient(create_client(provider), trace, provider.name),
            registry,
            provider.protocol,
            work_dir=str(task),
            max_iterations=cfg["turns"],
            permission_checker=checker,
            context_window=provider.get_context_window(),
            instructions_content=content.instructions,
        )
        agent.session_id = sid
        if content.skills:
            loader = FrozenSkillLoader(content.skills)
            tool = LoadSkill()
            tool.set_loader(loader)
            tool.set_agent(agent)
            registry.register(tool)
            agent.set_skill_catalog(
                "\n".join(f"{n}: {desc}" for n, desc in loader.get_catalog())
            )
        conv = ConversationManager()
        conv.add_user_message("Workspace request\n" + cfg["prompt"])
        old = Path.cwd()
        os.environ.setdefault("DOCKER_CONFIG", str(Path.home() / ".docker"))
        home = scratch / "home"
        home.mkdir(mode=0o700)
        os.environ.update(
            HOME=str(home),
            XDG_CONFIG_HOME=str(home / "config"),
            XDG_CACHE_HOME=str(home / "cache"),
        )
        os.chdir(task)
        status = "infrastructure_error"
        try:
            trace.record("started")
            async with asyncio.timeout(cfg["timeout"]):
                async for event in agent.run(conv):
                    decision = None
                    if isinstance(event, PermissionRequest):
                        reply = (
                            PermissionResponse.ALLOW
                            if event.tool_name == "Bash"
                            and event.description == PUBLIC_TEST
                            else PermissionResponse.DENY
                        )
                        event.future.set_result(PermissionReply(reply))
                        decision = reply.value
                    elif isinstance(event, ToolUseEvent):
                        tool = registry.get(event.tool_name)
                        decision = (
                            checker.check(tool, event.arguments).effect
                            if tool
                            else "deny"
                        )
                    trace.observe(event, decision)
                    if public_test.infrastructure_error:
                        raise RuntimeError("sandbox_unavailable")
                    if isinstance(event, LoopComplete):
                        status = "completed"
                    if (
                        isinstance(event, ErrorEvent)
                        and "maximum iterations" in event.message
                    ):
                        status = "turn_limit"
        except TimeoutError:
            status = "timed_out"
        except asyncio.CancelledError:
            status = "cancelled"
            await remove_container(public_test.container)
            raise
        finally:
            os.chdir(old)
            artifact = (
                seal_artifact(task, folder)
                if status in ("completed", "turn_limit", "timed_out")
                else None
            )
            trace.seal(status, cell_id=cid, artifact_digest=artifact)
        if not artifact:
            raise RuntimeError("runtime_failed")
        emit(job, "task_completed", {"status": status})
        emit(job, "evaluation_started", {"scope": "registered public tests"})
        tested = scratch / "tested"
        tested.mkdir()
        copy_files(folder / "artifact", tested)
        shutil.copyfile(source / "public_checks.py", tested / "public_checks.py")
        tool = PublicTestTool(tested, policy, "mewcode-public-" + sid)
        result = await tool.execute(
            SimpleNamespace(command=PUBLIC_TEST, timeout=policy["timeout_seconds"])
        )
        if tool.infrastructure_error:
            raise RuntimeError("sandbox_unavailable")
        receipt = dict(
            scope="public_checks",
            status="ok",
            passed=not result.is_error,
            score=None,
            repo_commit=pin["commit"],
            artifact_digest=artifact,
            session_id=sid,
        )
        (folder / "receipt.json").write_text(json.dumps(receipt))
        emit(job, "evaluation_completed", receipt)
