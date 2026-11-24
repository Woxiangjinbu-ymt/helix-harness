"""Exercise a disposable backend, live SSE reconnect, a lost parent, and sandbox failure."""

import argparse
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return str(s.getsockname()[1])


def start(env, port, client):
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "webdemo.api:app",
            "--host",
            "127.0.0.1",
            "--port",
            port,
            "--no-access-log",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(100):
        if proc.poll() is not None:
            raise RuntimeError("Test server failed to start")
        try:
            if client.get("/openapi.json").is_success:
                return proc
        except httpx.ConnectError:
            pass
        time.sleep(0.1)
    proc.terminate()
    proc.wait()
    raise RuntimeError("Test server did not become ready")


def stream(client, path, after=0):
    with client.stream("GET", path, headers={"Last-Event-ID": str(after)}) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line.startswith("data: "):
                yield json.loads(line[6:])


def run():
    port = free_port()
    with (
        tempfile.TemporaryDirectory(prefix="helix-recovery-") as tmp,
        httpx.Client(
            base_url="http://127.0.0.1:" + port,
            headers={"X-RSI-Client": "console"},
            timeout=120,
        ) as c,
    ):
        env = {**os.environ, "RSI_WEB_HOME": tmp, "CODING_WEB_HOME": str(Path(tmp)/"coding"), "RSI_MOCK_PORT": free_port()}
        server = start(env, port, c)
        try:

            def task():
                r = c.post("/api/tasks/run", json={"repo": "page-settings"})
                r.raise_for_status()
                return "/api/tasks/" + r.json()["id"]

            path = task()
            events = stream(c, path + "/events")
            first = next(events)
            events.close()
            rest = list(stream(c, path + "/events", first["event_id"]))
            assert rest and all(e["event_id"] > first["event_id"] for e in rest)
            ids = [e["event_id"] for e in [first, *rest]]
            assert ids == sorted(set(ids))
            assert rest[-1]["event_type"] == "run_completed"
            intact = c.get(path).json()["receipt"]
            assert intact["score"] == 100
            replay = c.post(
                "/api/evaluations/run", json={"task_id": path.rsplit("/", 1)[1]}
            )
            replay.raise_for_status()
            replay_path = "/api/evaluations/" + replay.json()["id"]
            assert (
                list(stream(c, replay_path + "/events"))[-1]["event_type"]
                == "run_completed"
            )
            assert c.get(replay_path).json()["receipt"] == intact
            # Lose the parent during a real trial. The worker watches its stdin for EOF.
            evo = c.post("/api/evolution/start", json={})
            evo.raise_for_status()
            active = "/api/evolution/" + evo.json()["id"]
            tail = stream(c, active + "/events")
            for event in tail:
                if event["event_type"] == "started":
                    break
            tail.close()
            server.kill()
            server.wait()
            server = start(
                {**env, "DOCKER_HOST": "unix:///no-such-rsi-docker.sock"}, port, c
            )
            assert c.get(path).json()["receipt"] == intact
            assert c.get(active).json()["status"] in {"cancelled", "interrupted"}
            bad = task()
            assert list(stream(c, bad + "/events"))[-1]["event_type"] == "run_failed"
            assert c.get(bad).json()["receipt"] is None
            assert (
                c.post(
                    "/api/tasks/run", json={"repo": "../evaluation/private"}
                ).status_code
                == 404
            )
            assert c.get("/api/repositories/not-a-repo/files").status_code == 404
            assert (
                c.post(
                    "/api/tasks/run", json={"repo": "page-settings", "turns": 100}
                ).status_code
                == 422
            )
            assert (
                c.post(
                    "/api/tasks/run",
                    json={"repo": "page-settings"},
                    headers={"Origin": "https://outside.invalid"},
                ).status_code
                == 403
            )
            print(
                "PASS live reconnect, identical sealed replay, process loss, historical recovery, sandbox failure and access restrictions"
            )
        finally:
            if server.poll() is None:
                server.terminate()
            server.wait(timeout=100)


def inventory(folder):
    files = {}
    for entry in sorted(folder.rglob("*")):
        if entry.is_symlink():
            raise ValueError(f"验收材料包含链接：{entry}")
        if entry.is_file():
            files[entry.relative_to(folder).as_posix()] = hashlib.sha256(entry.read_bytes()).hexdigest()
    return files


def fingerprint(value):
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def copy_baseline(target):
    from webdemo.store import ARCHIVE, HISTORY, HOME
    archive = target / "archive"
    for name in HISTORY:
        source = ARCHIVE / name
        if not source.is_dir():
            continue
        original = inventory(source)
        copy = archive / name
        shutil.copytree(source, copy, symlinks=True)
        if original != inventory(copy) or original != inventory(source):
            raise ValueError(f"归档在复制期间发生变化：{name}")
    if not list(archive.iterdir()):
        raise ValueError("没有可供验收的历史归档")

    native = target / "native" / ".mewcode" / "sessions"
    native.mkdir(parents=True)
    for log in sorted((ROOT / ".mewcode/sessions").glob("*.jsonl")):
        meta = log.with_suffix(".meta")
        if log.stat().st_size and meta.is_file():
            shutil.copy2(log, native)
            shutil.copy2(meta, native)
            if log.with_suffix("").is_dir():
                shutil.copytree(log.with_suffix(""), native / log.stem, symlinks=True)
    if not list(native.glob("*.jsonl")):
        raise ValueError("没有可恢复的原生会话")
    legacy = target / "web"
    legacy.mkdir()
    src = HOME / "console.sqlite"
    with closing(sqlite3.connect(src.as_uri() + "?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(legacy / src.name)) as dest:
            source.backup(dest)
    shutil.copytree(HOME / "studies", legacy / "studies", symlinks=True)
    for root in (archive, native, legacy):
        for f in sorted(root.rglob("*"), reverse=True):
            if f.is_symlink():
                raise ValueError(f"验收材料包含链接：{f}")
            f.chmod(0o500 if f.is_dir() else 0o400)
        root.chmod(0o500)


def observe_baseline(target):
    from dataclasses import asdict

    from mewcode.memory.session import SessionManager
    from mewcode.rsi.evaluator import verified_release

    with tempfile.TemporaryDirectory(prefix="mewcode-compat-") as temp:
        work = Path(temp)
        shutil.copytree(target / "native", work / "native")
        shutil.copytree(target / "web", work / "web")
        for tree in (work / "native", work / "web"):
            for f in (tree, *tree.rglob("*")):
                f.chmod(0o700 if f.is_dir() else 0o600)
        manager = SessionManager(str(work / "native"))
        sessions = {}
        for meta in manager.list():
            restored = manager.resume(meta.id)
            if restored is None or not restored.messages:
                raise ValueError(f"无法恢复会话 {meta.id}")
            try:
                sessions[meta.id] = [fingerprint(asdict(msg)) for msg in restored.messages]
            finally:
                restored.session.close()
        env = dict(os.environ, RSI_ARCHIVE=str(target / "archive"), RSI_WEB_HOME=str(work / "web"),
                   RSI_MOCK_PORT=free_port(), PYTHONDONTWRITEBYTECODE="1")
        port = free_port()
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30) as client:
            server = start(env, port, client)
            try:
                def get(path):
                    response = client.get("/api/" + path)
                    response.raise_for_status()
                    return response.json()

                archived = {p.name for p in (target / "archive").iterdir()}
                runs = [row for row in get("tasks") if row.get("study") in archived]
                versions = [v for v in get("versions") if v.get("study") in archived]
                jobs = [r for r in get("tasks") if r.get("job") and r.get("score") is not None]
                if not runs or not jobs or not versions:
                    raise ValueError("归档、版本或旧作业样本缺失")
                samples = {}
                for row in [*runs, jobs[0]]:
                    key = row.get("job") or row["id"]
                    item = get("tasks/" + key)
                    samples[key] = {k: item.get(k) for k in ("id", "mode", "model", "version", "score")}
                    samples[key].update(trace=fingerprint(item["events"]), receipt=fingerprint(item["receipt"]),
                                        files=fingerprint(item["files"]))
                versions = [{k: v.get(k) for k in ("id", "mode", "parent", "version", "digest", "score",
                                                    "decision", "active")} for v in versions]
            finally:
                server.terminate()
                server.wait(timeout=30)
    release, judge = verified_release()
    return dict(native_sessions=sessions, archive_files=inventory(target / "archive"),
                native_files=inventory(target / "native"), legacy_job=jobs[0]["job"],
                runs=samples, versions=sorted(versions, key=lambda v: v["id"]),
                release=release, judge_digest=judge)


def baseline(target, create):
    sys.path.insert(0, str(ROOT))
    target = target.expanduser().resolve()
    if create:
        if target.exists() or target.is_relative_to(ROOT):
            raise ValueError("基线须保存到仓库外的新目录")
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".baseline-", dir=target.parent) as tmp:
            staging = Path(tmp) / "data"
            staging.mkdir(mode=0o700)
            copy_baseline(staging)
            before = inventory(staging / "archive")
            result = observe_baseline(staging)
            if before != result["archive_files"]:
                raise ValueError("读取改变了归档")
            (staging / "baseline.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
            for entry in staging.rglob("*"):
                entry.chmod(0o500 if entry.is_dir() else 0o400)
            staging.rename(target)
            target.chmod(0o500)
    else:
        expected = json.loads((target / "baseline.json").read_text())
        actual = observe_baseline(target)
        if expected != json.loads(json.dumps(actual)):
            raise ValueError("兼容性基线不匹配：" + ", ".join(k for k in expected if expected[k] != actual.get(k)))
    print("PASS", target / "baseline.json")


def coding():
    from webdemo.tests.coding_fixture import (
        allow,
        finished,
        post,
        service,
        turn,
        wait_for,
    )

    controls = {}
    requests = []

    def respond(req, delta):
        messages = req["messages"]
        requests.append(messages)
        tail = str(messages[-1]["content"])
        if "Please provide your summary" in tail:
            assert "EARLY-MARKER" in str(messages)
            return delta(content="<summary>EARLY-SUMMARY-17</summary>")
        if "count then sleep" in tail:
            return delta(
                (
                    "Bash",
                    {"command": "printf x >> count.txt; sleep 600", "timeout": 700},
                )
            )
        if "recent details" in tail:
            return delta(content="EARLY-SUMMARY-17 / RECENT-DETAIL-23")
        if "read my file" in tail:
            return delta(("ReadFile", {"file_path": "fact.txt"}))
        return delta(content="received")

    with service("coding-recovery", respond, controls) as (work, repo, _url, c):
        (repo / "fact.txt").write_text("tool evidence")
        sid = post(c, "sessions", directory=str(repo))["session_id"]
        path = "sessions/" + sid
        completed = turn(c, path, "read my file")["turn_id"]
        finished(c, path, completed)
        tid = turn(c, path, "count then sleep")["turn_id"]
        allow(c, path, wait_for(c, path, lambda v: v["permissions"]))
        limit = time.monotonic() + 15
        while not (repo / "count.txt").exists() and time.monotonic() < limit:
            time.sleep(0.05)
        assert (repo / "count.txt").read_text() == "x"
        before = c.get(path + "/view").json()
        controls["restart"]()
        v = wait_for(
            c, path, lambda v: any(e["kind"] == "unknown_result" for e in v["events"])
        )
        assert [t["state"] for t in v["turns"]] == ["completed", "interrupted"]
        assert v["turns"][0]["turn_id"] == completed and v["turns"][1]["turn_id"] == tid
        assert before["events"] == v["events"][: len(before["events"])]
        expected = [e for e in v["events"] if e["seq"] > before["through_seq"]]
        for _ in range(2):
            with c.stream(
                "GET",
                path + "/events?after=invalid",
                headers={"Last-Event-ID": sid + "/" + str(before["through_seq"])},
            ) as response:
                events = (
                    json.loads(l[6:])
                    for l in response.iter_lines()
                    if l.startswith("data: ")
                )
                assert [next(events) for _ in expected] == expected
        for cursor in ("bad", sid + "/9999999", "wrong/0", sid + "/-1"):
            with c.stream(
                "GET", path + "/events", params={"after": cursor}
            ) as response:
                assert next(response.iter_lines()) == "event: reset_required"
        from webdemo.coding.store import Store

        store = Store(work / "state")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            with store.connection() as db:
                if db.execute("SELECT state FROM leases").fetchone()[0] == "free":
                    break
            time.sleep(0.1)
        else:
            raise AssertionError("cleanup proof missing")
        assert (repo / "count.txt").read_text() == "x"
        assert any(item["path"]=="count.txt" for item in c.get(path+"/diff").json())
        resumed=turn(c,path,"resume after interruption")["turn_id"]
        assert finished(c,path,resumed)["turns"][-1]["state"]=="completed"
        # 使用原生运行时写入多轮历史，再降低下一轮的上下文窗口以触发真实压缩。
        new = post(c, "sessions", directory=str(repo))["session_id"]
        ctx = "sessions/" + new
        prompts = [
            "EARLY-MARKER " + ("chronicle " * 2000),
            "read my file",
            "one",
            "two",
            "three",
            "RECENT-DETAIL-23",
        ]
        for user in prompts:
            finished(c, ctx, turn(c, ctx, user)["turn_id"])
        config = json.loads((work / "provider.json").read_text())["providers"][0]
        profile = post(
            c,
            "profiles",
            provider="short",
            model="small",
            endpoint=config["base_url"],
            protocol="openai-compat",
            api_key="local-demo",
            context_window=33001,
        )
        post(c, ctx + "/settings", profile_id=profile["profile_id"])
        compact = finished(c, ctx, turn(c, ctx, "compact now")["turn_id"])
        assert any(
            e["kind"] == "compact" and e["payload"].get("boundary")
            for e in compact["events"]
        ), compact["turns"]
        assert any(e["kind"] == "tool_result" for e in compact["events"])
        controls["restart"]()
        boundary_view = c.get(ctx + "/view").json()
        assert boundary_view["events"] == compact["events"]
        final = finished(c, ctx, turn(c, ctx, "recall recent details")["turn_id"])
        msgs = json.dumps(requests[-1])
        assert (
            msgs.count("EARLY-SUMMARY-17") == 1
            and msgs.count("RECENT-DETAIL-23") == 1
            and "chronicle " not in msgs
        )
        assert (
            final["turns"][-1]["state"] == "completed"
            and (repo / "count.txt").read_text() == "x"
        )
        with store.connection(True) as db:
            db.execute("DELETE FROM events WHERE session=? AND seq<=2", (sid,))
        with c.stream(
            "GET", path + "/events", params={"after": sid + "/0"}
        ) as response:
            assert next(response.iter_lines()) == "event: reset_required"
        (work / "recovery.json").write_text(
            json.dumps(
                {
                    "session": sid,
                    "lost_turn": tid,
                    "restored": v["turns"],
                    "replayed": [e["seq"] for e in expected],
                    "disk_count": 1,"continued_after_interrupt":resumed,
                    "compact_session": new,
                    "boundary": [
                        e for e in compact["events"] if e["kind"] == "compact"
                    ],
                    "model_input_summary_count": msgs.count("EARLY-SUMMARY-17"),
                    "model_input_detail_count": msgs.count("RECENT-DETAIL-23"),
                },
                indent=2,
            )
        )
        print("PASS restart, replay, unknown result, compaction", sid, new)


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    parser = argparse.ArgumentParser()
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--save-baseline", type=Path)
    actions.add_argument("--verify-baseline", type=Path)
    actions.add_argument("--coding", action="store_true")
    args = parser.parse_args()
    if args.coding:
        coding()
    elif args.save_baseline or args.verify_baseline:
        baseline(
            args.save_baseline or args.verify_baseline, args.save_baseline is not None
        )
    else:
        run()
