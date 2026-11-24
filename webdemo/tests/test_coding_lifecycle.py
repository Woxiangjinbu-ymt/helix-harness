"""通过真实 Git 仓库和独立 SQLite 连接验证普通会话。"""

from concurrent.futures import ThreadPoolExecutor
from subprocess import run
from uuid import uuid4

import pytest

from webdemo.coding import profiles
from webdemo.coding.models import Conflict
from webdemo.coding.store import Store
from webdemo.coding.worktrees import Worktree


def git(path, *args):
    return run(["git", "-C", str(path), *args], check=True, capture_output=True)


@pytest.fixture
def local(tmp_path):
    repo = tmp_path / "project"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "test.txt").write_text("original\n")
    git(repo, "add", ".")
    git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@invalid",
        "commit",
        "-qm",
        "base",
    )
    return repo, Store(tmp_path / "state")


def test_worktree_and_view(local, tmp_path):
    repo, store = local
    alias = tmp_path / "alias"
    alias.symlink_to(repo)
    (repo / "subdir").mkdir()
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "--detach", str(linked))
    a = store.create_session(str(alias), "")
    b = store.create_session(str(repo / "subdir"), "")
    c = store.create_session(str(linked), "")
    assert a["worktree_id"] == b["worktree_id"] != c["worktree_id"]
    tree = Worktree(**a["identity"])
    (repo / "test.txt").write_text("staged\n")
    git(repo, "add", "test.txt")
    (repo / "test.txt").write_text("working\n")
    (repo / "new.txt").write_text("unique marker")
    (repo / "link").symlink_to(store.root)
    assert tree.files("unique", content=True) == ["new.txt"]
    assert "new.txt" in tree.files("new")
    with pytest.raises(ValueError):
        tree.read("link/coding.sqlite")
    with pytest.raises(ValueError):
        tree.resolve("link/new.txt")
    changes = {r["path"]: r["status"] for r in tree.changes()}
    assert changes == {"new.txt": "??", "test.txt": "MM"}
    diff = tree.changes("test.txt")[0]
    assert "+staged" in diff["staged"] and "+working" in diff["working"]
    (repo / "test.txt").unlink()
    assert tree.changes("test.txt")[0]["status"] == "MD"
    repo.rename(tmp_path / "moved")
    git(tmp_path, "init", str(repo))
    with pytest.raises(ValueError):
        tree.verify()


def test_plain_directory_identity_files_and_retry(local, tmp_path):
    _, store = local
    folder = tmp_path / "PPT"
    folder.mkdir()
    (folder / "slides.md").write_text("local note")
    (folder / ".env").write_text("private")
    (folder / "external").symlink_to(store.root)
    alias = tmp_path / "slides"
    alias.symlink_to(folder)
    request = uuid4()
    a = store.create_session(folder, "", request)
    assert store.create_session(folder, "", request)["session_id"] == a["session_id"]
    assert store.create_session(alias, "")["worktree_id"] == a["worktree_id"]
    if (tmp_path / "ppt").is_dir():
        assert (
            store.create_session(tmp_path / "ppt", "")["worktree_id"]
            == a["worktree_id"]
        )
    tree = Worktree(**a["identity"])
    assert a["workspace_kind"] == "folder"
    assert tree.verify() == folder
    assert tree.files("local note", True) == ["slides.md"]
    assert set(tree.file_stamps()) == {"slides.md"}
    assert not (folder / ".git").exists()
    (folder / ".git").write_text("gitdir: missing")
    with pytest.raises(ValueError, match="Git"):
        Worktree.identify(folder)


def test_directory_picker_and_open_errors(configured, tmp_path):
    import os

    client, repo, _, _ = configured
    folder = tmp_path / "folder"
    folder.mkdir()
    item = folder / "notes.txt"
    item.touch()
    listing = client.post("/api/coding/directories", json={"directory": str(tmp_path)})
    assert listing.status_code == 200 and folder.name in listing.json()["folders"]
    for directory, reason in ((item, "是文件"), (folder / "missing", "不存在")):
        result = client.post("/api/coding/sessions", json={"directory": str(directory)})
        assert result.status_code == 422 and reason in result.json()["detail"]
    if os.geteuid() != 0:
        folder.chmod(0)
        try:
            result = client.post(
                "/api/coding/sessions", json={"directory": str(folder)}
            )
            assert result.status_code == 422 and "权限" in result.json()["detail"]
        finally:
            folder.chmod(0o700)
    nested = repo / "nested"
    nested.mkdir()
    (nested / ".git").mkdir()
    result = client.post("/api/coding/sessions", json={"directory": str(nested)})
    assert result.status_code == 422 and "Git" in result.json()["detail"]


def test_turn_receipts_and_events(local):
    repo, store = local
    sid = store.create_session(repo, "")["session_id"]
    request = uuid4()

    def submit(_):
        return store.submit(sid, request, "message", lambda _: {"model": "one"})

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(submit, range(2)))
    assert sorted(created for _, created in results) == [False, True]
    turn = results[0][0]["turn_id"]
    assert turn == results[1][0]["turn_id"]
    with pytest.raises(Conflict):
        store.submit(sid, request, "other", lambda _: {})
    with pytest.raises(Conflict):
        store.submit(sid, uuid4(), "other", lambda _: {})
    with store.connection(True) as db:
        seq = store.event(db, sid, turn, "worker/1", "text", {"text": "A"})
        assert store.event(db, sid, turn, "worker/1", "text", {"text": "A"}) == seq
    with pytest.raises(RuntimeError), store.connection(True) as db:
        store.event(db, sid, turn, "worker/2", "text", {"text": "B"})
        raise RuntimeError("rollback")
    with Store(store.root).connection() as db:
        assert [e["seq"] for e in store.events(db, sid)] == [1, 2, 3]


def test_config_snapshot(local, monkeypatch):
    from mewcode.config import AppConfig, ProviderConfig

    _, store = local
    p = ProviderConfig(
        "sample",
        "openai-compat",
        "http://localhost/v1",
        "one",
        api_key="sentinel-secret",
    )
    monkeypatch.setattr(
        profiles.providers, "select", lambda _: (AppConfig(providers=[p]), p)
    )
    first = profiles.snapshot("sample", store.root)
    assert first == profiles.snapshot("sample", store.root)
    assert p.api_key not in str(first)
    p.model = "two"
    assert first != profiles.snapshot("sample", store.root)


@pytest.fixture
def configured(local, tmp_path, monkeypatch):
    """受控 Provider 只接受测试凭据。每个测试使用独立库和正常 HTTP 入口。"""
    import json
    from http.server import ThreadingHTTPServer
    from threading import Thread

    from fastapi.testclient import TestClient

    from webdemo import mock
    from webdemo.api import app
    from webdemo.coding import routes

    repo, store = local
    model = ThreadingHTTPServer(("127.0.0.1", 0), mock.Handler)
    server = Thread(target=model.serve_forever, daemon=True)
    server.start()
    config = tmp_path / "profile.json"
    doc = {
        "providers": [
            {
                "name": "controlled",
                "protocol": "openai-compat",
                "model": "deterministic",
                "api_key": "local-demo",
                "base_url": f"http://127.0.0.1:{model.server_port}/v1",
            }
        ]
    }
    config.write_text(json.dumps(doc))
    monkeypatch.setenv("CODING_PROVIDER_CONFIG", str(config))
    monkeypatch.setenv("CODING_WEB_HOME", str(store.root))
    monkeypatch.setenv("MODEL_CREDENTIAL_TEST_MARKER", "local-demo")
    routes.store.cache_clear()
    routes.manager.cache_clear()
    try:
        with TestClient(
            app, base_url="http://127.0.0.1", headers={"X-RSI-Client": "console"}
        ) as client:
            yield client, repo, store, config
    finally:
        model.shutdown()
        model.server_close()
        server.join()
        routes.store.cache_clear()
        routes.manager.cache_clear()


def delta(*tools, content="", thinking=""):
    import json

    data = {"role": "assistant"}
    if content:
        data["content"] = content
    if thinking:
        data["reasoning_content"] = thinking
    if tools:
        data["tool_calls"] = [
            {
                "index": i,
                "id": str(uuid4()),
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
            for i, (name, args) in enumerate(tools)
        ]
    return data


def wait_view(api, path, test):
    import time

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        result = api.get(path + "/view")
        result.raise_for_status()
        view = result.json()
        if test(view):
            return view
        time.sleep(0.04)
    pytest.fail(
        str([(e["kind"], str(e["payload"])[:120]) for e in view["events"][-8:]])
    )


def test_live_runtime_authorization_and_reopen(configured, monkeypatch):
    import asyncio

    from webdemo import mock
    from webdemo.coding import actions

    real_file_action = actions.file_action
    second_returned = asyncio.Event()

    async def delayed_read(tree, cwd, tool, args, seen, scratch, **kwargs):
        if tool == "ReadFile" and args["file_path"] == "test.txt":
            await asyncio.wait_for(second_returned.wait(), 4)
        result = await real_file_action(tree, cwd, tool, args, seen, scratch, **kwargs)
        if tool == "ReadFile" and args["file_path"] == "missing.txt":
            second_returned.set()
        return result

    monkeypatch.setattr(actions, "file_action", delayed_read)

    client, repo, _store, _config = configured
    requests = []

    def model(req):
        requests.append(req["messages"])
        steps = [
            delta(
                ("ReadFile", {"file_path": "test.txt"}),
                ("ReadFile", {"file_path": "missing.txt"}),
                thinking="controlled thought",
            ),
            delta(("WriteFile", {"file_path": "created.txt", "content": "created"})),
            delta(("WriteFile", {"file_path": "blocked.txt", "content": "no"})),
            delta(content="first complete"),
            delta(content="second complete"),
        ]
        return steps[len(requests) - 1]

    monkeypatch.setattr(mock, "response", model)

    def create(dir):
        r = client.post("/api/coding/sessions", json={"directory": str(dir)})
        assert r.status_code == 201, r.text
        return "/api/coding/sessions/" + r.json()["session_id"]

    path, other = create(repo), create(repo)
    form = {"submit_request_id": str(uuid4()), "text": "first prompt"}
    first = client.post(path + "/turns", json=form)
    assert first.status_code == 202, first.text
    assert client.post(path + "/turns", json=form).json() == first.json()
    assert (
        client.post(path + "/turns", json={**form, "text": "changed"}).status_code
        == 409
    )
    assert (
        client.post(
            path + "/turns", json=form, headers={"Origin": "https://invalid.test"}
        ).status_code
        == 403
    )
    tid = first.json()["turn_id"]
    for count, answer, file in [
        (1, "allow_always", "created.txt"),
        (2, "deny", "blocked.txt"),
    ]:
        view = wait_view(
            client,
            path,
            lambda v, expected=count: (
                sum(e["kind"] == "permission" for e in v["events"]) == expected
            ),
        )
        perm = [e["payload"] for e in view["events"] if e["kind"] == "permission"][-1]
        assert not (repo / file).exists()
        assert perm["scope"] == file
        endpoint = "/turns/" + tid + "/permissions/" + perm["permission_id"] + "/reply"
        body = {
            "request_id": str(uuid4()),
            "decision": answer,
            "feedback": "only this call",
        }
        assert client.post(other + endpoint, json=body).status_code == 404
        r = client.post(path + endpoint, json=body)
        assert r.status_code == 200, r.text
        assert client.post(path + endpoint, json=body).json() == r.json()
        assert (
            client.post(path + endpoint, json={**body, "decision": "allow"}).status_code
            == 409
        )
    v = wait_view(client, path, lambda v: v["turns"][0]["ended"])
    assert v["turns"][0]["state"] == "completed"
    calls = {
        e["payload"]["call_id"]: e["payload"]
        for e in v["events"]
        if e["kind"] == "tool_call"
    }
    results = [
        (calls[e["payload"]["call_id"]]["args"]["file_path"], e["payload"]["is_error"])
        for e in v["events"]
        if e["kind"] == "tool_result"
    ]
    ended_reads = [
        calls[e["payload"]["call_id"]]["args"]["file_path"]
        for e in v["events"]
        if e["kind"] == "action_ended"
        and calls[e["payload"]["call_id"]]["tool"] == "ReadFile"
    ]
    assert ended_reads == ["missing.txt", "test.txt"]
    assert dict(results) == {
        "test.txt": False,
        "missing.txt": True,
        "blocked.txt": True,
        "created.txt": False,
    }
    assert (repo / "created.txt").read_text() == "created" and not (
        repo / "blocked.txt"
    ).exists()
    assert any(
        e["kind"] == "action_ended" and e["payload"].get("scope") == "created.txt"
        for e in v["events"]
    )
    assert any(e["kind"] == "thinking" for e in v["events"])
    assert client.post(path + "/turns", json=form).json() == first.json()
    assert (
        client.post(
            path + "/turns",
            json={"submit_request_id": str(uuid4()), "text": "second prompt"},
        ).status_code
        == 202
    )
    v = wait_view(
        client, path, lambda v: len(v["turns"]) == 2 and v["turns"][-1]["ended"]
    )
    assert not any(
        e["kind"] == "thinking" and e["turn_id"] == v["turns"][-1]["turn_id"]
        for e in v["events"]
    )
    user = [m["content"] for m in requests[-1] if m.get("role") == "user"]
    assert (
        sum("first prompt" in str(m) for m in user)
        == sum("second prompt" in str(m) for m in user)
        == 1
    )
    assert "local-demo" not in str(v)
    repo.rename(repo.with_name("removed"))
    assert client.get(path + "/view").json()["session"]["available"] is False
    assert client.post(
        path + "/turns", json={"submit_request_id": str(uuid4()), "text": "after move"}
    ).status_code in (409, 422)


@pytest.mark.asyncio
async def test_writer_supervision_and_external_edits(configured, tmp_path):
    import asyncio
    import json
    import sys
    import time

    import pytest

    from webdemo.coding.manager import TOOLS, Manager
    from webdemo.coding.store import uid

    if sys.platform != "darwin":
        pytest.skip("requires the macOS command sandbox")
    _, repo, store, config = configured
    doc = json.loads(config.read_text())
    doc["permission_mode"] = "bypassPermissions"
    config.write_text(json.dumps(doc))
    alias = tmp_path / "alias"
    alias.symlink_to(repo)
    mgr = Manager(store)

    async def job(path):
        sid = store.create_session(path, "")["session_id"]
        receipt, _ = store.submit(
            sid, uid(), "test", lambda p: profiles.snapshot(p, store.root)
        )
        j, _ = mgr.start_data(sid, receipt["turn_id"])
        await mgr.handle(j, "ready", {})
        return j

    a, b = await job(repo), await job(alias)

    async def action(j, tool, args, aid=None):
        call = uid()
        j.calls[call] = {"tool": tool, "args": args, "call_id": call}
        params = {
            "call_id": call,
            "action_id": aid or uid(),
            "tool": tool,
            "args": TOOLS[tool].params_model.model_validate(args).model_dump(),
        }
        return await mgr.action(j, params)

    (repo / "spawn.py").write_text(
        "import subprocess,sys,os\nfrom pathlib import Path\n"
        "subprocess.Popen([sys.executable,'-c',\"import time;from pathlib import Path;time.sleep(2);Path('child.txt').write_text('ended')\"],start_new_session=True)\n"
        "Path('parent.txt').write_text(str('MODEL_CREDENTIAL_TEST_MARKER' in os.environ))\n"
    )
    task = asyncio.create_task(action(a, "Bash", {"command": "python3 spawn.py"}))
    deadline = time.monotonic() + 12
    while (
        not (repo / "parent.txt").exists()
        and not task.done()
        and time.monotonic() < deadline
    ):
        await asyncio.sleep(0.03)
    assert (repo / "parent.txt").read_text() == "False"
    assert (await action(b, "ReadFile", {"file_path": "test.txt"}))["is_error"] is False
    for tool, args in [
        ("WriteFile", {"file_path": "b.txt", "content": "new"}),
        ("Bash", {"command": "echo unexpected"}),
    ]:
        with pytest.raises(Conflict, match=a.sid):
            await action(b, tool, args)
    assert not (repo / "b.txt").exists()
    assert (await task)["exit_code"] == 0 and (
        repo / "child.txt"
    ).read_text() == "ended"
    assert not (await action(b, "WriteFile", {"file_path": "b.txt", "content": "new"}))[
        "is_error"
    ]
    (repo / "test.txt").write_text("external line\n")
    edit = {"file_path": "test.txt", "old_string": "line", "new_string": "content"}
    assert (await action(b, "EditFile", edit))["is_error"]
    await action(b, "ReadFile", {"file_path": "test.txt"})
    assert not (await action(b, "EditFile", edit))["is_error"]
    assert (repo / "test.txt").read_text() == "external content\n"
    await mgr.handle(b, "finish", {"state": "completed"})
    with store.connection() as db:
        summary = next(e for e in store.events(db, b.sid) if e["kind"] == "summary")
        assert any(
            c["path"] == "test.txt" and c["uncertain"]
            for c in summary["payload"]["changes"]
        )
        assert (
            db.execute(
                "SELECT state FROM leases WHERE tree=?", (a.tree_id,)
            ).fetchone()[0]
            == "free"
        )


def test_hook_has_own_permission_and_lease(configured, monkeypatch):
    import json
    import sys

    from webdemo import mock

    if sys.platform != "darwin":
        pytest.skip("requires the macOS command sandbox")
    client, repo, _store, config = configured
    doc = json.loads(config.read_text())
    doc["hooks"] = [
        {
            "id": "write-audit",
            "event": "pre_tool_use",
            "if": "tool == WriteFile",
            "action": {"type": "command", "command": "echo hook-ran > hook.txt"},
        }
    ]
    config.write_text(json.dumps(doc))
    steps = iter(
        [
            delta(("WriteFile", {"file_path": "main.txt", "content": "main"})),
            delta(content="done"),
        ]
    )
    monkeypatch.setattr(mock, "response", lambda _: next(steps))
    sid = client.post("/api/coding/sessions", json={"directory": str(repo)}).json()[
        "session_id"
    ]
    path = "/api/coding/sessions/" + sid
    tid = client.post(
        path + "/turns", json={"submit_request_id": str(uuid4()), "text": "hook-test"}
    ).json()["turn_id"]
    for n in (1, 2):
        v = wait_view(
            client,
            path,
            lambda v, expected=n: (
                sum(e["kind"] == "permission" for e in v["events"]) == expected
            ),
        )
        wait = [e["payload"] for e in v["events"] if e["kind"] == "permission"][-1]
        call = next(
            e["payload"]
            for e in v["events"]
            if e["kind"] == "tool_call" and e["payload"]["call_id"] == wait["call_id"]
        )
        assert not (repo / "main.txt").exists()
        if n == 1:
            assert not (repo / "hook.txt").exists() and call["hook_id"] == "write-audit"
        else:
            assert (repo / "hook.txt").read_text() == "hook-ran\n" and call[
                "tool"
            ] == "WriteFile"
        r = client.post(
            path + "/turns/" + tid + "/permissions/" + wait["permission_id"] + "/reply",
            json={"request_id": str(uuid4()), "decision": "allow"},
        )
        assert r.status_code == 200, r.text
    v = wait_view(client, path, lambda v: v["turns"][0]["ended"])
    assert (
        v["turns"][0]["state"] == "completed"
        and (repo / "main.txt").read_text() == "main"
    )
    ids = [
        e["payload"]["action_id"] for e in v["events"] if e["kind"] == "action_started"
    ]
    assert len(set(ids)) == len(ids) == 2


@pytest.mark.asyncio
async def test_instance_ack_replay_and_storage_failure(configured, monkeypatch):
    import asyncio
    import json
    import sqlite3

    from webdemo.coding import worker
    from webdemo.coding.manager import TOOLS, Manager
    from webdemo.coding.store import uid

    client, repo, store, config = configured
    doc = json.loads(config.read_text())
    doc["permission_mode"] = "bypassPermissions"
    config.write_text(json.dumps(doc))
    sid = store.create_session(repo, "")["session_id"]
    receipt, _ = store.submit(
        sid, uid(), "run", lambda p: profiles.snapshot(p, store.root)
    )
    tid = receipt["turn_id"]
    mgr = Manager(store)
    job, initial = mgr.start_data(sid, tid)
    mgr.running[tid] = job
    proc = await worker.start_process()
    # 真实管道首次 ready 应答丢失。两个接手帧相同且只记录一次状态。
    await worker.send(proc.stdin, initial)
    try:
        first = json.loads(await asyncio.wait_for(proc.stdout.readline(), 6))
        accepted = await mgr.dispatch(job, first)
        again = json.loads(await asyncio.wait_for(proc.stdout.readline(), 6))
        assert first == again and await mgr.dispatch(job, again) == accepted
        with pytest.raises(ValueError):
            await mgr.dispatch(job, {**first, "instance": uid()})
        with store.connection() as db:
            assert (
                sum(
                    e["payload"].get("state") == "running"
                    for e in store.events(db, sid)
                )
                == 1
            )
    finally:
        proc.terminate()
        await proc.wait()
    args = {"file_path": "single.txt", "content": "one"}
    call, aid = uid(), uid()
    job.calls[call] = {"call_id": call, "args": args, "tool": "WriteFile"}
    action = {
        "call_id": call,
        "action_id": aid,
        "tool": "WriteFile",
        "args": TOOLS["WriteFile"].params_model.model_validate(args).model_dump(),
    }
    with store.connection(True) as db:
        db.execute(
            "CREATE TRIGGER break_intent BEFORE INSERT ON actions BEGIN SELECT RAISE(ABORT,'simulated disk error'); END"
        )
    with pytest.raises(sqlite3.DatabaseError):
        await mgr.action(job, action)
    assert not (repo / "single.txt").exists()
    with store.connection(True) as db:
        db.execute("DROP TRIGGER break_intent")
        assert db.execute("SELECT state FROM leases").fetchone()[0] == "free"
    result = await mgr.action(job, action)
    assert not result["is_error"]
    (repo / "single.txt").write_text("external")
    assert (
        await mgr.action(job, action) == result
        and (repo / "single.txt").read_text() == "external"
    )
    with store.connection() as db:
        assert (
            db.execute("SELECT COUNT(*) FROM actions WHERE id=?", (aid,)).fetchone()[0]
            == 1
        )
    with monkeypatch.context() as m:

        def unavailable(*args, **kwargs):
            raise sqlite3.OperationalError("disk I/O")

        m.setattr(type(store), "connection", unavailable)
        assert client.get("/api/coding/sessions").status_code == 503


def test_live_sessions_are_independent(configured, monkeypatch, tmp_path):
    from threading import Event

    from webdemo import mock
    from webdemo.coding.routes import manager

    client, repo, _store, _config = configured
    other = tmp_path / "separate"
    git(tmp_path, "init", str(other))
    received = Event()
    release = Event()

    def model(req):
        received.set()
        release.wait(8)
        return delta(content="finished")

    monkeypatch.setattr(mock, "response", model)
    jobs = []
    try:
        for d in (repo, other):
            sid = client.post(
                "/api/coding/sessions", json={"directory": str(d)}
            ).json()["session_id"]
            path = "/api/coding/sessions/" + sid
            tid = client.post(
                path + "/turns",
                json={"submit_request_id": str(uuid4()), "text": "independent"},
            ).json()["turn_id"]
            wait_view(client, path, lambda v: v["turns"][0]["state"] == "running")
            jobs.append(manager().running[tid])
        assert received.wait(3)
        assert (
            jobs[0].tree.root != jobs[1].tree.root
            and jobs[0].process.pid != jobs[1].process.pid
        )
        assert jobs[0].instance != jobs[1].instance
    finally:
        release.set()
    for j in jobs:
        assert (
            wait_view(
                client,
                "/api/coding/sessions/" + j.sid,
                lambda v: v["turns"][0]["ended"],
            )["turns"][0]["state"]
            == "completed"
        )


@pytest.mark.asyncio
async def test_policy_at_execution_and_sandbox_unavailable(configured, monkeypatch):
    import asyncio
    import json

    from webdemo.coding import actions
    from webdemo.coding.manager import TOOLS, Manager
    from webdemo.coding.store import uid

    _client, repo, store, config = configured
    doc = json.loads(config.read_text())
    doc["permission_mode"] = "bypassPermissions"
    config.write_text(json.dumps(doc))
    sid = store.create_session(repo, "")["session_id"]
    receipt, _ = store.submit(
        sid, uid(), "test", lambda p: profiles.snapshot(p, store.root)
    )
    mgr = Manager(store)
    job, _ = mgr.start_data(sid, receipt["turn_id"])
    mgr.running[job.tid] = job
    await mgr.handle(job, "ready", {})

    def bind(tool, args):
        call = uid()
        job.calls[call] = {"tool": tool, "args": args, "call_id": call}
        return {
            "tool": tool,
            "args": TOOLS[tool].params_model.model_validate(args).model_dump(),
            "call_id": call,
            "action_id": uid(),
        }

    job.rules.parent.mkdir(parents=True, exist_ok=True)
    job.rules.write_text('[{"effect":"ask","rule":"WriteFile(marker.txt)"}]')
    p = bind("WriteFile", {"file_path": "marker.txt", "content": "approved"})
    task = asyncio.create_task(mgr.action(job, p))
    for _ in range(100):
        if job.waiting:
            break
        await asyncio.sleep(0.02)
    pid = next(iter(job.waiting))
    assert not (repo / "marker.txt").exists()
    mgr.answer(sid, job.tid, pid, uid(), "allow", "")
    assert (
        not (await task)["is_error"] and (repo / "marker.txt").read_text() == "approved"
    )
    job.rules.write_text('[{"effect":"deny","rule":"Bash(pwd)"}]')
    with pytest.raises(ValueError):
        await mgr.action(job, bind("Bash", {"command": "pwd"}))
    with monkeypatch.context() as m:
        m.setattr(actions.sys, "platform", "unsupported")
        denied = await mgr.action(
            job, bind("Bash", {"command": "echo blocked > unwanted.txt"})
        )
        assert denied["is_error"] and "沙箱" in denied["output"]
    with store.connection() as db:
        assert not any(e["kind"] == "process_started" for e in store.events(db, sid))
    assert not (repo / "unwanted.txt").exists()


def test_session_scoped_long_result_and_failed_rule(configured, monkeypatch):
    from mewcode.permissions.rules import RuleEngine
    from webdemo import mock

    client, repo, store, _config = configured
    (repo / "long.txt").write_text("a" * 52000 + "-end-of-long-output")
    sid = client.post("/api/coding/sessions", json={"directory": str(repo)}).json()[
        "session_id"
    ]
    path = "/api/coding/sessions/" + sid
    capture = []

    def response(req):
        capture.append(req["messages"])
        if len(capture) == 1:
            return delta(("ReadFile", {"file_path": "long.txt"}))
        if len(capture) == 2:
            private = next(
                (store.root / "sessions" / sid / "tool-results").glob("*.txt")
            )
            return delta(("ReadFile", {"file_path": str(private)}))
        if len(capture) == 3:
            return delta(("WriteFile", {"file_path": "rule.txt", "content": "ok"}))
        return delta(content="done")

    monkeypatch.setattr(mock, "response", response)

    def failed_rule(*args):
        raise OSError("could not save rule")

    monkeypatch.setattr(RuleEngine, "append_local_rule", failed_rule)
    tid = client.post(
        path + "/turns", json={"submit_request_id": str(uuid4()), "text": "long read"}
    ).json()["turn_id"]
    handled = set()
    import time

    until = time.monotonic() + 20
    while time.monotonic() < until:
        v = client.get(path + "/view").json()
        for event in v["events"]:
            p = event["payload"]
            if event["kind"] == "permission" and p["permission_id"] not in handled:
                answer = "allow_always" if p["scope"] == "rule.txt" else "allow"
                r = client.post(
                    path
                    + "/turns/"
                    + tid
                    + "/permissions/"
                    + p["permission_id"]
                    + "/reply",
                    json={"request_id": str(uuid4()), "decision": answer},
                )
                assert r.status_code == 200
                handled.add(p["permission_id"])
        if v["turns"][0]["ended"]:
            break
        time.sleep(0.04)
    assert v["turns"][0]["state"] == "completed"
    assert len(capture) == 4 and "-end-of-long-output" in str(capture[2][-1])
    assert not any(
        e["kind"] == "action_ended" and e["payload"].get("scope") for e in v["events"]
    )
    assert not (repo / ".mewcode").exists()


def test_lost_ready_ack_runs_model_once(configured, monkeypatch):
    from webdemo import mock
    from webdemo.coding import worker

    client, repo, _store, _config = configured
    calls = []
    monkeypatch.setattr(
        mock, "response", lambda req: calls.append(req["model"]) or delta(content="ok")
    )
    send = worker.send
    dropped = []

    async def drop_once(stream, message):
        if not dropped and message == {"seq": 1, "result": True}:
            dropped.append(message)
            return
        await send(stream, message)

    monkeypatch.setattr(worker, "send", drop_once)
    sid = client.post("/api/coding/sessions", json={"directory": str(repo)}).json()[
        "session_id"
    ]
    path = "/api/coding/sessions/" + sid
    client.post(
        path + "/turns", json={"submit_request_id": str(uuid4()), "text": "once"}
    ).raise_for_status()
    view = wait_view(client, path, lambda v: v["turns"][0]["ended"])
    assert view["turns"][0]["state"] == "completed" and len(dropped) == len(calls) == 1
    final = [e for e in view["events"] if e["kind"] == "message_final"]
    assert len(final) == 1
    assert final[0]["payload"]["message_id"] == next(
        e["payload"]["message_id"] for e in view["events"] if e["kind"] == "text"
    )


@pytest.mark.parametrize("overlap", ["git-alias", "parent", "child"])
def test_alias_competition_through_http(configured, monkeypatch, tmp_path, overlap):
    import json
    import sys
    import time
    from collections import Counter

    from webdemo import mock

    if sys.platform != "darwin":
        pytest.skip("requires the macOS command sandbox")
    client, repo, _store, config = configured
    doc = json.loads(config.read_text())
    doc["permission_mode"] = "bypassPermissions"
    config.write_text(json.dumps(doc))
    if overlap == "git-alias":
        alias = tmp_path / "alias"
        alias.symlink_to(repo)
    else:
        parent = tmp_path / "plain"
        child = parent / "child"
        child.mkdir(parents=True)
        repo, alias = (parent, child) if overlap == "parent" else (child, parent)
        (alias / "test.txt").write_text("read while busy")
    other = tmp_path / "other"
    git(tmp_path, "init", str(other))
    (repo / "hold.py").write_text(
        "from pathlib import Path\nimport time\nPath('started').touch()\n"
        "for _ in range(400):\n    if Path('release').exists():break\n    time.sleep(.05)\n"
    )
    counter = Counter()

    def model(req):
        label = next(
            key
            for key in ("retry-B", "writer-C", "reader-B", "writer-A")
            if key in str(req["messages"])
        )
        counter[label] += 1
        steps = {
            "writer-A": [("Bash", {"command": "python3 hold.py"})],
            "reader-B": [
                ("ReadFile", {"file_path": "test.txt"}),
                ("WriteFile", {"file_path": "blocked", "content": "no"}),
                ("Bash", {"command": "echo forbidden > blocked"}),
            ],
            "writer-C": [("WriteFile", {"file_path": "independent", "content": "yes"})],
            "retry-B": [("WriteFile", {"file_path": "allowed", "content": "once"})],
        }
        pos = counter[label] - 1
        return (
            delta(steps[label][pos])
            if pos < len(steps[label])
            else delta(content="done")
        )

    monkeypatch.setattr(mock, "response", model)
    paths = []
    for dir in (repo, alias, other):
        s = client.post("/api/coding/sessions", json={"directory": str(dir)}).json()
        paths.append("/api/coding/sessions/" + s["session_id"])
    a, b, c = paths

    def submit(path, prompt):
        r = client.post(
            path + "/turns", json={"submit_request_id": str(uuid4()), "text": prompt}
        )
        assert r.status_code == 202, r.text

    submit(a, "writer-A")
    try:
        deadline = time.monotonic() + 10
        while not (repo / "started").exists() and time.monotonic() < deadline:
            time.sleep(0.03)
        assert (repo / "started").exists()
        submit(b, "reader-B")
        submit(c, "writer-C")
        busy = wait_view(client, b, lambda v: v["turns"][0]["ended"])
        wait_view(client, c, lambda v: v["turns"][0]["ended"])
        results = [e["payload"] for e in busy["events"] if e["kind"] == "tool_result"]
        assert (
            len(results) == 3
            and not results[0]["is_error"]
            and all(r["is_error"] for r in results[1:])
        )
        conflicts = [
            e["payload"]["holder_session_id"]
            for e in busy["events"]
            if e["kind"] == "write_conflict"
        ]
        assert conflicts == [a.rsplit("/", 1)[1]] * 2
        assert (
            not (alias / "blocked").exists()
            and (other / "independent").read_text() == "yes"
        )
    finally:
        (repo / "release").touch()
    wait_view(client, a, lambda v: v["turns"][0]["ended"])
    submit(b, "retry-B")
    wait_view(client, b, lambda v: len(v["turns"]) == 2 and v["turns"][-1]["ended"])
    assert (alias / "allowed").read_text() == "once"


def test_git_evidence_and_mixed_edits(configured, monkeypatch):
    import json
    import sys

    from webdemo import mock

    if sys.platform != "darwin":
        pytest.skip("requires the macOS command sandbox")
    client, repo, _store, config = configured
    doc = json.loads(config.read_text())
    doc["permission_mode"] = "bypassPermissions"
    config.write_text(json.dumps(doc))
    (repo / "obsolete.txt").write_text("remove me\n")
    git(repo, "add", "obsolete.txt")
    git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@invalid",
        "commit",
        "-qm",
        "obsolete",
    )
    (repo / "test.txt").write_text("index text\n")
    git(repo, "add", "test.txt")
    (repo / "test.txt").write_text("working text\n")
    steps = iter(
        [
            delta(("ReadFile", {"file_path": "test.txt"})),
            delta(
                (
                    "EditFile",
                    {
                        "file_path": "test.txt",
                        "old_string": "working",
                        "new_string": "edited",
                    },
                )
            ),
            delta(
                ("WriteFile", {"file_path": "new.txt", "content": "untracked needle\n"})
            ),
            delta(
                (
                    "Bash",
                    {
                        "command": "python3 -c \"from pathlib import Path; Path('obsolete.txt').unlink()\""
                    },
                )
            ),
        ]
    )

    def model(req):
        response = next(steps, None)
        if response:
            return response
        (repo / "test.txt").write_text("edited text\nexternal text\n")
        return delta(content="finished")

    monkeypatch.setattr(mock, "response", model)
    sid = client.post("/api/coding/sessions", json={"directory": str(repo)}).json()[
        "session_id"
    ]
    path = "/api/coding/sessions/" + sid
    client.post(
        path + "/turns", json={"submit_request_id": str(uuid4()), "text": "git changes"}
    ).raise_for_status()
    v = wait_view(client, path, lambda v: v["turns"][0]["ended"])
    assert v["turns"][0]["state"] == "completed"
    changes = {r["path"]: r["status"] for r in client.get(path + "/diff").json()}
    actual = {
        line[3:]: line[:2]
        for line in git(repo, "status", "--short").stdout.decode().splitlines()
    }
    assert (
        changes == actual == {"test.txt": "MM", "obsolete.txt": " D", "new.txt": "??"}
    )
    patch = client.get(path + "/diff", params={"path": "test.txt"}).json()[0]
    for key, flags in [("staged", ["--cached"]), ("working", [])]:
        native = (
            git(repo, "diff", *flags, "--", "test.txt")
            .stdout.decode()
            .split("@@", 1)[1]
        )
        assert patch[key].split("@@", 1)[1] == native
    assert client.get(
        path + "/files", params={"query": "needle", "content": "true"}
    ).json() == ["new.txt"]
    assert (
        client.get(path + "/file", params={"path": "new.txt"})
        .json()["content"]
        .encode()
        == (repo / "new.txt").read_bytes()
    )
    summary = next(e["payload"] for e in v["events"] if e["kind"] == "summary")
    attribution = {c["path"]: c["uncertain"] for c in summary["changes"]}
    assert attribution == {"test.txt": True, "obsolete.txt": True, "new.txt": False}
    calls = {e["payload"]["call_id"] for e in v["events"] if e["kind"] == "tool_call"}
    assert all(c["call_id"] in calls for c in summary["changes"])


def test_cancel_competes_with_finish_and_prelaunch(local):
    from webdemo.coding.manager import Manager

    repo, store = local
    sid = store.create_session(str(repo), "")["session_id"]
    manager = Manager(store)
    for index in range(12):
        row, _ = store.submit(sid, uuid4(), "race", lambda _: {"model": "race"})
        tid = row["turn_id"]
        request = uuid4()
        if not index:
            receipt = manager.cancel(sid, tid, request)
            assert receipt["state"] == "cancelling"
            with pytest.raises(Conflict):
                manager.start_data(sid, tid)
        with ThreadPoolExecutor(2) as pool:
            a = pool.submit(manager.cancel, sid, tid, request)
            b = pool.submit(manager.finalize, sid, tid, "completed", None)
            reply = a.result()
            b.result()
        assert manager.cancel(sid, tid, request) == reply
        with store.connection() as db:
            events = store.events(db, sid)
            states = [
                e["payload"]["state"]
                for e in events
                if e["turn_id"] == tid and e["kind"] == "state"
            ]
            assert states in (
                ["accepted", "completed"],
                ["accepted", "cancelling", "cancelled"],
            )
            assert (
                db.execute(
                    "SELECT count(*) FROM actions WHERE turn=?", (tid,)
                ).fetchone()[0]
                == 0
            )


def test_cancel_pending_permission_is_scoped(configured, monkeypatch):
    from webdemo import mock
    from webdemo.tests.coding_fixture import allow, finished, post, turn, wait_for

    client, repo, _store, _ = configured

    def model(req):
        if any(m["role"] == "tool" for m in req["messages"]):
            return delta(content="done")
        return delta(("WriteFile", {"file_path": "only-once.txt", "content": "one"}))

    monkeypatch.setattr(mock, "response", model)
    paths = [
        "/api/coding/sessions/"
        + post(client, "/api/coding/sessions", directory=str(repo))["session_id"]
        for _ in range(2)
    ]
    turns = [turn(client, p, "write")["turn_id"] for p in paths]
    views = [wait_for(client, p, lambda v: v["permissions"]) for p in paths]
    p = views[0]["permissions"][0]
    req = {"request_id": str(uuid4())}
    cancel = paths[0] + "/turns/" + turns[0] + "/cancel"
    first = client.post(cancel, json=req).json()
    assert first == client.post(cancel, json=req).json()
    assert finished(client, paths[0], turns[0])["turns"][-1]["state"] == "cancelled"
    assert (
        client.post(
            paths[0]
            + "/turns/"
            + turns[0]
            + "/permissions/"
            + p["permission_id"]
            + "/reply",
            json={"request_id": str(uuid4()), "decision": "allow"},
        ).status_code
        == 409
    )
    assert not (repo / "only-once.txt").exists()
    allow(client, paths[1], views[1])
    assert finished(client, paths[1], turns[1])["turns"][-1]["state"] == "completed"
    assert (repo / "only-once.txt").read_text() == "one"


def test_provider_revision_revoke_and_demo(configured, monkeypatch):
    import json

    from webdemo import mock
    from webdemo.tests.coding_fixture import allow, finished, post, turn, wait_for

    client, repo, _store, config = configured
    native = json.loads(config.read_text())["providers"][0]
    base = "/api/coding"
    path = base + "/profiles"
    form = {
        "provider": "my-local",
        "protocol": "openai-compat",
        "endpoint": native["base_url"],
        "model": "original",
        "api_key": "local-demo",
    }
    first = post(client, path, **form)
    other = post(client, path, **dict(form, model="separate"))
    assert first["profile_id"] != other["profile_id"]
    assert "local-demo" not in client.get(path).text
    assert (
        "local-demo" not in client.post(path, json=dict(form, extra="local-demo")).text
    )
    sid = post(
        client, base + "/sessions", directory=str(repo), profile_id=first["profile_id"]
    )["session_id"]
    session = base + "/sessions/" + sid
    seen = []

    def model(req):
        seen.append(req["model"])
        if req["messages"][-1]["role"] == "tool" or "simple" in str(
            req["messages"][-1]
        ):
            return delta(content="ok")
        return delta(("Bash", {"command": "printf accepted"}))

    monkeypatch.setattr(mock, "response", model)
    t1 = turn(client, session, "call command")
    pending = wait_for(client, session, lambda v: v["permissions"])
    revision = post(client, path, **dict(form, model="updated", **first))
    assert revision["revision"] == 2
    assert client.post(path, json=dict(form, **first)).status_code == 409
    allow(client, session, pending)
    assert finished(client, session, t1["turn_id"])["turns"][-1]["state"] == "completed"
    assert seen == ["original", "original"]
    t2 = turn(client, session, "simple")
    assert finished(client, session, t2["turn_id"])["turns"][-1]["state"] == "completed"
    assert seen[-1] == "updated" and t1["snapshot_id"] != t2["snapshot_id"]
    t3 = turn(client, session, "call command again")
    v = wait_for(client, session, lambda v: v["permissions"])
    post(client, path + "/" + first["profile_id"] + "/revoke")
    allow(client, session, v)
    v = finished(client, session, t3["turn_id"])
    assert v["turns"][-1]["state"] == "failed"
    assert not any(
        e["kind"] == "process_started" and e["turn_id"] == t3["turn_id"]
        for e in v["events"]
    )
    post(client, session + "/settings", profile_id="demo:local")
    demo = turn(client, session, "simple demo")
    v = finished(client, session, demo["turn_id"])
    assert "local-demo" not in json.dumps(v)
    assert any(e["payload"].get("source") == "demo" for e in v["events"])
    assert any(
        "local demonstration" in e["payload"].get("text", "") for e in v["events"]
    )


def test_storage_failure_stops_dispatch_and_keeps_receipt(configured, monkeypatch):
    from webdemo import mock
    from webdemo.tests.coding_fixture import finished, post, wait_for

    client, repo, store, _ = configured
    monkeypatch.setattr(
        mock,
        "response",
        lambda _: delta(("Bash", {"command": "printf x >> count.txt"})),
    )
    sid = post(client, "/api/coding/sessions", directory=str(repo))["session_id"]
    path = "/api/coding/sessions/" + sid
    data = {"submit_request_id": str(uuid4()), "text": "write once"}
    receipt = post(client, path + "/turns", **data)
    v = wait_for(client, path, lambda v: v["permissions"])
    with store.connection(True) as db:
        db.execute(
            "CREATE TRIGGER reject_action BEFORE INSERT ON actions BEGIN SELECT RAISE(ABORT,'storage unavailable'); END"
        )
    from webdemo.tests.coding_fixture import allow

    allow(client, path, v)
    final = finished(client, path, receipt["turn_id"])
    assert final["turns"][-1]["state"] == "failed"
    assert any(e["payload"].get("error_type") == "storage" for e in final["events"])
    assert not (repo / "count.txt").exists()
    with store.connection(True) as db:
        db.execute("DROP TRIGGER reject_action")
    assert post(client, path + "/turns", **data) == receipt
    assert len(client.get(path + "/view").json()["turns"]) == 1


def test_session_bound_extensions_and_disconnect(configured, monkeypatch):
    import json
    import shutil
    import sys
    from pathlib import Path

    from mewcode.mcp.tool_wrapper import build_mcp_tool_name
    from webdemo import mock
    from webdemo.tests.coding_fixture import allow, finished, post, turn, wait_for

    client, repo, _store, config = configured
    src = Path(__file__).with_name("mcp_stdio_fixture.py")
    shutil.copyfile(src, repo / "stamp.py")
    skill = repo / ".mewcode/skills/skill-proof.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: skill-proof\ndescription: testing fixture\n---\nSKILL-BODY-819"
    )
    (skill.parent / "bad.md").write_text("---\n: [invalid YAML\n---\n")
    doc = json.loads(config.read_text())
    monkeypatch.setenv("EXTENSION_FIXTURE_REFERENCE", "declared-value-82")
    doc["mcp_servers"] = [
        {
            "name": "fixture",
            "command": str(Path(sys.base_prefix) / "bin/python3"),
            "env": {"MCP_FIXTURE_KEY": "${EXTENSION_FIXTURE_REFERENCE}"},
            "args": ["-B", "stamp.py", "--check-env"],
        },
        {"name": "missing", "command": "/missing/fixture"},
    ]
    doc["hooks"] = [
        {
            "id": "audit",
            "event": "post_tool_use",
            "if": "tool == ReadFile",
            "action": {"type": "command", "command": "printf HOOK-725", "timeout": 8},
        }
    ]
    doc["mcp_servers"].append(
        {
            "name": "protected",
            "command": "python3",
            "env": {"UNRELATED": "${MODEL_CREDENTIAL_TEST_MARKER}"},
        }
    )
    config.write_text(json.dumps(doc))
    base = "/api/coding/sessions"
    a, b = [
        base + "/" + post(client, base, directory=str(repo))["session_id"]
        for _ in range(2)
    ]
    options = client.get(a + "/settings").json()
    assert "skill-proof" in options["catalog"]["skills"] and options["errors"]
    name = build_mcp_tool_name("fixture", "stamp")
    probe = client.post(a + "/mcp/fixture/connect", json={})
    assert probe.is_success, probe.text
    assert probe.json()["tools"] == [name] and not (repo / "mcp-count.txt").exists()
    assert client.post(a + "/mcp/missing/connect", json={}).status_code == 422
    post(
        client,
        a + "/settings",
        skills=["skill-proof"],
        mcp=["fixture"],
        hooks=["audit"],
    )
    post(client, b + "/settings", skills=[], mcp=[], hooks=[])
    observed = []

    def model(req):
        tools = [x["function"]["name"] for x in req["tools"]]
        marked = "SKILL-BODY-819" in str(req["messages"])
        observed.append((name in tools, marked))
        if name in tools and not any(x["role"] == "tool" for x in req["messages"]):
            return delta((name, {}))
        if req["messages"][-1]["role"] == "user":
            return delta(("ReadFile", {"file_path": "test.txt"}))
        return delta(content="done")

    monkeypatch.setattr(mock, "response", model)
    t = turn(client, a, "use stamp")
    pending = wait_for(client, a, lambda v: v["permissions"] or v["turns"][-1]["ended"])
    assert pending["permissions"], pending["events"][-6:]
    allow(client, a, pending)
    v = finished(client, a, t["turn_id"])
    assert (repo / "mcp-count.txt").read_text() == "x" and "MCP-SESSION-MARK-42" in str(
        v["events"]
    )
    assert all(x == (True, True) for x in observed)
    # 独立会话无扩展。原会话显式断开 MCP 后保留自己的 Hook。
    t = turn(client, b, "read my file")
    other = finished(client, b, t["turn_id"])
    assert observed[-1] == (False, False) and "HOOK-725" not in str(other["events"])
    forbidden = client.post(a + "/mcp/protected/connect", json={})
    assert forbidden.status_code == 422 and "local-demo" not in forbidden.text
    post(client, a + "/settings", skills=[], mcp=[], hooks=["audit"])
    assert (
        client.get(a + "/settings").json()["connections"]["fixture"]["status"]
        == "disconnected"
    )
    t = turn(client, a, "read my file")
    pending = wait_for(client, a, lambda v: v["permissions"] or v["turns"][-1]["ended"])
    assert pending["permissions"]
    allow(client, a, pending)
    done = finished(client, a, t["turn_id"])
    events = [e for e in done["events"] if e["turn_id"] == t["turn_id"]]
    hook = next(
        e["payload"]
        for e in events
        if e["kind"] == "tool_call" and e["payload"].get("hook_id") == "audit"
    )
    assert any(
        e["payload"].get("call_id") == hook["call_id"]
        and e["kind"] == "tool_result"
        and "HOOK-725" in e["payload"]["output"]
        for e in events
    )
    assert (
        observed[-1] == (False, False) and (repo / "mcp-count.txt").read_text() == "x"
    )


def test_cancel_drains_only_owned_command(configured, monkeypatch, tmp_path):
    import asyncio
    import json
    import time
    from threading import Event

    from webdemo import mock
    from webdemo.coding import actions
    from webdemo.tests.coding_fixture import finished, post, turn

    client, repo, store, config = configured
    raw = json.loads(config.read_text())
    raw["permission_mode"] = "bypassPermissions"
    config.write_text(json.dumps(raw))
    other = tmp_path / "another"
    other.mkdir()
    git(other, "init", "-q")
    (other / "tick.py").write_text(
        "import time\nfrom pathlib import Path\nwhile True:\n Path('tick').write_text(str(time.time()))\n time.sleep(.15)\n"
    )

    def model(req):
        messages = req["messages"]
        if messages[-1]["role"] == "tool":
            return delta(content="finished")
        user = next(
            m["content"]
            for m in reversed(messages)
            if m["role"] == "user"
            and m["content"] in ("owner", "parallel", "competitor", "handoff")
        )
        if user in ("competitor", "handoff"):
            return delta(("WriteFile", {"file_path": user + ".txt", "content": "x"}))
        return delta(
            (
                "Bash",
                {
                    "command": "printf yes > prior.txt; sleep 600"
                    if user == "owner"
                    else "python3 tick.py",
                    "timeout": 700,
                },
            )
        )

    monkeypatch.setattr(mock, "response", model)
    proven, release = Event(), Event()
    original = actions.run_shell

    async def delayed_proof(tree, cwd, command, timeout, state, notify, **kwargs):
        async def append(kind, value):
            if kind == "process_exited" and "prior.txt" in command:
                proven.set()
                await asyncio.to_thread(release.wait, 12)
            await notify(kind, value)

        return await original(tree, cwd, command, timeout, state, append, **kwargs)

    monkeypatch.setattr(actions, "run_shell", delayed_proof)
    base = "/api/coding/sessions"
    owner, competitor, parallel = [
        base + "/" + post(client, base, directory=str(d))["session_id"]
        for d in (repo, repo, other)
    ]
    a = turn(client, owner, "owner")["turn_id"]
    c = turn(client, parallel, "parallel")["turn_id"]
    for target in (repo / "prior.txt", other / "tick"):
        end = time.monotonic() + 15
        while not target.is_file() and time.monotonic() < end:
            time.sleep(0.05)
        assert target.is_file()
    try:
        post(client, owner + "/turns/" + a + "/cancel", request_id=str(uuid4()))
        assert proven.wait(6)
        wait_view(
            client,
            owner,
            lambda v: any(e["source_id"] == "delayed-cleanup" for e in v["events"]),
        )
        assert client.get(owner + "/view").json()["turns"][-1]["state"] == "cancelling"
        assert (
            client.get(competitor + "/file", params={"path": "prior.txt"}).json()[
                "content"
            ]
            == "yes"
        )
        with store.connection() as db:
            assert (
                db.execute("SELECT state FROM leases WHERE turn=?", (a,)).fetchone()[0]
                == "draining"
            )
        tick = (other / "tick").read_text()
        blocked = finished(
            client, competitor, turn(client, competitor, "competitor")["turn_id"]
        )
        assert (
            any(e["kind"] == "write_conflict" for e in blocked["events"])
            and not (repo / "competitor.txt").exists()
        )
        assert (other / "tick").read_text() != tick
    finally:
        release.set()
        post(client, parallel + "/turns/" + c + "/cancel", request_id=str(uuid4()))
    assert finished(client, parallel, c)["turns"][-1]["state"] == "cancelled"
    assert finished(client, owner, a)["turns"][-1]["state"] == "cancelled"
    finished(client, competitor, turn(client, competitor, "handoff")["turn_id"])
    assert (repo / "handoff.txt").read_text() == "x"


def test_skills_exclude_external_and_linked_sources(local, tmp_path):
    import os

    repo, _ = local
    root = repo / ".mewcode/skills"
    root.mkdir(parents=True)
    outside = tmp_path / "sentinel.md"
    outside.write_text(
        "---\nname: private-note\ndescription: should stay private\n---\nPROTECTED-CONTENT-942"
    )
    (root / "linked.md").symlink_to(outside)
    os.link(outside, root / "hard.md")
    catalog = profiles.extension_catalog(repo)
    assert "PROTECTED-CONTENT-942" not in str(catalog)
    assert any("hard.md" in e for e in catalog["errors"]) and any(
        "linked.md" in e for e in catalog["errors"]
    )


def test_credentials_excluded_from_shell_test_and_transcript(configured, monkeypatch):
    import json

    from webdemo import mock
    from webdemo.tests.coding_fixture import finished, post, turn

    client, repo, store, cfg = configured
    raw = json.loads(cfg.read_text())
    raw["permission_mode"] = "bypassPermissions"
    cfg.write_text(json.dumps(raw))
    (repo / "test_env.py").write_text(
        "import os,unittest\nclass Environment(unittest.TestCase):\n def test_no_model_key(self):self.assertNotIn('MODEL_CREDENTIAL_TEST_MARKER',os.environ)\n"
    )
    command = 'test -z "${MODEL_CREDENTIAL_TEST_MARKER+x}" && python3 -m unittest'
    replies = iter([delta(("Bash", {"command": command})), delta(content="local-demo")])
    monkeypatch.setattr(mock, "response", lambda _: next(replies))
    base = "/api/coding/sessions"
    sid = post(client, base, directory=str(repo))["session_id"]
    path = base + "/" + sid
    tid = turn(client, path, "check isolated shell and test")["turn_id"]
    v = finished(client, path, tid)
    assert v["turns"][-1]["state"] == "completed"
    assert any(
        e["kind"] == "process_exited" and e["payload"]["exit_code"] == 0
        for e in v["events"]
    )
    assert any("Ran 1 test" in e["payload"].get("text", "") for e in v["events"])
    assert "[redacted]" in str(v["events"]) and "local-demo" not in str(v)
    assert (
        "local-demo"
        not in (store.root / "sessions" / sid / "history.jsonl").read_text()
    )
