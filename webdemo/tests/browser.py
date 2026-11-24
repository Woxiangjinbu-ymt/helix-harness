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
