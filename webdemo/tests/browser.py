"""Browser acceptance runner. Select a scenario with --phase."""

import argparse
import os
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

URL = os.getenv("RSI_UI", "http://127.0.0.1:5173")


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
        page.get_by_label("Git working directory").fill(str(path))
        page.get_by_role("button", name="Open directory").click()
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
    from webdemo import mock
    from webdemo.tests.test_coding_lifecycle import delta

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
        "from webdemo.api import app;from fastapi.staticfiles import StaticFiles;import uvicorn,sys;"
        "app.mount('/',StaticFiles(directory='webdemo/frontend/dist',html=True));"
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
        page.get_by_label("Git working directory").fill(str(repo))
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
        page = browser.new_page(viewport={"width": 1440, "height": 980})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        for name in phase.split(","):
            globals()[name](page)
            assert not errors, errors
            print("PASS browser", name, flush=True)
        browser.close()


if __name__ == "__main__":
    main()
