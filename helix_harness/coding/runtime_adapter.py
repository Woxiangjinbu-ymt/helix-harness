"""将原生 Agent 的真实事件与执行入口绑定到所属 Web 调用。"""

import asyncio
import json
import os
import re
import sys
from contextvars import ContextVar
from dataclasses import asdict
from pathlib import Path

from ..paths import APP_DATA
from mtcode import agent as native
from mtcode.config import ProviderConfig
from mtcode.conversation import ConversationManager
from mtcode.hooks.engine import HookEngine, HookNotification
from mtcode.hooks.loader import load_hooks
from mtcode.hooks.models import ToolRejectedError
from mtcode.memory.session import (
    SessionRecord,
)
from mtcode.permissions import (
    DangerousCommandDetector,
    Decision,
    PathSandbox,
    PermissionChecker,
    PermissionMode,
    RuleEngine,
)
from mtcode.permissions.rules import Rule, extract_content
from mtcode.tools import ToolRegistry
from mtcode.tools.base import Tool, ToolResult

from .model_input import CodingModel, compact_record, replay
from .store import uid
from .worker import MAX_FRAME, Channel

current_call = ContextVar("coding_call")


def checker(directory, mode, local_rules, sandbox, policy=None):
    class Rules(RuleEngine):
        def snapshot(self):
            return (
                super().snapshot()
                if policy is None
                else [Rule(**r) for r in policy]
                + [r for r in super().snapshot() if r.effect in ("deny", "ask")]
            ) + RuleEngine(local_rules_path=local_rules).snapshot()

    root = Path(directory)
    rules = Rules(
        user_rules_path=APP_DATA / "permissions.yaml",
        project_rules_path=root / ".mewcode/permissions.yaml",
        local_rules_path=root / ".mewcode/permissions.local.yaml",
    )

    class WebChecker(PermissionChecker):
        def check(self, tool, args):
            observed = self.rule_engine.evaluate(
                tool.name, extract_content(tool.name, args)
            )
            decision = super().check(tool, args)
            if observed == "deny" or decision.effect == "deny":
                return Decision(
                    "deny", "策略禁止执行" if observed == "deny" else decision.reason
                )
            if observed == "ask":
                return Decision("ask", "策略要求本次确认")
            return decision

    return WebChecker(
        DangerousCommandDetector(),
        PathSandbox(str(root)),
        rules,
        PermissionMode(mode),
        sandbox_enabled=sandbox["enabled"] and sandbox["auto_allow"],
    )


class RoutedHooks(HookEngine):
    def __init__(self, definitions, channel, extra_hooks=None):
        super().__init__(load_hooks(definitions) + list(extra_hooks or ()))
        self.channel = channel

    async def invoke(self, hook):
        result = await self.channel.request(
            "hook", hook_id=hook.id, event=hook.event, parent_call=current_call.get()
        )
        self._notifications.append(
            HookNotification(
                hook.id, hook.event, result["output"], not result["is_error"]
            )
        )
        return result

    async def _run_single(self, hook, ctx):
        if hook.action.type == "prompt":
            await super()._run_single(hook, ctx)
            return
        await self.invoke(hook)

    async def run_pre_tool_hooks(self, ctx):
        for hook in self.find_matching_hooks("pre_tool_use", ctx):
            hook.mark_executed()
            if hook.action.type == "prompt":
                msg = ctx.expand(hook.action.message)
                self._notifications.append(
                    HookNotification(hook.id, "pre_tool_use", msg, True)
                )
                if hook.reject:
                    return ToolRejectedError(ctx.tool_name, msg, hook.id)
                continue
            result = await self.invoke(hook)
            if hook.reject or result["is_error"]:
                return ToolRejectedError(ctx.tool_name, result["output"], hook.id)
        return None


class RoutedTool(Tool):
    def __init__(self, tool, channel, overlay=None):
        self.name, self.category = tool.name, tool.category
        self.params_model = tool.params_model
        self.channel = channel
        self.schema = dict(tool.get_schema())
        overlay_text = (overlay or "").strip()
        if overlay_text:
            self.schema["description"] = (
                f"{self.schema['description']}\n\n[Repository Tool Guidance]\n{overlay_text}"
            )
        self.description = self.schema["description"]

    def get_schema(self):
        return self.schema

    async def execute(self, params):
        response = await self.channel.request(
            "action",
            call_id=current_call.get(),
            action_id=uid(),
            tool=self.name,
            args=params.model_dump(),
        )
        return ToolResult(response["output"], is_error=response["is_error"])


class SessionConversation(ConversationManager):
    """扩展与环境由当前快照注入，下一轮可以重新选择。"""

    def inject_long_term_memory(self, instructions, memories, skills=""):
        previous = self.ltm_injected
        super().inject_long_term_memory(instructions, memories, skills)
        if not previous and self.ltm_injected:
            self.history[1 if self.env_injected else 0].persisted = True

    def inject_environment(self, context):
        previous = self.env_injected
        super().inject_environment(context)
        if not previous and self.env_injected:
            self.history[0].persisted = True


class WebAgent(native.Agent):
    def __init__(self, *, private_dir, **kwargs):
        super().__init__(**kwargs)
        self.private_dir = private_dir
        self.calls = {}

    @property
    def session_dir(self):
        return self.private_dir / "tool-results"

    @property
    def _transcript_path(self):
        return str(self.private_dir / "history.jsonl")

    async def _execute_single_tool_direct(self, tc):
        token = current_call.set(self.calls[tc.tool_id])
        try:
            return await super()._execute_single_tool_direct(tc)
        finally:
            current_call.reset(token)

    async def _execute_tool(self, tc):
        token = current_call.set(self.calls[tc.tool_id])
        try:
            async for item in super()._execute_tool(tc):
                yield item
        finally:
            current_call.reset(token)

    def _snapshot_for_recovery(self, tc, result):
        # 回读也要走受管的文件入口，避免原生恢复旁路再打开同一个路径。
        if tc.tool_name == "ReadFile" and not result.is_error:
            self.recovery_state.record_file_read(
                tc.arguments.get("file_path", ""), result.output
            )


async def run(data, channel):
    from mtcode.tools.base import MAX_OUTPUT_CHARS
    from mtcode.tools.bash import Bash
    from mtcode.tools.edit_file import EditFile
    from mtcode.tools.glob import Glob
    from mtcode.tools.grep import Grep
    from mtcode.tools.read_file import ReadFile
    from mtcode.tools.write_file import WriteFile

    snap = data["snapshot"]
    directory = Path(data["directory"])
    os.chdir(directory)
    supplied = ProviderConfig(
        snap["provider"],
        snap["protocol"],
        snap["endpoint"],
        snap["model"],
        api_key=data.pop("credential"),
        thinking=snap["thinking"],
        context_window=snap["context_window"],
        max_output_tokens=snap["max_output_tokens"],
    )

    from mtcode.context.manager import compute_compact_threshold

    harness = data.get("harness") or {}
    overlays = harness.get("description_overlays") or {}
    guidance_hooks = []
    if harness.get("tool_hooks"):
        from helix_harness.rsi.repo.harness import build_guidance_hooks

        guidance_hooks = build_guidance_hooks(harness["tool_hooks"])

    registry = ToolRegistry()
    for cls in (ReadFile, EditFile, WriteFile, Glob, Grep, Bash):
        tool_obj = cls()
        registry.register(RoutedTool(tool_obj, channel, overlay=overlays.get(tool_obj.name)))
    from .mcp_bridge import BoundMCPTool

    for schema in data.get("mcp_schemas", []):
        registry.register(RoutedTool(BoundMCPTool(schema), channel))
    private = Path(data["private_dir"])
    (private / "tool-results").mkdir(mode=0o700, parents=True, exist_ok=True)
    merged_skills = dict(data.get("skills") or {})
    for s_name, s_item in (harness.get("skills") or {}).items():
        if isinstance(s_item, dict) and s_item.get("prompt_body"):
            merged_skills.setdefault(s_name, s_item["prompt_body"])
        elif isinstance(s_item, str):
            merged_skills.setdefault(s_name, s_item)
    instruction_parts = [data["instructions"]]
    if harness.get("instructions"):
        instruction_parts.append(harness["instructions"])
    instruction_parts.extend(
        f"Skill {name}\n{body}" for name, body in merged_skills.items()
    )
    hook_engine = (
        RoutedHooks(data.get("hooks") or [], channel, extra_hooks=guidance_hooks)
        if (data.get("hooks") or guidance_hooks)
        else None
    )
    agent = WebAgent(
        client=CodingModel(supplied, snap, channel),
        registry=registry,
        protocol=supplied.protocol,
        work_dir=str(directory),
        private_dir=private,
        max_iterations=snap["budget"]["max_iterations"],
        hook_engine=hook_engine,
        permission_checker=checker(
            data["root"],
            snap["permission_mode"],
            Path(data["rules"]),
            snap["sandbox"],
            data.get("policy"),
        ),
        context_window=snap["context_window"],
        instructions_content="\n\n".join(p for p in instruction_parts if p),
    )
    agent.session_id = data["session_id"]
    records = [SessionRecord.from_jsonl(x) for x in data["history"]]
    boundary = max(
        (i for i, r in enumerate(records) if r.is_compact_boundary()), default=0
    )
    conv = SessionConversation(history=replay(records[boundary:], data, snap))
    for msg in conv.history:
        msg.persisted = True
    history_file = private / "history.jsonl"
    history_file.write_text("".join(r.to_jsonl() + "\n" for r in records))
    await channel.request("ready")
    message = uid()
    failure = None

    async def emit(kind, **payload):
        await channel.request("event", event=kind, value=payload)

    async def persist(record):
        serialized = record.to_jsonl()
        if supplied.api_key:
            from .profiles import redact

            value = json.loads(serialized)
            for field in ("content", "tool_uses", "tool_results"):
                if field in value:
                    value[field] = redact(value[field], supplied.api_key)
            serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        await emit("native_record", record=serialized)
        if record.role == "assistant" and not record.is_compact_boundary():
            await emit("message_final", message_id=message)
        with history_file.open("a") as stream:
            stream.write(serialized + "\n")

    last_context = None

    async def report_context():
        nonlocal last_context
        reading = (
            conv.current_tokens(),
            "measured"
            if conv.baseline_tokens and conv.anchor_count == len(conv.history)
            else "estimated",
        )
        if reading != last_context:
            await emit(
                "context",
                used=reading[0],
                source=reading[1],
                window=snap["context_window"],
                threshold=compute_compact_threshold(snap["context_window"]),
                model=snap["model"],
                profile_id=snap["profile_id"],
            )
            last_context = reading

    await report_context()
    for name in data.get("skills", {}):
        await emit("extension", extension="Skill", name=name, status="loaded")
    async for ev in agent.run(conv):
        if isinstance(ev, native.ToolUseEvent):
            if (
                not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", ev.tool_id)
                or ev.tool_id in agent.calls
            ):
                raise ValueError("工具调用标识重复")
            call = agent.calls[ev.tool_id] = uid()
            await emit(
                "tool_call",
                call_id=call,
                tool=ev.tool_name,
                args=ev.arguments,
                native_id=ev.tool_id,
            )
        elif isinstance(ev, native.ToolResultEvent):
            await emit(
                "tool_result",
                call_id=agent.calls.pop(ev.tool_id),
                tool=ev.tool_name,
                is_error=ev.is_error,
                output=ev.output[:MAX_OUTPUT_CHARS],
                truncated=len(ev.output) > MAX_OUTPUT_CHARS,
            )
        elif isinstance(ev, native.PermissionRequest):
            call = current_call.get()
            ans = await channel.request(
                "permission", call_id=call, description=ev.description
            )
            await channel.request(
                "delivered", call_id=call, permission_id=ans["permission_id"]
            )
            decision = ans["decision"]
            if decision == "allow_always":
                try:
                    await channel.request("save_rule", call_id=call, action_id=uid())
                    decision = "allow"
                except ValueError as exc:
                    decision = "deny"
                    ans["feedback"] = str(exc)
            ev.future.set_result(
                native.PermissionReply(
                    native.PermissionResponse(decision), ans["feedback"]
                )
            )
        elif isinstance(ev, (native.StreamText, native.ThinkingText)):
            await emit(
                "text" if isinstance(ev, native.StreamText) else "thinking",
                message_id=message,
                text=ev.text,
            )
        elif isinstance(ev, native.CompactNotification):
            if ev.boundary is not None:
                await persist(compact_record(ev.boundary.summary, ev.boundary.keep))
                for message_in_boundary in conv.history:
                    message_in_boundary.persisted = True
            await emit(
                "compact",
                text=ev.message,
                boundary=ev.boundary is not None,
                kept=len(ev.boundary.keep) if ev.boundary else 0,
            )
        elif isinstance(ev, native.ErrorEvent):
            failure = ev.message
            await emit("error", text=ev.message)
        elif (
            isinstance(ev, native.UsageEvent)
            and agent.client.latest
            and (ev.input_tokens or ev.output_tokens or agent.client.cached)
        ):
            await emit(
                "usage",
                input_tokens=ev.input_tokens + agent.client.cached,
                output_tokens=ev.output_tokens,
            )
        for msg in conv.history:
            if not msg.persisted:
                for record in SessionRecord.from_message(msg):
                    if msg.thinking_blocks and supplied.protocol == "openai-compat":
                        record.type = "web_reply"
                        record.content = {
                            "text": msg.content,
                            "profile": snap["profile_id"],
                            "model": snap["model"],
                            "blocks": [asdict(b) for b in msg.thinking_blocks],
                        }
                        msg.web_reply = record.content
                    await persist(record)
                msg.persisted = True
        if isinstance(ev, native.LoopComplete) and agent.client.latest:
            measured = agent.client.latest
            conv.record_usage_anchor(
                measured.input_tokens,
                measured.output_tokens,
                measured.cache_read,
                measured.cache_creation,
            )
        if isinstance(
            ev,
            (
                native.CompactNotification,
                native.ToolResultEvent,
                native.LoopComplete,
                native.TurnComplete,
            ),
        ):
            await report_context()
        if isinstance(ev, native.TurnComplete):
            message = uid()
    await channel.request(
        "finish", state="failed" if failure else "completed", reason=failure
    )


def main():
    import threading
    import time

    parent = int(os.environ.get("CODING_OWNER_PID", os.getppid()))

    def watch_parent():
        while os.getppid() == parent and parent != 1:
            time.sleep(0.5)
        os._exit(1)

    threading.Thread(target=watch_parent, daemon=True).start()
    source, sink = sys.stdin.buffer, sys.stdout.buffer
    sys.stdout = sys.stderr
    first = json.loads(source.readline(MAX_FRAME))
    channel = Channel(first["instance"], source, sink)

    async def supervise():
        import signal

        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
        loop.add_signal_handler(signal.SIGUSR1, task.cancel)
        try:
            await run(first, channel)
        except (BrokenPipeError, ConnectionError):
            raise
        except Exception as exc:  # noqa: BLE001 — 向管理器报告原生执行失败
            await channel.request(
                "finish", state="failed", reason=f"{type(exc).__name__}: {exc}"
            )

    asyncio.run(supervise())
