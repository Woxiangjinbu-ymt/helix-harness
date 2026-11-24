"""正式评分的隔离、故障语义、重复提交与独立复算验收。"""

from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import shutil
import threading

import pytest

from mtcode.config import ProviderConfig
from helix_harness.rsi.baseline import baseline, db_open, make_plan, pin_plan, publish, request_for, rescore, save_receipt
from helix_harness.rsi.evaluator import CellFailure, Evaluator, verified_release
from helix_harness.rsi.fixtures import BENCHMARK, copy_files
from helix_harness.rsi.runtime import MTCodeRuntime, ROOT
from helix_harness.rsi.test_runner import TestRunner as PrivateRunner


@pytest.fixture
def no_edit_provider():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(req["model"])
            frame = {"id": "local", "object": "chat.completion.chunk", "created": 1, "model": req["model"],
                     "choices": [{"index": 0, "delta": {"content": "Done"}, "finish_reason": "stop"}]}
            data = ("data: " + json.dumps(frame) + "\n\ndata: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield ProviderConfig("test", "openai-compat", f"http://127.0.0.1:{server.server_port}/v1",
                         "starter", api_key="test-only"), seen
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.mark.parametrize("relative", ["helix_harness/rsi/test_runner.py", "evaluation/policy/judge-v1.json"])
def test_release_refuses_changed_judge_or_unpublished_benchmark(release_copy, relative):
    changed = release_copy / relative
    if changed.suffix == ".py":
        with changed.open("a") as stream:
            stream.write("\n")
    else:
        release = json.loads(changed.read_text())
        release["status"] = "draft"
        changed.write_text(json.dumps(release))
    with pytest.raises(CellFailure, match="benchmark_invalid"):
        verified_release(release_copy)


@pytest.mark.asyncio
async def test_public_check_replacement_cannot_raise_private_score(docker_policy, tmp_path):
    bench = ROOT / "evaluation" / BENCHMARK
    case = "page-settings"
    task = tmp_path / "task"
    shutil.copytree(bench / "public" / case / "repo", task)
    (task / "public_checks.py").write_text("import sys\nsys.exit(0)\n")
    protected = bench / "private" / case
    rubric = json.loads((protected / "checks.json").read_text())
    runner = PrivateRunner(docker_policy)
    assert (await runner.grade(task, rubric["checks"])).score == rubric["starter_score"] == 25
    copy_files(protected / "reference", task)
    assert (await runner.grade(task, rubric["checks"])).score == 100


@pytest.mark.asyncio
@pytest.mark.parametrize(("source", "reason", "exit_code"), [
    (None, "missing_entry", None),
    ("raise SystemExit(125)", "nonzero_exit", 125),
    ("import time; time.sleep(12)", "timeout", None),
    ("print('invalid json')", "malformed_output", 0),
    ("print(0)", "wrong_answer", 0),
    ("print('x' * 70000)", "output_limit", 0),
])
async def test_candidate_failure_can_score_zero(docker_policy, tmp_path, source, reason, exit_code):
    if source is not None:
        (tmp_path / "app.py").write_text(source + "\n")
    policy = dict(docker_policy, timeout_seconds=2 if reason == "timeout" else 30)
    grade = await PrivateRunner(policy).grade(tmp_path, [{"input": None, "expected": "hello"}])
    assert grade.score == 0
    assert grade.audit() == [{"reason": reason, "exit_code": exit_code}]


@pytest.mark.asyncio
async def test_private_check_exposes_only_input(docker_policy, tmp_path, monkeypatch):
    task = tmp_path / "task"
    task.mkdir()
    sentinel = tmp_path / "host-private"
    sentinel.write_text("judge-only")
    monkeypatch.setenv("PRIVATE_PROBE", "judge-only")
    (task / "app.py").write_text(
        "import json, os, sys\nfrom pathlib import Path\n"
        "assert set(json.load(sys.stdin)) == {'public_input'}\n"
        "assert not Path(" + repr(str(sentinel)) + ").exists()\n"
        "assert not Path('/var/run/docker.sock').exists() and os.getuid() == 65534\n"
        "assert set(p.name for p in Path('/task').iterdir()) == {'app.py'}\n"
        "assert 'PRIVATE_PROBE' not in os.environ\n"
        "print('true')\n")
    result = await PrivateRunner(docker_policy).grade(task, [{"input": {"public_input": 3}, "expected": True}])
    assert result.score == 100


@pytest.mark.asyncio
async def test_partial_resume_replay_and_refusal_paths(no_edit_provider, docker_policy, tmp_path, monkeypatch):
    provider, seen = no_edit_provider
    eid = "test-campaign"
    original = Evaluator.score

    async def interrupted(evaluator, req):
        if req.cell.case_id == "record-upgrade":
            raise CellFailure("evaluation_failed")
        return await original(evaluator, req)

    with monkeypatch.context() as m:
        m.setattr(Evaluator, "score", interrupted)
        with pytest.raises(CellFailure, match="evaluation_failed"):
            await baseline(tmp_path, eid, provider)
    db = db_open(tmp_path)
    try:
        assert db.execute("SELECT count(*) FROM cells").fetchone() == (1,)
        assert db.execute("SELECT count(*) FROM evaluations").fetchone() == (0,)
        assert not (tmp_path / "baselines").exists()
        assert seen == ["starter"] * 2
        report = await baseline(tmp_path, eid, provider)
        assert seen == ["starter"] * 3
        assert [c["score"] for c in report["cases"]] == [25, 40, 25]
        assert report["score"] == 30 and report["cost"] is None
        receipts = [c["runs"][0] for c in report["cases"]]
        assert len({r["session_id"] for r in receipts}) == len(receipts) == 3
        assert all(r["cost"] is None and "checks" not in r for r in receipts)
        assert (tmp_path / "evaluator-private").is_dir()
        assert json.loads((tmp_path / "baselines" / (eid + ".json")).read_text()) == report

        async def forbid_agent(*args):
            pytest.fail("复算和完整基线重试均不得再次启动 Agent")

        monkeypatch.setattr(MTCodeRuntime, "run", forbid_agent)
        assert await baseline(tmp_path, eid, provider) == report
        plan = make_plan(eid, provider)
        case = plan["cases"][0]
        request = request_for(plan, case)
        assert await rescore(tmp_path, eid, case) == receipts[0]
        save_receipt(db, request, receipts[0])
        with pytest.raises(CellFailure, match="evaluation_failed"):
            save_receipt(db, request, dict(receipts[0], score=0))
        with pytest.raises(CellFailure, match="version_changed"):
            await baseline(tmp_path, eid, replace(provider, model="other"))
        with pytest.raises(CellFailure, match="version_changed"):
            await original(Evaluator(tmp_path), replace(request, thinking_mode="on"))

        # 保留 Git，使 worker 能先通过冻结核验，然后在启动 Docker 时遇到设施故障。
        stub = tmp_path / "control-bin"
        stub.mkdir()
        (stub / "git").symlink_to(shutil.which("git"))
        with monkeypatch.context() as m:
            m.setenv("PATH", str(stub))
            with pytest.raises(CellFailure, match="evaluation_failed"):
                await rescore(tmp_path, eid, case)
        assert db.execute("SELECT count(*) FROM cells").fetchone() == (3,)
        with db:
            db.execute("INSERT INTO cells VALUES (?,?,?,?,?,?)", (eid, "extra", 1, "extra", "extra", "{}"))
        with pytest.raises(CellFailure, match="evaluation_failed"):
            publish(db, tmp_path, plan)

        run = tmp_path / "runs" / request.cell.cell_id
        for name in ("events.jsonl", "artifact", "identity.json"):
            stored = run / name
            saved = stored.rename(run / "hold")
            try:
                with pytest.raises(CellFailure, match="evaluation_failed"):
                    await rescore(tmp_path, eid, case)
            finally:
                saved.rename(stored)
        assert seen == ["starter"] * 3
        assert db.execute("SELECT count(*) FROM evaluations").fetchone() == (1,)
    finally:
        db.close()


def test_complete_matrix_can_publish_zero_and_keeps_unknown_cost(tmp_path):
    provider = ProviderConfig("fixture", "openai-compat", "http://localhost/v1", "fixture")
    plan = make_plan("zero-fixture", provider)
    db = db_open(tmp_path)
    try:
        pin_plan(db, plan)
        for index, case in enumerate(plan["cases"], 1):
            req = request_for(plan, case)
            receipt = dict(req.public_identity(), status="ok", score=0, cost=None if index == 1 else 0.01,
                           duration_ms=500, repo_commit="a" * 40, trace_digest="b" * 64, artifact_digest="c" * 64,
                           trace_ref=f"{req.cell.cell_id}/events.jsonl", session_id=f"{index:032x}")
            with pytest.raises(CellFailure):
                save_receipt(db, req, dict(receipt, private_checks=[]))
            if index == 2:
                with pytest.raises(CellFailure):
                    save_receipt(db, req, dict(receipt, session_id=f"{1:032x}"))
            save_receipt(db, req, receipt)
            if index == 1:
                with pytest.raises(CellFailure):
                    publish(db, tmp_path, plan)
        report = publish(db, tmp_path, plan)
        assert report["score"] == 0 and report["cost"] is None
        assert db.execute("SELECT count(*) FROM evaluations").fetchone() == (1,)
    finally:
        db.close()
