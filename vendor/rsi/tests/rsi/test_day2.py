"""真实 Agent、模型 HTTP 流和 Docker 公开测试之间的集成验收。"""

import asyncio
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import subprocess
import sys
import threading
import time
import uuid

import pytest

from mewcode.config import ProviderConfig
from mewcode.rsi.fixtures import tree_digest
from mewcode.rsi.runtime import MewCodeRuntime, ROOT, RunSpec
from mewcode.rsi.strategy import assemble_strategy
from mewcode.rsi.workspace import PUBLIC_TEST, PublicTestTool
from mewcode.tools.bash import Params as BashParams


@pytest.fixture
def endpoint(docker_policy):
    requests = []
    counter = 0
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            nonlocal counter
            req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            model = req["model"]
            messages = req["messages"]
            turn = sum(m["role"] == "assistant" for m in messages)
            with lock:
                requests.append(req)
                if turn == 0:
                    counter += 1
                mark = counter
            if model == "broken":
                self.send_error(401, "private-provider-detail")
                return
            if model == "slow":
                time.sleep(1.6)
            calls = [
                [("ReadFile", {"file_path": "settings.py"})],
                [("EditFile", {"file_path": "settings.py", "old_string": "return 2",
                               "new_string": "return settings.get('page_size', 2)"})],
                [("WriteFile", {"file_path": "worker.txt", "content": str(mark)}),
                 ("ReadFile", {"file_path": "../control-only"}),
                 ("Bash", {"command": "echo private-command-detail"})],
                [("Bash", {"command": PUBLIC_TEST})],
            ]
            options = calls[turn] if turn < len(calls) else []
            if turn == 0 and model == "script":
                options = options + [("LoadSkill", {"name": "on-demand"})]
            delta = {"content": "private-text-detail", "reasoning_content": "private-thinking-detail"}
            if options:
                delta["tool_calls"] = [
                    {"index": i, "id": f"call-{turn}-{i}", "type": "function",
                     "function": {"name": name, "arguments": json.dumps(args)}}
                    for i, (name, args) in enumerate(options)
                ]
            # 首轮分配的标记保存在会话历史内，后续请求可用其验证本次工作树。
            if turn == 0:
                delta["content"] = f"run-marker-{mark}"
            if turn == 2:
                first = next(m for m in messages if m["role"] == "assistant")
                marker = str(first["content"]).split("run-marker-")[-1].split()[0]
                delta["tool_calls"][0]["function"]["arguments"] = json.dumps(
                    {"file_path": "worker.txt", "content": marker})
            chunk = {"id": "chunk", "object": "chat.completion.chunk", "created": 1, "model": model}
            frames = [
                dict(chunk, choices=[{"index": 0, "delta": delta, "finish_reason": None}]),
                dict(chunk, choices=[{"index": 0, "delta": {}, "finish_reason": "tool_calls" if options else "stop"}]),
                dict(chunk, choices=[], usage={"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}),
            ]
            body = ("".join("data: " + json.dumps(f) + "\n\n" for f in frames) + "data: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def config(model):
        return ProviderConfig("local-script", "openai-compat", f"http://127.0.0.1:{server.server_port}/v1",
                              model, api_key="private-key-detail", context_window=64000)
    yield config, requests
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.fixture
def strategy(tmp_path):
    folder = tmp_path / "strategy"
    skill = folder / "skills" / "public-check"
    skill.mkdir(parents=True)
    (folder / "prompt.md").write_text("BEHAVIOR_MARKER")
    (folder / "workflow.yaml").write_text("stages:\n  - inspect\n  - name: verify\n    skills: [public-check]\n")
    (skill / "SKILL.md").write_text("---\nname: public-check\ndescription: verify\n---\nSKILL_BODY_MARKER\n")
    extra = skill.parent / "on-demand"
    extra.mkdir()
    (extra / "SKILL.md").write_text("---\nname: on-demand\ndescription: optional\n---\nON_DEMAND_BODY\n")
    return folder


@pytest.mark.asyncio
async def test_real_loop_two_parallel_workspaces_and_redacted_traces(endpoint, strategy, tmp_path):
    config, requests = endpoint
    runtime = MewCodeRuntime(tmp_path / "runs")
    specs = [RunSpec("day2", "page-settings", i, strategy_dir=strategy, strategy_digest=tree_digest(strategy))
             for i in (1, 2)]
    results = await asyncio.gather(*(runtime.run(s, config("script")) for s in specs))
    markers = set()
    assert results[0]["session_id"] != results[1]["session_id"]
    for result in results:
        assert result["status"] == "completed", result
        assert result["usage"] == {"input_tokens": 55, "output_tokens": 35}
        folder = runtime.runs_dir / result["cell_id"]
        assert tree_digest(folder / "artifact") == result["artifact_digest"]
        assert not (folder / "artifact/.mewcode").exists()
        assert "settings.get" in (folder / "artifact/settings.py").read_text()
        markers.add((folder / "artifact/worker.txt").read_text())
        text = (folder / "events.jsonl").read_text()
        for private in ("private-key-detail", "private-text-detail", "private-thinking-detail", "private-command-detail"):
            assert private not in text
        rows = [json.loads(line) for line in text.splitlines()]
        assert [r["seq"] for r in rows] == list(range(1, len(rows) + 1))
        assert {r["session_id"] for r in rows} == {result["session_id"]}
        ids = [r["tool_id"] for r in rows if r["type"] == "tool_use"]
        assert len(ids) == len(set(ids)) == 7
        assert sorted(r["tool_id"] for r in rows if r["type"] == "tool_result") == sorted(ids)
        assert [r["decision"] for r in rows if r["type"] == "permission"] == ["deny", "allow"]
        errors = {r["tool_id"] for r in rows if r["type"] == "tool_result" and r["is_error"]}
        assert errors == {5, 6}
    assert len(markers) == 2
    assert len(requests) == 10
    for request in requests:
        content = json.dumps(request["messages"], ensure_ascii=False)
        assert all(marker in content for marker in ("BEHAVIOR_MARKER", "1. inspect", "2. verify", "SKILL_BODY_MARKER"))
    assert sum("Public test exit code: 0" in json.dumps(r["messages"]) for r in requests) == 2
    assert sum("ON_DEMAND_BODY" in json.dumps(r["messages"]) for r in requests) == 8
    assert await runtime.run(specs[0], config("script")) == results[0]
    assert len(requests) == 10
    with pytest.raises(ValueError, match="预期指纹"):
        await runtime.run(replace(specs[0], expected_fingerprint="f" * 64), config("script"))
    (runtime.runs_dir / results[0]["cell_id"] / "artifact/worker.txt").write_text("drift")
    with pytest.raises(ValueError, match="产物封存摘要"):
        await runtime.run(specs[0], config("script"))


@pytest.mark.asyncio
@pytest.mark.parametrize(("model", "turns", "seconds", "expected"),
                         [("limited", 1, 30, "turn_limit"), ("slow", 5, 1, "timed_out"),
                          ("broken", 5, 30, "infrastructure_error")])
async def test_distinct_terminal_states(endpoint, tmp_path, model, turns, seconds, expected):
    config, _ = endpoint
    runtime = MewCodeRuntime(tmp_path / "runs")
    result = await runtime.run(RunSpec("terminal", "page-settings", max_turns=turns, timeout_seconds=seconds),
                               config(model))
    assert result["status"] == expected
    assert bool(result["artifact_digest"]) == (expected != "infrastructure_error")
    assert "private-provider-detail" not in (runtime.runs_dir / result["cell_id"] / "events.jsonl").read_text()


def test_strategy_snapshot_rejects_drift_and_executable_skill(strategy):
    initial = tree_digest(strategy)
    (strategy / "skills/public-check/run.sh").write_text("exit 1\n")
    with pytest.raises(ValueError, match="摘要"):
        assemble_strategy(strategy, initial)
    with pytest.raises(ValueError, match="未授权文件"):
        assemble_strategy(strategy, tree_digest(strategy))


def test_cli_runs_one_public_case(endpoint, tmp_path):
    config, requests = endpoint
    provider = config("cli")
    settings = tmp_path / "trusted.json"
    settings.write_text(json.dumps({"providers": [
        {"name": provider.name, "protocol": provider.protocol, "base_url": provider.base_url,
         "model": provider.model, "api_key": provider.api_key}
    ]}))
    proc = subprocess.run(
        [sys.executable, "-B", "-m", "mewcode.rsi.runtime", "page-settings", "--evaluation", "cli",
         "--provider-config", str(settings), "--provider", provider.name, "--runs-dir", str(tmp_path / "runs")],
        cwd=ROOT, capture_output=True, text=True, timeout=45, check=True,
    )
    result = json.loads(proc.stdout)
    assert result["status"] == "completed" and len(requests) == 5
    assert (tmp_path / "runs" / result["trace_ref"]).is_file()


@pytest.mark.asyncio
async def test_public_test_identity_and_failed_test_are_observable(docker_policy, tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_TEST_PRIVATE_SENTINEL", "host-only")
    private = tmp_path / "judge-only"
    private.write_text("host-only")
    task = tmp_path / "task"
    task.mkdir()
    (task / "visible").write_text("public")
    checks = task / "public_checks.py"
    checks.write_text(
        "from pathlib import Path\nimport os, unittest\n"
        "class PublicChecks(unittest.TestCase):\n"
        "    def test_boundary(self):\n"
        "        self.assertEqual(Path('visible').read_text(), 'public')\n"
        "        self.assertEqual(os.getuid(), 65534)\n"
        "        self.assertNotIn('RSI_TEST_PRIVATE_SENTINEL', os.environ)\n"
        f"        with self.assertRaises(OSError): Path({str(private)!r}).read_text()\n"
        "        with self.assertRaises(OSError): Path('visible').write_text('changed')\n"
    )
    tool = PublicTestTool(task, docker_policy, "mewcode-rsi-test-" + uuid.uuid4().hex)
    params = BashParams(command=PUBLIC_TEST)
    assert not (await tool.execute(params)).is_error
    checks.write_text("import sys\nsys.exit(125)\n")
    result = await tool.execute(params)
    assert result.is_error and "exit code: 1" in result.output
    assert not tool.infrastructure_error
