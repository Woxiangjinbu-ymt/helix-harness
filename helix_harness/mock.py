"""Deterministic, loopback-only model for exercising the real workers without a key."""

import json
import os
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from mtcode.config import ProviderConfig, load_config
from .paths import PROVIDER_CONFIG
from pathlib import Path

HINT = "Trace configuration precedence across callers."
PORT = int(os.getenv("RSI_MOCK_PORT", "18963"))


def providers():
    mock = ProviderConfig(
        "demo-mock",
        "openai-compat",
        f"http://127.0.0.1:{PORT}/v1",
        "scripted-coder",
        api_key="local-demo",
    )
    path = Path(os.getenv("RSI_PROVIDER_CONFIG", PROVIDER_CONFIG))
    configured = load_config(path).providers if path.is_file() else []
    return {**{p.name: p for p in configured if p.name != "demo-mock"}, mock.name: mock}


def proposer(p):
    return replace(p, model="scripted-optimizer") if p.name == "demo-mock" else p


def response(request):
    messages = request["messages"]
    if request["model"] == "scripted-optimizer":
        data = json.loads(messages[-1]["content"])
        if len(data["history"]) >= 2:
            return {"content": "null"}
        first = not data["history"]
        proposal = dict(
            parent=data["parent"],
            hypothesis=(
                "Tracing configuration through callers will increase successful edits."
                if first
                else "Reviewing the request before editing may reduce tool errors."
            ),
            evidence=[r["session"] for r in data["findings"]],
            metric="writes" if first else "tool_errors",
            direction="increase" if first else "decrease",
            edits=[
                dict(
                    path="prompt.md",
                    content=HINT if first else "Review the request before returning.",
                )
            ],
        )
        return {"content": json.dumps(proposal)}
    context = json.dumps(messages, ensure_ascii=False)
    turn = sum(m["role"] == "assistant" for m in messages)
    calls = []
    if (HINT in context or "Workspace request" in context) and "分页配置" in context:
        for path, old, new in [
            ("settings.py", "return 2", "return settings.get('page_size', 2)"),
            (
                "app.py",
                'size = page_size(request.get("settings", {}))',
                'size = request.get("limit", page_size(request.get("settings", {})))',
            ),
        ]:
            if turn == 0:
                calls.append(("ReadFile", {"file_path": path}))
            if turn == 1:
                calls.append(
                    ("EditFile", dict(file_path=path, old_string=old, new_string=new))
                )
        if turn == 2:
            calls.append(("Bash", dict(command="python -m unittest public_checks")))
    elif "Workspace request" in context:
        if turn == 0:
            calls.append(("Glob", {"pattern": "*.py"}))
        if turn == 1:
            calls.append(("Bash", dict(command="python -m unittest public_checks")))
    if not calls:
        return {"content": "Completed the scripted model turn."}
    return {
        "tool_calls": [
            {
                "index": i,
                "id": f"call-{turn}-{i}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
            for i, (name, args) in enumerate(calls)
        ]
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        if (
            self.path != "/v1/chat/completions"
            or self.headers.get("Authorization") != "Bearer local-demo"
        ):
            self.send_error(403)
            return
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        delta = response(req)
        frames = [
            (delta, None),
            ({}, "tool_calls" if "tool_calls" in delta else "stop"),
        ]
        body = (
            "".join(
                "data: "
                + json.dumps(
                    dict(
                        id="local",
                        object="chat.completion.chunk",
                        created=1,
                        model=req["model"],
                        choices=[dict(index=0, delta=d, finish_reason=r)],
                    )
                )
                + "\n\n"
                for d, r in frames
            )
            + "data: [DONE]\n\n"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(body.encode())


def serve():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    return server
