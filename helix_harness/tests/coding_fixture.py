"""在独立 Git 目录、数据库和回环端口上运行浏览器验收。"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack, contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import httpx

ROOT = Path(__file__).resolve().parents[2]


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def service(scenario, respond=None, control=None):
    from helix_harness import mock
    from helix_harness.tests.test_coding_lifecycle import delta

    work = Path(tempfile.mkdtemp(prefix="mewcode-" + scenario + "-")).resolve()
    repo = work / "repo"
    subprocess.run(["git", "init", "-q", repo], check=True)
    url = "http://127.0.0.1:" + str(port())
    cmd = "from helix_harness.api import app;from fastapi.staticfiles import StaticFiles;import uvicorn,sys;app.mount('/',StaticFiles(directory='helix_harness/frontend/dist',html=True));uvicorn.run(app,host='127.0.0.1',port=int(sys.argv[1]),log_level='warning')"
    env = dict(
        os.environ,
        CODING_WEB_HOME=str(work / "state"),
        RSI_WEB_HOME=str(work / "unavailable"),
        RSI_ARCHIVE=str(work / "missing"),
        RSI_MOCK_PORT="unavailable",
    )
    (work / "unavailable").write_text("RSI disabled")
    with ExitStack() as resources:
        if respond:
            previous = mock.response
            resources.callback(setattr, mock, "response", previous)
            mock.response = lambda req: respond(req, delta)
            model = ThreadingHTTPServer(("127.0.0.1", 0), mock.Handler)
            thread = Thread(target=model.serve_forever, daemon=True)
            thread.start()
            resources.callback(model.server_close)
            resources.callback(model.shutdown)
            config = work / "provider.json"
            config.write_text(
                json.dumps(
                    {
                        "providers": [
                            {
                                "name": "controlled",
                                "model": "scripted",
                                "protocol": "openai-compat",
                                "base_url": f"http://127.0.0.1:{model.server_port}/v1",
                                "api_key": "local-demo",
                            }
                        ]
                    }
                )
            )
            env["CODING_PROVIDER_CONFIG"] = str(config)
        log = resources.enter_context((work / "server.log").open("w"))
        client = resources.enter_context(
            httpx.Client(
                base_url=url + "/api/coding/",
                headers={"X-RSI-Client": "console"},
                trust_env=False,
                timeout=15,
            )
        )
        proc = None

        def boot():
            nonlocal proc
            proc = subprocess.Popen(
                [sys.executable, "-B", "-c", cmd, url.rsplit(":", 1)[1]],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=log,
            )
            for _ in range(100):
                if proc.poll() is not None:
                    break
                try:
                    if client.get("health").is_success:
                        return
                except httpx.ConnectError:
                    pass
                time.sleep(0.1)
            raise RuntimeError("验收服务未启动")

        def close():
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()

        def restart():
            proc.kill()
            proc.wait(timeout=10)
            boot()

        resources.callback(close)
        boot()
        if control is not None:
            control["restart"] = restart
        print("Coding fixture", scenario, work, flush=True)
        yield work, repo, url, client


def post(client, path, **data):
    response = client.post(path, json=data)
    response.raise_for_status()
    return response.json()


def turn(client, path, text):
    from uuid import uuid4

    return post(client, path + "/turns", submit_request_id=str(uuid4()), text=text)


def wait_for(client, path, predicate, seconds=45):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        response = client.get(path + "/view")
        response.raise_for_status()
        v = response.json()
        if predicate(v):
            return v
        time.sleep(0.08)
    raise AssertionError([(t["turn_id"], t["state"], t["reason"]) for t in v["turns"]])


def finished(client, path, tid):
    return wait_for(
        client,
        path,
        lambda v: any(t["turn_id"] == tid and t["ended"] for t in v["turns"]),
    )


def allow(client, path, view, decision="allow"):
    from uuid import uuid4

    p = view["permissions"][0]
    return post(
        client,
        path
        + "/turns/"
        + p["turn_id"]
        + "/permissions/"
        + p["permission_id"]
        + "/reply",
        request_id=str(uuid4()),
        decision=decision,
    )
