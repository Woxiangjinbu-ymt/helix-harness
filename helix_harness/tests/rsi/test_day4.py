"""Default RSI 的真实调用链、并发提交和故障恢复。"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import shutil
import sqlite3
import threading
import uuid

import pytest

from mtcode.config import ProviderConfig
from helix_harness.rsi.baseline import baseline, db_open, make_plan, pin_plan, publish
from helix_harness.rsi.default_optimizer import DefaultStrategyOptimizer, InvalidProposal, Proposal
from helix_harness.rsi.evaluator import CellFailure, verified_release
from helix_harness.rsi.fixtures import tree_digest
from helix_harness.rsi.matrix import requests
from helix_harness.rsi.orchestrator import evaluate_pending, optimize, progress
from helix_harness.rsi.trajectory import TrajectoryCollector
from helix_harness.rsi.versions import VersionManager


def proposal(parent, sessions, edits):
    return {"parent": parent, "hypothesis": "记录公开回归结果会增加成功检查的次数。", "evidence": sessions,
            "metric": "public_checks", "direction": "increase", "edits": edits}


def edit(path="prompt.md", content="先核对输入的优先级，再运行公开检查。"):
    return {"path": path, "content": content}


@pytest.mark.parametrize("bad", ["../secret", "skills/../SKILL.md", "skills/x/run.py", "policy/docker.json",
                                 "/tmp/prompt.md", "workflow.yml", "helix_harness/rsi/evaluator.py", "hooks/start.sh"])
def test_proposal_allowlist(bad):
    with pytest.raises(InvalidProposal):
        Proposal.parse(proposal(1, ["a"], [edit(bad)]), 1, {"a"}, ["page-settings"])


@pytest.fixture(scope="module")
def scripted():
    seen = {"proposals": [], "coding": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            model = request["model"]
            messages = request["messages"]
            delta = {"content": "done"}
            if model == "proposer":
                assert not request.get("tools")
                public = json.loads(messages[-1]["content"])
                seen["proposals"].append(public)
                k = len(public["history"])
                options = [
                    [edit("skills/review/SKILL.md", "---\nname: review\ndescription: review\n---\nRead callers."),
                     edit("workflow.yaml", "stages:\n  - name: review\n    skills: [review]\n")],
                    [edit(content="Trace configuration precedence across callers.")],
                    [edit(content="Review the request before returning.")],
                ]
                data = proposal(public["parent"], [r["session"] for r in public["findings"]], options[k])
                delta = {"content": json.dumps(data)}
            else:
                seen["coding"].append(request)
                context = json.dumps(messages, ensure_ascii=False)
                turn = sum(m["role"] == "assistant" for m in messages)
                if "Trace configuration precedence" in context and "分页配置" in context and turn < 2:
                    edits = [("settings.py", "return 2", "return settings.get('page_size', 2)"),
                             ("app.py", 'size = page_size(request.get("settings", {}))',
                              'size = request.get("limit", page_size(request.get("settings", {})))')]
                    delta = {"tool_calls": [
                        {"index": n, "id": f"tool-{turn}-{n}", "type": "function", "function": {
                         "name": "ReadFile" if turn == 0 else "EditFile",
                         "arguments": json.dumps({"file_path": path} if turn == 0 else
                                                 {"file_path": path, "old_string": old, "new_string": new})}}
                        for n, (path, old, new) in enumerate(edits)]}
            chunk = {"id": "local", "object": "chat.completion.chunk", "created": 1, "model": model}
            frames = [dict(chunk, choices=[{"index": 0, "delta": delta, "finish_reason": None}]),
                      dict(chunk, choices=[{"index": 0, "delta": {}, "finish_reason":
                           "tool_calls" if "tool_calls" in delta else "stop"}])]
            body = ("".join("data: " + json.dumps(f) + "\n\n" for f in frames) + "data: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    provider = ProviderConfig("local", "openai-compat", f"http://127.0.0.1:{server.server_port}/v1",
                              "coder", api_key="fixture-secret")
    yield provider, replace(provider, name="local-opt", model="proposer"), seen
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.mark.asyncio
async def test_real_worker_with_scripted_models(scripted, docker_policy, tmp_path):
    coder, proposer, seen = scripted
    base = await baseline(tmp_path, "test-base", coder)
    assert base["score"] == 30
    report = await optimize(tmp_path, "test-base", "loop", coder, proposer, runs=2, rounds=3)
    assert report["stop_reason"] == "round_limit"
    rows = report["attempts"]
    assert [(r["version"], r["parent"], r["score"], r["decision"]) for r in rows] == [
        (2, 1, 30, "rejected"), (3, 1, 55, "accepted"), (4, 3, 30, "rejected")]
    assert [r["version"] for r in report["scoreboard"]] == [1, 3]
    assert report["reference_version"] == 3 and report["completed_rounds"] == 3
    assert not rows[1]["prediction"]["supported"]
    assert all(len(c["runs"]) == 2 for r in rows for c in r["cases"])
    ids = [s["session_id"] for r in [base, *rows] for c in r["cases"] for s in c["runs"]]
    assert len(set(ids)) == len(ids) == 21
    first, second, third = seen["proposals"]
    assert first["strategy"] == second["strategy"] and "skills/review/SKILL.md" not in third["strategy"]
    assert first["history"] == [] and second["history"][0]["decision"] == "rejected"
    assert len(first["findings"]) == 3 and all(f["gap"] == "no_successful_edit" for f in first["findings"])
    assert "fixture-secret" not in json.dumps(seen["proposals"])
    snapshot = tmp_path / "objects" / rows[1]["strategy_digest"]
    assert tree_digest(snapshot) == rows[1]["strategy_digest"] and not (snapshot / "skills/review").exists()
    count = len(seen["coding"])
    assert await optimize(tmp_path, "test-base", "loop", coder, proposer, runs=2, rounds=3) == report
    assert len(seen["coding"]) == count and len(seen["proposals"]) == 3


# 控制层故障注入使用带完整身份、产物和事件的合成回执，不进入正式数据目录。
def seed(db, state, plan, lock, score, *, count=None):
    pins = {c["case_id"]: c for c in lock["benchmark"]["cases"]}
    for req in list(requests(plan))[:count]:
        cell = req.cell
        session = uuid.uuid4().hex
        folder = state / "runs" / cell.cell_id
        folder.mkdir(parents=True)
        (folder / "artifact").mkdir()
        (folder / "artifact/app.py").write_text("pass\n")
        commit = pins[cell.case_id]["repo_commit"]
        identity = {"cell": asdict(cell), "cell_id": cell.cell_id, "session_id": session,
                    "repo_commit": commit, "strategy_digest": req.strategy_digest, "context": plan["context"]}
        (folder / "identity.json").write_text(json.dumps(identity))
        trace = TrajectoryCollector(folder, session, set())
        trace.record("started")
        trace.record("model_request", provider=req.provider, model_id=req.model_id, thinking_mode=req.thinking_mode)
        outcome = trace.seal("completed", cell_id=cell.cell_id, run_fingerprint=cell.run_fingerprint,
                             artifact_digest=tree_digest(folder / "artifact"))
        receipt = dict(req.public_identity(), score=score, cost=None, status="ok", repo_commit=commit,
                       **{k: outcome[k] for k in ("session_id", "trace_ref", "trace_digest", "artifact_digest", "duration_ms")})
        with db:
            db.execute("INSERT INTO cells VALUES (?,?,?,?,?,?)", (cell.evaluation_id, cell.case_id, cell.run_index,
                       session, cell.cell_id, json.dumps(receipt)))


@pytest.fixture(scope="module")
def control_template(tmp_path_factory):
    state = tmp_path_factory.mktemp("control-template")
    provider = ProviderConfig("local", "openai-compat", "http://localhost/v1", "coder")
    plan = make_plan("fixture", provider)
    lock, _ = verified_release()
    db = db_open(state)
    pin_plan(db, plan)
    seed(db, state, plan, lock, 30)
    publish(db, state, plan)
    db.close()
    return state, plan, lock, provider


@pytest.fixture
def control(tmp_path, control_template):
    source, plan, lock, _ = control_template
    state = tmp_path / "state"
    shutil.copytree(source, state)
    manager = VersionManager(state, plan, lock)
    yield manager
    manager.db.close()


def stage(manager, sid, edits=None):
    manager.open_session(sid, {"runs": 1, "rounds": 2, "target": 100})
    ref = manager.current(sid)
    sid0 = ref["cases"][0]["runs"][0]["session_id"]
    return manager.stage(sid, ref, proposal(ref["version"], [sid0], edits or [edit()]), 1)


@pytest.mark.parametrize("edits", [
    [edit(content="Add a page_settings branch.")],
    [edit("workflow.yaml", "stages: [verify]\nprovider: other\n")],
    [edit("skills/a/SKILL.md", "---\nname: a\ndescription: ab\nmode: fork\n---\nx")],
    [edit("workflow.yaml", "stages: [{name: test, skills: [unknown]}]")],
    [edit("skills/a/SKILL.md", None)],
    [edit(), edit()],
])
def test_inadmissible_is_never_staged(control, edits):
    with pytest.raises(InvalidProposal):
        stage(control, "invalid", edits)
    assert control.board()["version"] == 1 and not control.attempts("invalid")


def test_invalid_matrix_and_snapshot_drift(control):
    row = stage(control, "partial")
    plan = json.loads(row["plan"])
    seed(control.db, control.state, plan, control.lock, 100, count=1)
    with pytest.raises(CellFailure, match="evaluation_failed"):
        control.finish("partial", row["version"])
    assert progress(control.db, "partial")["completed_rounds"] == 0
    bad = control.objects.get(plan["strategy_digest"]) / "prompt.md"
    bad.write_text("drift")
    with pytest.raises(CellFailure, match="version_changed"):
        control.finish("partial", row["version"])
    assert control.board()["version"] == 1


@pytest.mark.asyncio
async def test_registered_unsealed_cell_is_never_relaunched(control, control_template):
    row = stage(control, "interrupted")
    plan = json.loads(row["plan"])
    seed(control.db, control.state, plan, control.lock, 75, count=1)
    req = list(requests(plan))[1]
    (control.state / "runs" / req.cell.cell_id).mkdir()

    class Runtime:
        async def run(self, *args):
            pytest.fail("已有 cell 不可再次调用模型")

    class Judge:
        async def score(self, request):
            assert request == req
            raise CellFailure("evaluation_failed")

    with pytest.raises(CellFailure, match="evaluation_failed"):
        await evaluate_pending(control, "interrupted", row, control_template[3], Runtime(), Judge())
    report = progress(control.db, "interrupted")
    assert report["attempts"][0]["scored_cells"] == 1 and report["completed_rounds"] == 0
    assert len(report["scoreboard"]) == 1


@pytest.mark.asyncio
async def test_illegal_model_reply_stops_without_a_candidate(control, control_template, monkeypatch):
    async def bad_reply(self, public):
        first = public["observations"][0]
        return proposal(public["parent"], [first["session"]], [edit("evaluation/policy/docker.json")])
    monkeypatch.setattr(DefaultStrategyOptimizer, "propose", bad_reply)
    provider = control_template[3]
    report = await optimize(control.state, control.base, "bad", provider, provider)
    assert report["stop_reason"] == "invalid_proposal" and report["attempts"] == []
    assert report["reference_version"] == 1 and len(report["scoreboard"]) == 1


def test_interrupted_scoreboard_transaction_recovers(control):
    row = stage(control, "recover")
    seed(control.db, control.state, json.loads(row["plan"]), control.lock, 80)
    control.db.execute("""CREATE TEMP TRIGGER fail_head BEFORE UPDATE ON rsi_head
                          BEGIN SELECT RAISE(ABORT, 'interrupted after board insert'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        control.finish("recover", 2)
    reopened = VersionManager(control.state, control.plan, control.lock)
    try:
        assert len(progress(reopened.db, "recover")["scoreboard"]) == 1
        result = reopened.finish("recover", 2)
        assert result["decision"] == "accepted" and reopened.finish("recover", 2) == result
        assert [r["version"] for r in progress(reopened.db, "recover")["scoreboard"]] == [1, 2]
    finally:
        reopened.db.close()


def test_two_concurrent_promotions_have_one_winner(control):
    for sid, score in [("a", 50), ("b", 55)]:
        row = stage(control, sid, [edit(content=f"Read the caller before editing. Revision {sid}.")])
        seed(control.db, control.state, json.loads(row["plan"]), control.lock, score)
    barrier = threading.Barrier(2)

    def finish(sid, version):
        other = VersionManager(control.state, control.plan, control.lock)
        try:
            barrier.wait(timeout=20)
            try:
                return other.finish(sid, version)["decision"]
            except CellFailure as exc:
                return exc.code
        finally:
            other.db.close()

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(finish, sid, v) for sid, v in [("a", 2), ("b", 3)]]
        assert sorted(f.result(timeout=45) for f in futures) == ["accepted", "reference_changed"]
    assert control.db.execute("SELECT count(*) FROM rsi_board").fetchone()[0] == 2
    assert control.current("a" if control.board()["version"] == 2 else "b")["score"] in (50, 55)
