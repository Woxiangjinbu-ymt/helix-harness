"""Exercise a disposable backend, live SSE reconnect, a lost parent, and sandbox failure."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

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
        env = {**os.environ, "RSI_WEB_HOME": tmp, "RSI_MOCK_PORT": free_port()}
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


if __name__ == "__main__":
    run()
