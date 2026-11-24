"""仅保存公开事件投影。outcome.json 的原子发布标志本次运行结束。"""

from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import time

from mtcode.agent import (
    CompactNotification, ErrorEvent, HookEvent, LoopComplete, PermissionRequest, RetryEvent,
    ToolResultEvent, ToolUseEvent, TurnComplete, UsageEvent,
)
from mtcode.client import LLMClient
from mtcode.tools.base import ToolCallComplete

from .contracts import digest
from .fixtures import file_digest


class TrajectoryCollector:
    def __init__(self, folder: Path, session_id: str, public_paths: set[str]):
        self.folder = folder
        self.session_id = session_id
        self.public_paths = public_paths
        self.started = time.monotonic()
        self.seq = 0
        self.calls = {}
        self.pending = set()
        self.awaiting_permission = []
        self.usage = {"input_tokens": 0, "output_tokens": 0}
        self.turns = 0
        self.stream = (folder / "events.jsonl").open("x", encoding="utf-8")

    def record(self, kind: str, **fields):
        self.seq += 1
        row = {"session_id": self.session_id, "seq": self.seq, "type": kind, **fields}
        self.stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
        self.stream.flush()

    def observe(self, event, decision=None):
        if isinstance(event, ToolUseEvent):
            if event.tool_id in self.calls:
                raise ValueError("工具 ID 重复")
            tool_id = len(self.calls) + 1
            self.calls[event.tool_id] = tool_id
            self.pending.add(event.tool_id)
            tool = event.tool_name if event.tool_name in PUBLIC_TOOLS else "unknown"
            if decision == "ask":
                self.awaiting_permission.append((tool, tool_id))
            path = event.arguments.get("file_path")
            public = {"file": path} if isinstance(path, str) and path in self.public_paths else {}
            self.record("tool_use", tool=tool, tool_id=tool_id, policy=decision, **public)
        elif isinstance(event, ToolResultEvent):
            if event.tool_id not in self.pending:
                raise ValueError("工具结果未配对")
            self.pending.remove(event.tool_id)
            self.record("tool_result", tool_id=self.calls[event.tool_id], is_error=event.is_error,
                        elapsed_ms=round(event.elapsed * 1000))
        elif isinstance(event, PermissionRequest):
            tool, tool_id = self.awaiting_permission.pop(0)
            if tool != event.tool_name:
                raise ValueError("工具授权未配对")
            self.record("permission", tool=tool, tool_id=tool_id, decision=decision)
        elif isinstance(event, UsageEvent):
            self.usage = {"input_tokens": event.input_tokens, "output_tokens": event.output_tokens}
            self.record("usage", **self.usage)
        elif isinstance(event, (TurnComplete, LoopComplete)):
            self.turns = event.turn if isinstance(event, TurnComplete) else event.total_turns
            self.record("turn_complete" if isinstance(event, TurnComplete) else "loop_complete", turn=self.turns)
        elif isinstance(event, (RetryEvent, CompactNotification, ErrorEvent, HookEvent)):
            self.record(type(event).__name__)

    def seal(self, status: str, **fields) -> dict:
        self.record("terminal", status=status)
        self.stream.close()
        outcome = {"session_id": self.session_id, "status": status, "events": self.seq,
                   "trace_ref": f"{self.folder.name}/events.jsonl",
                   "trace_digest": file_digest(self.folder / "events.jsonl"),
                   "duration_ms": round((time.monotonic() - self.started) * 1000),
                   "turns": self.turns, "usage": self.usage, **fields}
        temp = self.folder / "outcome.tmp"
        temp.write_text(json.dumps(outcome, ensure_ascii=False, indent=2) + "\n")
        os.replace(temp, self.folder / "outcome.json")
        return outcome


PUBLIC_TOOLS = {"ReadFile", "WriteFile", "EditFile", "Glob", "Grep", "Bash", "LoadSkill"}


class RecordingClient(LLMClient):
    def __init__(self, inner, trace, provider_name):
        self.inner = inner
        self.trace = trace
        self.provider_name = provider_name

    def set_max_output_tokens(self, tokens):
        self.inner.set_max_output_tokens(tokens)

    async def stream(self, conversation, system="", tools=None):
        request = {"system": system, "messages": [asdict(m) for m in conversation.get_messages()],
                   "tools": tools}
        thinking = getattr(self.inner, "thinking", None)
        mode = "provider_default" if thinking is None else ("on" if thinking else "off")
        self.trace.record("model_request", input_digest=digest(request), provider=self.provider_name,
                          model_id=self.inner.model, thinking_mode=mode)
        async for event in self.inner.stream(conversation, system=system, tools=tools):
            # 原循环使用工具 ID 命名溢写文件，只接受不含路径的标识。
            if isinstance(event, ToolCallComplete) and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", event.tool_id):
                raise ValueError("Provider 返回了无效的工具 ID")
            yield event
