"""Browser acceptance runner. Select a scenario with --phase."""

import argparse
import os
from datetime import UTC
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

URL = os.getenv("RSI_UI", "http://127.0.0.1:5173")


def choose_folder(page, path):
    page.get_by_role("button", name="Choose local folder").click()
    page.get_by_label("Working directory", exact=True).fill(str(path))
    page.get_by_role("button", name="Open directory").click()
    page.wait_for_url("**/#coding/*")


def dashboard(page):
    page.goto(URL + "/#overview")
    expect(page.get_by_role("heading", name="Harness performance")).to_be_visible()
    for mode in ("Real", "Mock"):
        page.get_by_label("Filter sources").select_option(mode)
        expect(page.locator(".kpi").first.locator("strong")).to_be_visible()
        assert (
            page.locator(".kpi")
            .first.locator("strong")
            .inner_text()
            .replace(",", "")
            .isdigit()
        )
    page.get_by_label("Filter sources").select_option("All sources")
    expect(page.get_by_role("heading", name="Recent coding tasks")).to_be_visible()
    page.screenshot(path="/tmp/helix-overview.png", full_page=True)


def coding(page):
    page.goto(URL + "/#workspace")
    page.get_by_label("Source repository").select_option("page-settings")
    page.get_by_label("Task instructions").fill(
        "Correct pagination so the explicit limit takes precedence, including zero."
    )
    page.get_by_role("button", name="Run task").click()
    page.wait_for_url("**/#workspace/*")
    expect(page.locator(".tracelist").first).to_contain_text("Tool call", timeout=90000)
    page.reload()
    expect(page.locator(".score strong")).to_have_text("100", timeout=120000)
    expect(page.locator(".diff")).to_contain_text("settings.py")
    page.get_by_role("button", name="Files", exact=True).click()
    page.get_by_label("Artifact file").select_option("settings.py")
    expect(page.locator(".code")).to_contain_text("page_size")
    page.get_by_role("button", name="Tools", exact=True).click()
    expect(page.locator(".tracelist").last).to_contain_text("Bash")
    page.get_by_role("button", name="Replay score").click()
    expect(page.get_by_text("Receipt matched", exact=True)).to_be_visible(timeout=90000)
    page.reload()
    expect(page.get_by_text("Receipt matched", exact=True)).to_be_visible()
    page.screenshot(path="/tmp/helix-workspace.png", full_page=True)


def evolve(page):
    page.goto(URL + "/#evolution")
    page.get_by_label("Tested agent").select_option("demo-mock")
    page.get_by_label("Round limit").fill("2")
    page.get_by_role("button", name="Start evolution").click()
    page.wait_for_url("**/#evolution/*")
    expect(page.locator(".eventlog")).to_contain_text("trial started", timeout=25000)
    page.reload()
    expect(page.locator(".eventlog")).to_contain_text(
        "version accepted", timeout=160000
    )
    expect(page.locator(".eventlog")).to_contain_text(
        "rollback completed", timeout=160000
    )
    expect(page.get_by_text("Stop reason · round limit")).to_be_visible(timeout=20000)
    page.get_by_role("button", name="Round 2").click()
    expect(page.get_by_text("Reference retained at v2.", exact=False)).to_be_visible()
    expect(page.locator(".diff")).to_contain_text("prompt.md")
    page.screenshot(path="/tmp/helix-evolution.png", full_page=True)
    print("browser evolution", page.url, flush=True)


def history(page):
    archive = "day-4-scripted-acceptance"
    records = page.request.get(URL + "/api/evolution").json()
    run = next((r for r in records if r["id"] == archive + "~loop"), None)
    if run is None:
        run = next(
            r
            for r in records
            if r["mode"] == "Mock"
            and r["status"] == "completed"
            and r.get("score") == 55
            and r["config"]["rounds"] >= 2
        )
    report = page.request.get(URL + "/api/evolution/" + run["id"]).json()
    round_count = len(report["rounds"])
    retained = report["rounds"][-1]["before"]
    line = retained["study"] + "~" + retained["base"]
    page.goto(URL + "/#evolution/" + run["id"])
    page.get_by_role("button", name=f"Round {round_count}").click()
    expect(
        page.get_by_text(f"Reference retained at v{retained['version']}.", exact=False)
    ).to_be_visible()
    page.get_by_text("page-settings", exact=True).locator(
        "xpath=self::summary"
    ).first.click()
    assert page.get_by_role("button", name="Sample 1 ·", exact=False).count()
    page.locator(".finding summary").first.click()
    page.get_by_role("button", name="Inspect trajectory").first.click()
    expect(page.locator(".tracelist").first).to_be_visible()
    page.get_by_role("button", name="Files", exact=True).click()
    expect(page.get_by_label("Artifact file")).to_be_visible()
    page.goto(URL + "/#versions/" + retained["id"])
    expect(
        page.get_by_role("heading", name=f"Harness v{retained['version']}", exact=True)
    ).to_be_visible()
    page.get_by_role("button", name="Files", exact=True).click()
    page.get_by_label("Artifact file").select_option("prompt.md")
    expect(page.locator(".code")).to_contain_text("Trace configuration")
    page.get_by_role("button", name="Changes", exact=True).click()
    expect(page.locator(".diff")).to_contain_text("prompt.md")
    page.goto(URL + "/#evaluations")
    page.get_by_label("Experiment lineage").select_option(line)
    page.get_by_label("Reference version").select_option(line + "~1")
    page.get_by_label("Candidate version").select_option(retained["id"])
    expect(page.get_by_role("row").filter(has_text="Benchmark score")).to_contain_text(
        "55"
    )
    expect(
        page.get_by_text("Train / validation / test split", exact=False)
    ).to_be_visible()
    page.get_by_label("Evaluation source").select_option("Real")
    assert all(
        mode == "Real" for mode in page.locator(".tall .badge").all_inner_texts()
    )
    page.screenshot(path="/tmp/helix-evaluations.png", full_page=True)


def real(page):
    page.goto(URL + "/#workspace")
    page.get_by_label("Source repository").select_option("page-settings")
    page.get_by_label("Model provider").select_option(os.environ["RSI_REAL_PROVIDER"])
    page.get_by_label("Task instructions").fill(
        "Implement the public pagination specification and run the public tests."
    )
    page.get_by_role("button", name="Run task").click()
    page.wait_for_url("**/#workspace/*")
    expect(page.get_by_text("Independent Docker evaluation")).to_be_visible(
        timeout=180000
    )
    expect(page.locator(".tracelist").first).to_contain_text("Tool call")
    print(
        "real model",
        page.url,
        "score",
        page.locator(".score strong").inner_text(),
        flush=True,
    )


def registered(page):
    page.goto(URL + "/#workspace")
    page.get_by_role("button", name="Register repository").click()
    page.get_by_label("Published Git project").fill("pagination-demo")
    page.get_by_role("button", name="Register project", exact=True).click()
    expect(
        page.get_by_label("Source repository").locator("option:checked")
    ).to_have_text("pagination-demo")
    page.get_by_label("Task instructions").fill(
        "Inspect the source and run its public tests."
    )
    page.get_by_role("button", name="Run task").click()
    page.wait_for_url("**/#workspace/*")
    expect(page.get_by_text("Registered public tests · passed")).to_be_visible(
        timeout=60000
    )
    expect(page.locator(".score strong")).to_have_text("—")


def cancellation(page):
    page.goto(URL + "/#evolution")
    page.get_by_role("button", name="Start evolution").click()
    page.wait_for_url("**/#evolution/*")
    expect(page.locator(".eventlog")).to_contain_text("trial started", timeout=30000)
    page.get_by_role("button", name="Cancel evolution").click()
    expect(page.locator(".eventlog")).to_contain_text("run cancelled", timeout=90000)
    page.reload()
    expect(page.get_by_role("heading", name="Lifecycle journal")).to_be_visible()
    expect(page.locator(".eventlog")).to_contain_text("run cancelled", timeout=30000)


def coding_workspace(page):
    """在本机专用验收实例与新建的两份 Git 工作树中验证普通入口。"""
    import json
    import subprocess
    import tempfile
    import time

    scratch = Path(tempfile.mkdtemp(prefix="mewcode-browser-"))
    scratch = scratch.resolve()
    repo, other = scratch / "addition", scratch / "other"
    for path in (repo, other):
        path.mkdir()
        subprocess.run(["git", "init", "-q", path], check=True)
    (repo / "maths.py").write_text("def add(a, b):\n    return a - b\n")
    (repo / "test_maths.py").write_text(
        "import unittest\nfrom maths import add\nclass Addition(unittest.TestCase):\n"
        "    def test_sum(self):\n        self.assertEqual(add(3, 2), 5)\n"
    )
    subprocess.run(["git", "-C", repo, "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            repo,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@invalid",
            "commit",
            "-qm",
            "base",
        ],
        check=True,
    )
    calls = []
    page.on(
        "request",
        lambda r: calls.append(r.url.split("/api/")[-1]) if "/api/" in r.url else None,
    )
    page.goto(URL)

    def open_repo(path):
        choose_folder(page, path)
        page.wait_for_url("**/#coding/*")
        expect(page.get_by_label("Message", exact=True)).to_be_visible()
        return page.url

    original = open_repo(repo)
    sid = original.rsplit("/", 1)[1]
    box = page.get_by_label("Message", exact=True)
    box.fill("draft A")
    page.get_by_role("button", name="New session").click()
    open_repo(other)
    box.fill("draft B")
    page.goto(original)
    expect(box).to_have_value("draft A")
    page.get_by_role("button", name="Files", exact=True).click()
    page.get_by_role("textbox", name="Search files").fill("return a - b")
    page.get_by_label("Search contents").check()
    expect(page.locator(".code")).to_contain_text("return a - b")
    page.get_by_label("Close panel").click()
    box.fill(
        "用 Bash 直接执行 python3 -m unittest，解释测试失败。用 ReadFile 读 maths.py，EditFile 修复 add，再执行完全相同的命令并报告两个结果。只操作这两个文件，不要运行其他命令。"
    )
    page.get_by_role("button", name="Send", exact=True).click()
    endpoint = URL + "/api/coding/sessions/" + sid
    handled = set()
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        view = page.request.get(endpoint + "/view").json()
        events = view["events"]
        for e in events:
            data = e["payload"]
            if e["kind"] != "permission" or data["permission_id"] in handled:
                continue
            call = next(
                x["payload"]
                for x in events
                if x["kind"] == "tool_call"
                and x["payload"]["call_id"] == data["call_id"]
            )
            tool, args = call["tool"], call["args"]
            allow = (
                tool == "Bash" and args["command"].strip() == "python3 -m unittest"
            ) or (
                tool in ("EditFile", "WriteFile")
                and (repo / args["file_path"]).resolve() == repo / "maths.py"
            )
            card = page.locator('[data-permission="' + data["permission_id"] + '"]')
            if not allow:
                card.get_by_label("Feedback for the agent").fill(
                    "请只运行 python3 -m unittest 或编辑 maths.py"
                )
            card.get_by_role(
                "button", name="Allow once" if allow else "Deny", exact=True
            ).click()
            handled.add(data["permission_id"])
        if view["turns"] and view["turns"][-1]["ended"]:
            break
        page.wait_for_timeout(250)
    (scratch / "view.json").write_text(json.dumps(view, ensure_ascii=False, indent=2))
    print("Coding evidence", scratch, sid, flush=True)
    assert len(view["turns"]) == 1 and view["turns"][0]["state"] == "completed", view[
        "turns"
    ]
    assert [
        e["payload"]["exit_code"] for e in events if e["kind"] == "process_exited"
    ] == [1, 0]
    expect(page.locator('[data-state="completed"]')).to_be_visible()
    page.get_by_role("button", name="Terminal", exact=True).click()
    expect(page.locator(".coding-panel")).to_contain_text("Tests failed")
    expect(page.locator(".coding-panel")).to_contain_text("Tests passed")
    page.get_by_role("button", name="Changes", exact=True).click()
    expect(page.locator(".coding-panel .diff")).to_contain_text("return a + b")
    page.get_by_label("Close panel").click()
    page.get_by_label("Switch language").click()
    expect(page.get_by_role("button", name="终端", exact=True)).to_be_visible()
    page.reload()
    expect(page.locator('[data-state="completed"]')).to_be_visible()
    result = subprocess.run(
        ["python3", "-B", "-m", "unittest"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    (scratch / "unittest.txt").write_text(result.stdout + result.stderr)
    patch = subprocess.check_output(
        ["git", "-C", repo, "diff", "--no-ext-diff"], text=True
    )
    (scratch / "changes.diff").write_text(patch)
    assert "return a + b" in patch and all(path.startswith("coding/") for path in calls)
    page.screenshot(path=str(scratch / "workspace.png"))
    # 原仓库移走后原历史仍可看。用一个新仓库占据旧路径，验证不会误写。
    repo.rename(scratch / "moved")
    subprocess.run(["git", "init", "-q", repo], check=True)
    page.reload()
    expect(page.get_by_role("alert")).to_contain_text("工作目录不可用")
    box = page.get_by_role("textbox", name="消息")
    box.fill("这条指令不应运行")
    expect(page.get_by_role("button", name="发送", exact=True)).to_be_disabled()
    assert not (repo / "maths.py").exists()


def coding_onboarding(page):
    import time

    from helix_harness.tests.coding_fixture import service

    calls = []

    def respond(req, delta):
        calls.append(req)
        time.sleep(0.4)
        if any(m["role"] == "tool" for m in req["messages"]):
            return delta(content="Saved the note.")
        if "write a note" in str(req["messages"]):
            return delta(("WriteFile", {"file_path": "note.txt", "content": "on disk"}))
        return delta(content="Hello from the local folder.")

    with service("coding-onboarding", respond) as (work, repo, url, client):
        folder = work / "PPT"
        folder.mkdir()
        page.goto(url)
        box = page.get_by_role("textbox", name="Message", exact=True)
        expect(box).to_be_visible()
        expect(page.locator(".coding-sessions a")).to_have_count(0)
        expect(page.get_by_role("dialog")).to_have_count(0)
        page.screenshot(path=str(work / "home.png"))
        page.evaluate("""() => {
            const send = window.fetch.bind(window);
            window.fetch = async (...args) => {
                if (String(args[0]).endsWith('/turns') && !window.releaseTurn) {
                    await new Promise(resolve => { window.releaseTurn = resolve; });
                }
                return send(...args);
            };
        }""")
        box.fill("hello")
        box.press("Enter")
        dialog = page.get_by_role("dialog")
        path = dialog.get_by_label("Working directory")
        path.fill(str(work / "absent"))
        dialog.get_by_role("button", name="Open directory").click()
        expect(dialog.get_by_role("alert")).to_contain_text("目录不存在")
        expect(box).to_have_value("hello")
        path.fill(str(work))
        dialog.get_by_role("button", name="Browse", exact=True).click()
        dialog.get_by_role("button", name="PPT", exact=True).click()
        expect(path).to_have_value(str(folder))
        dialog.get_by_role("button", name="Open directory").click()
        page.wait_for_url("**/#coding/*")
        expect(page.locator(".coding-pending")).to_contain_text("Sending")
        expect(page.locator(".coding-user")).to_have_text("hello")
        page.evaluate("window.releaseTurn()")
        expect(page.locator(".coding-text")).to_have_text(
            "Hello from the local folder.", timeout=20000
        )
        expect(page.locator(".coding-user")).to_have_count(1)
        expect(page.get_by_role("button", name="Changes", exact=True)).to_have_count(0)
        session = "sessions/" + page.url.rsplit("/", 1)[1]
        assert len(client.get(session + "/view").json()["turns"]) == len(calls) == 1

        # 请求已持久化但回执丢失。冻结事件流直到重试，核对两次提交号相同。
        expect(page.locator('[data-state="completed"]')).to_have_count(1)
        page.route("**/events?after=*", lambda route: route.abort())
        page.reload()
        expect(box).to_be_enabled()
        requests = []

        def lose_first_reply(route):
            requests.append(route.request.post_data_json["submit_request_id"])
            response = route.fetch()
            assert response.status == 202
            if len(requests) == 1:
                route.abort()
            else:
                route.fulfill(response=response)

        page.route("**/turns", lose_first_reply)
        box.fill("write a note")
        expect(page.get_by_role("button", name="Send", exact=True)).to_be_enabled()
        box.press("Enter")
        expect(page.get_by_role("alert")).to_be_visible()
        expect(box).to_have_value("write a note")
        page.get_by_role("button", name="Retry").click()
        expect(box).to_have_value("")
        assert len(requests) == 2 and len(set(requests)) == 1
        page.unroute("**/events?after=*")
        card = page.locator(".coding-permission")
        expect(card.get_by_role("button", name="Allow once")).to_be_visible(
            timeout=15000
        )
        assert not (folder / "note.txt").exists()
        card.get_by_role("button", name="Allow once").click()
        expect(page.locator('[data-state="completed"]')).to_have_count(2, timeout=20000)
        assert (folder / "note.txt").read_text() == "on disk"
        assert not (folder / ".git").exists()
        page.reload()
        expect(page.locator(".coding-user")).to_have_count(2)
        page.get_by_role("button", name="Files", exact=True).click()
        expect(page.locator(".code")).to_contain_text("on disk")
        page.get_by_label("Close panel").click()
        page.screenshot(path=str(work / "folder.png"))
        page.unroute("**/turns")

        page.get_by_role("button", name="New session").click()
        choose_folder(page, repo)
        expect(page.get_by_role("button", name="Changes", exact=True)).to_be_visible()
        box.fill("hello from git")
        page.get_by_role("button", name="Send", exact=True).dblclick()
        expect(page.locator('[data-state="completed"]')).to_have_count(1, timeout=20000)
        assert len(client.get("sessions").json()) == 2
        git_view = client.get("sessions/" + page.url.rsplit("/", 1)[1] + "/view").json()
        assert len(git_view["turns"]) == 1
        page.set_viewport_size({"width": 390, "height": 844})
        page.get_by_label("Expand sessions").click()
        page.get_by_role("button", name="New session").click()
        expect(box).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(work / "mobile.png"))
        page.set_viewport_size({"width": 1440, "height": 980})
        page.goto("about:blank")


def coding_controls(page):
    """本地受控模型与真实 Runtime 验证并行卡片、授权、摘要和未知测试结论。"""
    import json
    import socket
    import subprocess
    import sys
    import tempfile
    import time
    from http.server import ThreadingHTTPServer
    from threading import Thread

    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    from helix_harness import mock
    from helix_harness.tests.test_coding_lifecycle import delta

    scratch = Path(tempfile.mkdtemp(prefix="mewcode-controls-")).resolve()
    repo = scratch / "repo"
    subprocess.run(["git", "init", "-q", repo], check=True)
    (repo / "first.txt").write_text("first-file-marker")
    steps = iter(
        [
            delta(
                ("ReadFile", {"file_path": "absent.txt"}),
                ("ReadFile", {"file_path": "first.txt"}),
                thinking="fixture",
            ),
            delta(("WriteFile", {"file_path": "approved.txt", "content": "created"})),
            delta(("WriteFile", {"file_path": "denied.txt", "content": "blocked"})),
            delta(("Bash", {"command": "printf plain-output"})),
            delta(content="Fixture finished"),
        ]
    )
    previous = mock.response
    mock.response = lambda req: next(steps)
    model = ThreadingHTTPServer(("127.0.0.1", 0), mock.Handler)
    thread = Thread(target=model.serve_forever, daemon=True)
    thread.start()
    config = scratch / "profile.json"
    config.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "name": "controlled",
                        "model": "scripted",
                        "protocol": "openai-compat",
                        "api_key": "local-demo",
                        "base_url": f"http://127.0.0.1:{model.server_port}/v1",
                    }
                ]
            }
        )
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    entry = (
        "from helix_harness.api import app;from fastapi.staticfiles import StaticFiles;import uvicorn,sys;"
        "app.mount('/',StaticFiles(directory='helix_harness/frontend/dist',html=True));"
        "uvicorn.run(app,host='127.0.0.1',port=int(sys.argv[1]),log_level='warning')"
    )
    server = subprocess.Popen(
        [sys.executable, "-B", "-c", entry, str(port)],
        cwd=root,
        env={
            **os.environ,
            "CODING_PROVIDER_CONFIG": str(config),
            "CODING_WEB_HOME": str(scratch / "state"),
        },
    )
    try:
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", port), 0.2):
                    break
            except OSError:
                time.sleep(0.1)
        page.goto(url)
        choose_folder(page, repo)
        page.get_by_label("Message", exact=True).fill(
            "controlled parallel call scenario"
        )
        page.get_by_role("button", name="Send", exact=True).click()
        page.wait_for_url("**/#coding/*")
        sid = page.url.rsplit("/", 1)[1]
        path = url + "/api/coding/sessions/" + sid
        for target, answer in (
            ("approved.txt", "Always allow in this worktree"),
            ("denied.txt", "Deny"),
        ):
            card = page.locator(".coding-permission").filter(has_text=target).last
            expect(card.get_by_role("button", name=answer, exact=True)).to_be_visible(
                timeout=15000
            )
            if target == "approved.txt":
                page.reload()
                expect(
                    card.get_by_role("button", name=answer, exact=True)
                ).to_be_visible()
            else:
                card.get_by_label("Feedback for the agent").fill("keep absent")
            assert not (repo / target).exists()
            card.get_by_role("button", name=answer, exact=True).click()
        bash = page.locator(".coding-permission").filter(has_text="plain-output")
        page.wait_for_function(
            """() => !!document.querySelector('[data-state="completed"]') ||
                          Array.from(document.querySelectorAll('.coding-permission')).some(e => e.textContent.includes('plain-output'))""",
            timeout=20000,
        )
        if bash.count():
            bash.get_by_role("button", name="Allow once", exact=True).click()
        expect(page.locator('[data-state="completed"]')).to_be_visible(timeout=20000)
        assert (repo / "approved.txt").read_text() == "created" and not (
            repo / "denied.txt"
        ).exists()
        page.get_by_role("button", name="Tools", exact=True).click()
        tools = page.locator(".coding-panel")
        reads = tools.locator(".coding-call").filter(has_text="ReadFile")
        expect(reads).to_have_count(2)
        expect(reads.filter(has_text="first-file-marker")).to_contain_text("first.txt")
        expect(
            reads.filter(has_text="absent.txt").locator("summary").first
        ).to_contain_text("Failed")
        expect(tools).to_contain_text("Persistent rule saved")
        page.get_by_role("button", name="Terminal", exact=True).click()
        expect(tools).to_contain_text("Test result unknown")
        expect(tools).to_contain_text("plain-output")
        page.get_by_label("Close panel").click()
        expect(page.locator("summary").filter(has_text="Thinking")).to_have_count(1)
        page.locator("summary").filter(has_text="Turn summary").click()
        expect(
            page.locator(".coding-timeline button").filter(has_text="approved.txt")
        ).to_be_visible()
        view = page.request.get(path + "/view").json()
        (scratch / "view.json").write_text(
            json.dumps(view, ensure_ascii=False, indent=2)
        )
        page.screenshot(path=str(scratch / "controls.png"))
        print("Controlled evidence", scratch, sid, flush=True)
        page.goto("about:blank")
    finally:
        server.terminate()
        try:
            server.wait(timeout=8)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
        model.shutdown()
        model.server_close()
        thread.join()
        mock.response = previous


def coding_legacy(page):
    job = os.environ["CODING_LEGACY_JOB"]
    paths = []
    page.on("request", lambda r: paths.append(r.url) if "/api/" in r.url else None)
    page.goto(URL + "/#workspace/" + job)
    expect(page.locator(".agent-workspace")).to_be_visible()
    expect(page.locator(".tracelist").first).to_be_visible(timeout=20000)
    assert any("/api/tasks/" + job in p for p in paths)
    assert not any("/api/coding/" in p for p in paths)
    print("Legacy URL verified", job, flush=True)


def coding_long(page):
    import json
    import sys
    import time
    from datetime import datetime

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from helix_harness.tests.coding_fixture import service

    def respond(req, delta):
        if "handoff-check" in str(req["messages"]):
            if any(m["role"] == "tool" for m in req["messages"]):
                return delta(content="done")
            return delta(("WriteFile", {"file_path": "handoff.txt", "content": "ok"}))
        return delta(("Bash", {"command": "python3 -u long.py", "timeout": 2400}))

    with service("coding-long", respond) as (work, repo, url, client):
        (repo / "long.py").write_text(
            "import os,time,subprocess,sys\nfrom pathlib import Path\nchild=subprocess.Popen([sys.executable,'-c','import time; time.sleep(3000)'])\nPath('pids.txt').write_text(str(os.getpid())+' '+str(child.pid))\nprint('LONG-START',os.getpid(),flush=True)\ntime.sleep(9)\nprint('OFFLINE-OUTPUT',flush=True)\nfor i in range(40): print(str(i)+'x'*60000,flush=True)\nwhile True:\n Path('progress.txt').write_text(str(time.time()))\n time.sleep(3)\n"
        )
        page.goto(url)
        choose_folder(page, repo)
        page.get_by_label("Message", exact=True).fill("long-running acceptance")
        page.get_by_role("button", name="Send", exact=True).click()
        page.wait_for_url("**/#coding/*")
        original = page.url
        sid = original.rsplit("/", 1)[1]
        path = "sessions/" + sid
        page.get_by_role("button", name="Allow once", exact=True).click(timeout=30000)
        until = time.monotonic() + 30
        while time.monotonic() < until:
            v = client.get(path + "/view").json()
            if any(e["kind"] == "process_started" for e in v["events"]):
                break
            time.sleep(0.2)
        start = v["turns"][0]
        started = datetime.fromisoformat(start["started"])
        mark = next(e for e in v["events"] if e["kind"] == "process_started")
        other = client.post("sessions", json={"directory": str(repo)}).json()[
            "session_id"
        ]
        ctx = page.context
        page.goto(url + "/#coding/" + other)
        expect(page.get_by_label("Message", exact=True)).to_be_visible()
        for tab in list(ctx.pages):
            tab.close()
        assert not ctx.pages
        evidence = {
            "session_id": sid,
            "turn_id": start["turn_id"],
            "call_id": mark["payload"]["call_id"],
            "started": started.isoformat(),
            "pages_after_close": len(ctx.pages),
            "closed": datetime.now(UTC).isoformat(),
            "second_session": other,
        }
        (work / "long.json").write_text(json.dumps(evidence, indent=2))
        print("Long task running", evidence, flush=True)
        while (datetime.now(UTC) - started).total_seconds() < 1801:
            time.sleep(
                min(10, max(0.1, 1801 - (datetime.now(UTC) - started).total_seconds()))
            )
            assert not ctx.pages
            client.get("health").raise_for_status()
        assert (repo / "progress.txt").exists()
        resumed = ctx.new_page()
        resumed.goto(original)
        expect(resumed.get_by_role("button", name="Cancel turn")).to_be_visible()
        evidence["reopened"] = datetime.now(UTC).isoformat()
        resumed.get_by_role("button", name="Terminal", exact=True).click()
        expect(resumed.locator(".coding-panel")).to_contain_text("LONG-START")
        expect(resumed.locator(".coding-panel")).to_contain_text("OFFLINE-OUTPUT")
        resumed.get_by_label("Close panel").click()
        resumed.get_by_role("button", name="Cancel turn").click()
        expect(resumed.locator('[data-state="cancelled"]')).to_be_visible(timeout=60000)
        v = client.get(path + "/view").json()
        cancel = next(
            e for e in v["events"] if e["payload"].get("state") == "cancelling"
        )
        evidence.update(
            cancelled=cancel["recorded_at"],
            elapsed=(
                datetime.fromisoformat(cancel["recorded_at"]) - started
            ).total_seconds(),
            truncated=any(e["payload"].get("truncated") for e in v["events"]),
            clean=any(e["kind"] == "process_exited" for e in v["events"]),
            offline_output=any(
                e["kind"] == "output"
                and "OFFLINE-OUTPUT" in e["payload"].get("text", "")
                and e["recorded_at"] > evidence["closed"]
                for e in v["events"]
            ),
        )
        assert evidence["elapsed"] >= 1800 and all(
            evidence[k] for k in ("clean", "truncated", "offline_output")
        ), evidence
        import os

        pids = [int(n) for n in (repo / "pids.txt").read_text().split()]
        for pid in pids:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                pass
            else:
                raise AssertionError("command family survived: " + str(pid))
        evidence["terminated_pids"] = pids
        resumed.goto(url + "/#coding/" + other)
        resumed.get_by_label("Message", exact=True).fill("handoff-check")
        resumed.get_by_role("button", name="Send", exact=True).click()
        resumed.get_by_role("button", name="Allow once", exact=True).click()
        expect(resumed.locator('[data-state="completed"]')).to_be_visible(timeout=20000)
        assert (repo / "handoff.txt").read_text() == "ok"
        evidence["handoff"] = True
        (work / "long.json").write_text(json.dumps(evidence, indent=2))
        print("Long task verified", work, evidence["elapsed"], flush=True)


def coding_real(page):
    import json
    import subprocess
    import time

    from helix_harness.tests.coding_fixture import service

    with service("coding-real") as (work, repo, url, client):
        (repo / "maths.py").write_text("def add(a, b):\n    return a - b\n")
        (repo / "test_maths.py").write_text(
            "import unittest\nfrom maths import add\nclass Addition(unittest.TestCase):\n    def test_sum(self): self.assertEqual(add(3,2),5)\n"
        )
        subprocess.run(["git", "-C", repo, "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                repo,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@invalid",
                "commit",
                "-qm",
                "fixture",
            ],
            check=True,
        )
        page.goto(url)
        choose_folder(page, repo)
        page.wait_for_url("**/#coding/*")
        original = page.url
        path = "sessions/" + original.rsplit("/", 1)[1]
        prompts = [
            "工作目录已经就绪，禁止 cd 或串联命令。先通过 Bash 仅运行 python3 -m unittest，读 maths.py 后解释失败原因，记住实际值和期望值。本轮不要修改文件。",
            "沿用刚才的诊断。先用 ReadFile 读取 maths.py，用 EditFile 修复，再通过 Bash 运行相同的 python3 -m unittest。请勿使用其他命令。",
            "只根据前两轮记录回答：原来的 add(3,2) 实际值和期望值分别是多少？修复后测试结果如何？不要调用工具。",
        ]
        for index, prompt in enumerate(prompts):
            if index == 2:
                ctx = page.context
                for tab in ctx.pages:
                    tab.close()
                page = ctx.new_page()
                page.goto(original)
                expect(page.locator('[data-state="completed"]')).to_have_count(2)
            page.get_by_label("Message", exact=True).fill(prompt)
            page.get_by_role("button", name="Send", exact=True).click()
            handled = set()
            end = time.monotonic() + 240
            while time.monotonic() < end:
                v = client.get(path + "/view").json()
                for item in v["permissions"]:
                    pid = item["permission_id"]
                    if pid in handled:
                        continue
                    call = next(
                        e["payload"]
                        for e in v["events"]
                        if e["kind"] == "tool_call"
                        and e["payload"]["call_id"] == item["call_id"]
                    )
                    tool, args = call["tool"], call["args"]
                    allow = (
                        index < 2
                        and tool == "Bash"
                        and args["command"].strip()
                        in ("python3 -m unittest", f"cd {repo} && python3 -m unittest")
                    ) or (
                        index == 1
                        and tool in ("EditFile", "WriteFile")
                        and (repo / args["file_path"]).resolve() == repo / "maths.py"
                    )
                    if not allow:
                        page.locator('[data-permission="' + pid + '"]').get_by_label(
                            "Feedback for the agent"
                        ).fill("请仅运行 python3 -m unittest。工作目录已绑定到仓库。")
                    page.locator('[data-permission="' + pid + '"]').get_by_role(
                        "button", name="Allow once" if allow else "Deny", exact=True
                    ).click()
                    handled.add(pid)
                if len(v["turns"]) > index and v["turns"][index]["ended"]:
                    break
                page.wait_for_timeout(200)
            (work / "view.json").write_text(json.dumps(v, ensure_ascii=False))
            assert (
                len(v["turns"]) == index + 1 and v["turns"][-1]["state"] == "completed"
            ), v["turns"]
            print("Real turn", index + 1, v["turns"][-1]["turn_id"], flush=True)
        exits = [
            e["payload"]["exit_code"]
            for e in v["events"]
            if e["kind"] == "process_exited"
        ]
        assert exits == [1, 0], exits
        reply = "".join(
            e["payload"]["text"]
            for e in v["events"]
            if e["kind"] == "text" and e["turn_id"] == v["turns"][-1]["turn_id"]
        )
        assert (
            "1" in reply
            and "5" in reply
            and any(word in reply.lower() for word in ("通过", "成功", "ok", "pass"))
        )
        patch = subprocess.check_output(["git", "-C", repo, "diff"], text=True)
        (work / "fix.diff").write_text(patch)
        assert "return a + b" in patch or "return a+b" in patch
        result = subprocess.run(
            ["python3", "-B", "-m", "unittest"],
            cwd=repo,
            text=True,
            capture_output=True,
            check=True,
        )
        (work / "unittest.txt").write_text(result.stderr)
        page.get_by_role("button", name="Changes", exact=True).click()
        expect(page.locator(".coding-panel .diff")).to_contain_text("return")
        assert all(
            e["payload"].get("provider") not in ("controlled", "Helix Demo")
            for e in v["events"]
        )
        print("Real browser verified", original, flush=True)


def coding_stability(page):
    import json

    from helix_harness.tests.coding_fixture import (
        allow,
        finished,
        post,
        service,
        turn,
        wait_for,
    )

    def respond(req, delta):
        last = max(
            (
                i
                for i, m in enumerate(req["messages"])
                if isinstance(m.get("content"), str)
                and m["content"].startswith("write-proof-")
            ),
            default=-1,
        )
        if last >= 0 and not any(
            m["role"] == "tool" for m in req["messages"][last + 1 :]
        ):
            return delta(
                (
                    "WriteFile",
                    {
                        "file_path": req["messages"][last]["content"] + ".txt",
                        "content": "recorded once",
                    },
                )
            )
        return delta(content="stable answer")

    with service("coding-reconnect", respond) as (work, repo, url, c):
        sid = post(c, "sessions", directory=str(repo))["session_id"]
        path = "sessions/" + sid
        ctx = page.context
        ctx.add_init_script("""window.feedCursors=[];window.applied=[];window.EventSource=class extends EventSource{
          constructor(url){if(window.forceReset){url=url.replace(/after=.*/,"after=invalid");window.forceReset=false}super(url);window.feedCursors.push(url);window.dropFeed=()=>{this.close();this.dispatchEvent(new Event("error"))}}
          set onmessage(fn){super.onmessage=e=>{
            if(window.damage){window.damage=false;fn({data:"{",lastEventId:e.lastEventId});return}
            window.applied.push(e.lastEventId);fn(e)
          }}
        }""")
        page.goto(url + "/#coding/" + sid)
        page.wait_for_function("window.feedCursors.length>0")
        receipt = turn(c, path, "write-proof-one")
        expect(
            page.get_by_role("button", name="Allow once", exact=True)
        ).to_be_visible()
        pending = wait_for(c, path, lambda v: v["permissions"])
        page.evaluate("window.dropFeed()")
        page.wait_for_function("window.feedCursors.length>=2")
        expect(page.get_by_role("button", name="Allow once", exact=True)).to_have_count(
            1
        )
        # 同一轮再次断开，服务端在离线期间处理已有授权并完成轮次。
        ctx.set_offline(True)
        page.evaluate("window.dropFeed()")
        allow(c, path, pending)
        finished(c, path, receipt["turn_id"])
        ctx.set_offline(False)
        expect(page.locator('[data-state="completed"]')).to_have_count(1, timeout=15000)
        page.evaluate("window.damage=true")
        receipt = turn(c, path, "ordinary second")
        v = finished(c, path, receipt["turn_id"])
        expect(page.locator('[data-state="completed"]')).to_have_count(2)
        want = [sid + "/" + str(e["seq"]) for e in v["events"]]
        page.wait_for_function("last=>window.applied.includes(last)", arg=want[-1])
        observed = page.evaluate("window.applied")
        assert observed == want, (observed, want)
        assert (repo / "write-proof-one.txt").read_text() == "recorded once"
        expect(page.locator("[data-call]")).to_have_count(1)
        with page.expect_response(lambda r: r.url.endswith("/view")):
            page.evaluate("window.forceReset=true;window.dropFeed()")
        expect(page.locator("[data-call]")).to_have_count(1)
        second = ctx.new_page()
        second.goto(page.url)
        job = turn(c, path, "write-proof-cancel")
        expect(
            page.get_by_role("button", name="Allow once", exact=True)
        ).to_be_visible()
        second.get_by_role("button", name="Cancel turn").click()
        expect(page.locator('[data-state="cancelled"]')).to_have_count(1)
        second.reload()
        expect(second.locator('[data-state="cancelled"]')).to_have_count(1)
        assert not (repo / "write-proof-cancel.txt").exists()
        post(c, path + "/settings", profile_id="demo:local")
        again = turn(c, path, "continue after cancel")
        assert finished(c, path, again["turn_id"])["turns"][-1]["state"] == "completed"
        evidence = {
            "session": sid,
            "event_ids": want,
            "applied": observed,
            "connections": page.evaluate("window.feedCursors.length"),
            "cancelled": job["turn_id"],
            "continued": again["turn_id"],
        }
        assert evidence["connections"] >= 3
        (work / "reconnect.json").write_text(json.dumps(evidence, indent=2))
        print("PASS browser reconnect, cursor replay, two-tab cancel", sid)


def coding_settings(page):
    import json

    from helix_harness.tests.coding_fixture import (
        allow,
        finished,
        post,
        service,
        turn,
        wait_for,
    )

    models = []

    def respond(req, delta):
        models.append(req["model"])
        if req["messages"][-1]["role"] == "user" and "hold-profile" in str(
            req["messages"][-1]
        ):
            return delta(("WriteFile", {"file_path": "held.txt", "content": "A"}))
        return delta(content="provider-replied")

    with service("coding-settings", respond) as (work, repo, url, c):
        cfg = json.loads((work / "provider.json").read_text())["providers"][0]
        sid = post(c, "sessions", directory=str(repo))["session_id"]
        path = "sessions/" + sid
        page.goto(url + "/#coding-settings/" + sid)
        history = []
        profile_ids = []
        for name in ("web-first", "web-second"):
            page.get_by_role("button", name="New provider").click()
            form = page.locator("form").filter(
                has=page.locator('input[name="api_key"]')
            )
            for key, value in {
                "provider": name,
                "model": name,
                "endpoint": cfg["base_url"],
                "api_key": "local-demo",
            }.items():
                form.locator('input[name="' + key + '"]').fill(value)
            form.get_by_role("button", name="Save provider").click()
            expect(form).to_have_count(0)
            info = next(p for p in c.get("profiles").json() if p["provider"] == name)
            profile_ids.append(info["profile_id"])
            page.get_by_label("Model for the next turn").select_option(
                info["profile_id"]
            )
            page.get_by_role("button", name="Apply settings").click()
            page.wait_for_function(
                "()=>!document.querySelector('button[type=submit]:disabled')"
            )
            expect(page.get_by_role("button", name="Apply settings")).to_be_enabled()
            job = turn(c, path, name)
            v = finished(c, path, job["turn_id"])
            history.append(job["turn_id"])
        assert models == ["web-first", "web-second"], models
        page.reload()
        expect(page.get_by_label("Model for the next turn")).to_have_value(
            info["profile_id"]
        )
        assert "local-demo" not in page.locator("body").inner_text()
        assert "local-demo" not in page.evaluate(
            "JSON.stringify([localStorage,sessionStorage])"
        )

        def select_profile(key):
            page.get_by_label("Model for the next turn").select_option(key)
            with page.expect_response(
                lambda r: r.request.method == "POST" and r.url.endswith("/settings")
            ):
                page.get_by_role("button", name="Apply settings").click()

        select_profile(profile_ids[0])
        active = turn(c, path, "hold-profile")
        pending = wait_for(c, path, lambda v: v["permissions"])
        select_profile(profile_ids[1])
        allow(c, path, pending)
        assert finished(c, path, active["turn_id"])["turns"][-1]["state"] == "completed"
        next_turn = turn(c, path, "after switch")
        finished(c, path, next_turn["turn_id"])
        assert models[-3:] == ["web-first", "web-first", "web-second"]
        select_profile("demo:local")
        demo = turn(c, path, "explicit demo")
        demo_view = finished(c, path, demo["turn_id"])
        assert any(e["payload"].get("source") == "demo" for e in demo_view["events"])
        bad = post(
            c,
            "profiles",
            provider="bad-key",
            protocol="openai-compat",
            model="wrong-key-model",
            endpoint=cfg["base_url"],
            api_key="test-failure-marker",
        )
        post(c, path + "/settings", profile_id=bad["profile_id"])
        t = turn(c, path, "connection test")
        v = finished(c, path, t["turn_id"])
        assert v["turns"][-1]["state"] == "failed" and v["turns"][-1]["reason"]
        page.goto(url + "/#coding/" + sid)
        expect(page.locator('[data-turn="' + t["turn_id"] + '"]')).to_contain_text(
            "wrong-key-model"
        )
        page.get_by_label("Switch language").click()
        expect(page.locator('[data-state="failed"]')).to_be_visible()
        for marker in ("test-failure-marker", "local-demo"):
            assert (
                marker not in json.dumps(v)
                and marker not in page.locator("body").inner_text()
            )
        (work / "settings.json").write_text(
            json.dumps(
                {
                    "session": sid,
                    "models": models,
                    "turns": history,
                    "failed_turn": t["turn_id"],
                    "credential_matches": 0,
                },
                indent=2,
            )
        )
        print("PASS browser settings, sources, failure", sid)


def coding_extensions(page):
    import json
    import shutil
    import sys

    from mtcode.mcp.tool_wrapper import build_mcp_tool_name
    from helix_harness.tests.coding_fixture import finished, post, service, turn

    tool = build_mcp_tool_name("local-stamp", "stamp")

    def respond(req, delta):
        messages = req["messages"]
        if not any(m["role"] == "tool" for m in messages):
            if any(t["function"]["name"] == tool for t in req["tools"]):
                return delta((tool, {}))
            return delta(content="extensions disconnected")
        if "SKILL-WEB-MARK-12" in str(messages):
            return delta(content="Skill selected the answer ORANGE-12")
        return delta(content="done")

    with service("coding-extensions", respond) as (work, repo, url, c):
        cfg = work / "provider.json"
        config = json.loads(cfg.read_text())
        config["mcp_servers"] = [
            {
                "name": "local-stamp",
                "command": str(Path(sys.base_prefix) / "bin/python3"),
                "args": ["-B", "stamp.py"],
            },
            {"name": "unavailable-server", "command": "/missing/executable"},
        ]
        cfg.write_text(json.dumps(config))
        shutil.copyfile(
            Path(__file__).with_name("mcp_stdio_fixture.py"), repo / "stamp.py"
        )
        skills = repo / ".mewcode/skills"
        skills.mkdir(parents=True)
        (skills / "orange.md").write_text(
            "---\nname: orange\ndescription: fixture\n---\nSKILL-WEB-MARK-12: include ORANGE-12 in your answer."
        )
        (skills / "invalid.md").write_text("---\n: [invalid YAML\n---\n")
        sid = post(c, "sessions", directory=str(repo))["session_id"]
        path = "sessions/" + sid
        page.goto(url + "/#coding-settings/" + sid)
        expect(page.get_by_role("status")).to_contain_text("invalid.md")
        page.get_by_label("unavailable-server", exact=True).click()
        expect(page.get_by_role("status")).to_contain_text("unavailable-server")
        expect(page.get_by_label("unavailable-server", exact=True)).not_to_be_checked()
        for name in ("orange", "local-stamp"):
            with page.expect_response(
                lambda r: r.request.method == "POST" and r.url.endswith("/settings")
            ):
                page.get_by_label(name, exact=True).click()
        expect(page.locator("fieldset").filter(has_text="local-stamp")).to_contain_text(
            tool
        )
        page.reload()
        expect(page.locator("fieldset").filter(has_text="local-stamp")).to_contain_text(
            tool
        )
        page.goto(url + "/#coding/" + sid)
        t = turn(c, path, "run selected extensions")
        page.get_by_role("button", name="Allow once", exact=True).click(timeout=30000)
        v = finished(c, path, t["turn_id"])
        expect(page.locator(".coding-history")).to_contain_text("ORANGE-12")
        call = next(
            e["payload"]["call_id"]
            for e in v["events"]
            if e["kind"] == "tool_call" and e["payload"]["tool"] == tool
        )
        assert any(
            e["kind"] == "tool_result"
            and e["payload"]["output"] == "MCP-SESSION-MARK-42"
            for e in v["events"]
        )
        page.goto(url + "/#coding-settings/" + sid)
        for name in ("orange", "local-stamp"):
            with page.expect_response(
                lambda r: r.request.method == "POST" and r.url.endswith("/settings")
            ):
                page.get_by_role("checkbox", name=name).click()
        assert c.get(path + "/settings").json()["selected"]["mcp"] == []
        assert (repo / "mcp-count.txt").read_text() == "x"
        (work / "extensions.json").write_text(
            json.dumps(
                {
                    "session": sid,
                    "turn": t["turn_id"],
                    "call": call,
                    "tool": tool,
                    "skill_answer": "ORANGE-12",
                    "result": "MCP-SESSION-MARK-42",
                    "count": 1,
                    "disconnected": True,
                    "failures": ["invalid.md", "unavailable-server"],
                },
                indent=2,
            )
        )
        print("PASS browser Skill and MCP", sid)


def coding_provider_error(page):
    import json

    from helix_harness import providers
    from helix_harness.tests.coding_fixture import finished, post, service, turn

    _, native = providers.select()
    marker = "mewcode-invalid-acceptance-credential"
    with service("coding-provider-error") as (work, repo, url, c):
        profile = post(
            c,
            "profiles",
            provider=native.name,
            model=native.model,
            protocol=native.protocol,
            endpoint=native.base_url,
            api_key=marker,
        )
        sid = post(
            c, "sessions", directory=str(repo), profile_id=profile["profile_id"]
        )["session_id"]
        path = "sessions/" + sid
        page.goto(url + "/#coding/" + sid)
        job = turn(c, path, "Connectivity check. Answer OK.")
        v = finished(c, path, job["turn_id"])
        assert v["turns"][-1]["state"] == "failed"
        reason = v["turns"][-1]["reason"]
        assert any(
            word in reason.lower()
            for word in ("401", "403", "authenti", "api key", "unauthor")
        ), reason
        expect(page.locator('[data-turn="' + job["turn_id"] + '"]')).to_contain_text(
            native.model
        )
        expect(page.get_by_role("link", name="Review model settings")).to_be_visible()
        locations = {
            "settings": c.get("profiles").text,
            "events": json.dumps(v),
            "page": page.locator("body").inner_text(),
            "browser_storage": page.evaluate(
                "JSON.stringify([localStorage,sessionStorage])"
            ),
            "logs": (work / "server.log").read_text(),
            "transcript": "".join(
                p.read_text() for p in (work / "state/sessions").rglob("*.jsonl")
            ),
        }
        counts = {k: value.count(marker) for k, value in locations.items()}
        assert not any(counts.values()), counts
        (work / "provider_error.json").write_text(
            json.dumps(
                {
                    "session": sid,
                    "turn": job["turn_id"],
                    "provider": native.name,
                    "model": native.model,
                    "reason": reason,
                    "credential_matches": counts,
                },
                indent=2,
            )
        )
        print(
            "PASS real endpoint authentication failure", native.name, native.model, sid
        )


def coding_composer_controls(page):
    import threading

    from helix_harness.tests.coding_fixture import service

    requests = []
    release = threading.Event()

    def respond(req, delta):
        requests.append(req)
        if len(requests) == 1:
            release.wait(15)
        return delta(content="| Model | Result |\n| --- | --- |\n| fixture | done |")

    with service("composer-controls", respond) as (work, repo, url, client):
        page.goto(url)
        choose_folder(page, repo)
        (repo / "proof.txt").write_text("reference-marker-7795")
        box = page.get_by_role("textbox", name="Message", exact=True)
        box.fill("first")
        box.press("Enter")
        expect(page.get_by_role("button", name="Cancel turn")).to_be_visible()
        page.get_by_role("button", name="Select model").click()
        page.get_by_role("dialog").get_by_role("button", name="coding-demo").click()
        release.set()
        expect(page.locator("[data-state=completed]")).to_have_count(1, timeout=20000)
        expect(page.locator(".coding-text table")).to_be_visible()
        page.get_by_role("button", name="Add attachments").click()
        page.get_by_role("button", name="Reference project file").click()
        page.get_by_role("dialog").get_by_role("button", name="proof.txt").click()
        expect(
            page.locator("[data-slot=composer] [data-slot=attachment]")
        ).to_contain_text("proof.txt")
        page.get_by_role("button", name="Send", exact=True).click()
        expect(page.locator("[data-state=completed]")).to_have_count(2, timeout=20000)
        expect(page.locator(".coding-text").last).to_contain_text("Helix Demo")
        page.get_by_role("button", name="Select model").click()
        page.get_by_role("dialog").get_by_role("button", name="scripted").click()
        box.fill("read prior reference")
        box.press("Enter")
        expect(page.locator("[data-state=completed]")).to_have_count(3, timeout=20000)
        assert [r["model"] for r in requests] == ["scripted", "scripted"]
        assert "reference-marker-7795" in str(requests[-1]["messages"])
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(work / "composer-mobile.png"))
        for label in ["Select model", "Thinking effort", "Add attachments", "Skills"]:
            assert page.get_by_role("button", name=label, exact=True).is_visible()
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        page.set_viewport_size({"width": 1440, "height": 980})
        if page.locator("html").get_attribute("data-theme") == "dark":
            page.get_by_role("button", name="Switch to light theme").click()
        page.get_by_role("button", name="Switch to dark theme").click()
        expect(page.locator("html")).to_have_class("dark")
        page.get_by_role("button", name="Select model").click()
        page.screenshot(path=str(work / "composer-dark.png"))
        page.keyboard.press("Escape")
        # The stylesheet and theme adapter are scoped to the Coding route.
        page.get_by_role("link", name="RSI Center").click()
        expect(page.locator("link[href*='styles-']")).to_have_count(0)
        page.go_back()
        expect(page.get_by_role("button", name="Select model")).to_be_visible()
        assert len(client.get("sessions").json()) == 1


def coding_composer_real(page):
    from io import BytesIO

    from PIL import Image

    from helix_harness.tests.coding_fixture import service

    with service("composer-real") as (_work, repo, url, client):
        page.goto(url + "/#coding")
        box = page.get_by_role("textbox", name="Message", exact=True)
        page.get_by_role("button", name="Select model").click()
        page.get_by_role("dialog").get_by_role("button", name="deepseek-flash").click()
        page.get_by_role("button", name="Thinking effort").click()
        page.get_by_role("button", name="Off", exact=True).click()
        page.locator("input[type=file]").set_input_files(
            {
                "name": "sample.txt",
                "mimeType": "text/plain",
                "buffer": b"Verification code: MICA-728",
            }
        )
        expect(page.locator("[data-slot=attachment]")).to_contain_text("sample.txt")
        fresh = page.context.new_page()
        page.close()
        page = fresh
        page.goto(url + "/#coding")
        box = page.get_by_role("textbox", name="Message", exact=True)
        expect(page.locator("[data-slot=attachment]")).to_contain_text("sample.txt")
        box.fill("Reply with the verification code in the attachment. No tools.")
        box.press("Enter")
        page.get_by_label("Working directory", exact=True).fill(str(repo))
        page.get_by_role("button", name="Open directory").click()
        expect(page.locator("[data-state=completed]")).to_have_count(1, timeout=90000)
        expect(page.locator(".coding-text").last).to_contain_text("MICA-728")
        page.get_by_role("button", name="Thinking effort").click()
        page.get_by_role("button", name="Low", exact=True).click()
        image = BytesIO()
        Image.new("RGB", (96, 96), (0, 0, 255)).save(image, "PNG")
        # A literal paste exercises the browser's clipboard file path.
        import base64

        page.evaluate(
            """encoded => {
           const dt = new DataTransfer();
           dt.items.add(new File([Uint8Array.from(atob(encoded), c=>c.charCodeAt(0))], "sample.png", {type:"image/png"}));
           document.querySelector("textarea").dispatchEvent(new ClipboardEvent("paste",{clipboardData:dt,bubbles:true,cancelable:true}));
        }""",
            base64.b64encode(image.getvalue()).decode(),
        )
        expect(
            page.locator("[data-slot=composer] img[alt='sample.png']")
        ).to_be_visible()
        box.fill("What color fills this image? Answer one word in English. No tools.")
        box.press("Enter")
        expect(page.locator("[data-state=completed]")).to_have_count(2, timeout=90000)
        assert "blue" in page.locator(".coding-text").last.inner_text().lower()
        page.reload()
        expect(page.locator(".coding-user img[alt='sample.png']")).to_be_visible()
        page.get_by_role("button", name="Thinking effort").click()
        page.get_by_role("button", name="High", exact=True).click()
        box.fill("What color was the previous picture? One English word. No tools.")
        box.press("Enter")
        expect(page.locator("[data-state=completed]")).to_have_count(3, timeout=90000)
        assert "blue" in page.locator(".coding-text").last.inner_text().lower()
        session = "sessions/" + page.url.rsplit("/", 1)[1]
        view = client.get(session + "/view").json()
        measured = [
            e["payload"]
            for e in view["events"]
            if e["kind"] == "context" and e["payload"]["source"] == "measured"
        ]
        assert len(measured) >= 3 and measured[-1]["used"] > 500, measured
        page.get_by_role("button", name="Context ·").click()
        expect(page.get_by_role("dialog")).to_contain_text("Measured by provider")
        print("Verified provider context", measured[-1], flush=True)
        assert not list(repo.glob("sample.*"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="dashboard")
    phase = ap.parse_args().phase
    with sync_playwright() as pw:
        chrome = os.getenv("RSI_CHROME")
        system = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        if not chrome and Path(system).is_file():
            chrome = system
        browser = pw.chromium.launch(executable_path=chrome, headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 980})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        for name in phase.split(","):
            if page.is_closed():
                page = context.new_page()
                page.on("pageerror", lambda e: errors.append(str(e)))
            globals()[name](page)
            assert not errors, errors
            print("PASS browser", name, flush=True)
        browser.close()


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    main()
