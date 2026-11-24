"""Exercise a running local server, including its real worker and independent judge."""

import json
import os

import httpx


def main():
    with httpx.Client(
        base_url=os.getenv("RSI_API", "http://127.0.0.1:8765"),
        headers={"X-RSI-Client": "console"},
        timeout=180,
    ) as c:

        def get(path):
            r = c.get("/api/" + path)
            r.raise_for_status()
            return r.json()

        def post(path, data):
            r = c.post("/api/" + path, json=data)
            r.raise_for_status()
            return r.json()["id"]

        assert get("benchmarks")[0]["split"] == "unspecified"
        assert all(set(p) == {"id", "model", "mode"} for p in get("providers"))
        assert (
            c.post("/api/repositories", json={"name": "../private"}).status_code == 422
        )
        assert (
            httpx.post(
                str(c.base_url) + "/api/tasks/run", json={"repo": "page-settings"}
            ).status_code
            == 403
        )
        t = post(
            "tasks/run",
            {"repo": "page-settings", "prompt": "Correct pagination precedence."},
        )
        e = post("evolution/start", {"rounds": 1})
        print("started", t, e, flush=True)
        for group, id in (("tasks", t), ("evolution", e)):
            r = c.get(f"/api/{group}/{id}/events")
            r.raise_for_status()
            events = [
                json.loads(l[6:]) for l in r.text.splitlines() if l.startswith("data: ")
            ]
            assert events[-1]["event_type"] == "run_completed", events[-4:]
            cursor = events[-3]["event_id"]
            replay = c.get(
                f"/api/{group}/{id}/events", headers={"last-event-id": str(cursor)}
            )
            assert [
                json.loads(l[6:])["event_id"]
                for l in replay.text.splitlines()
                if l.startswith("data: ")
            ] == [v["event_id"] for v in events[-2:]]
        task = get("tasks/" + t)
        assert task["receipt"]["score"] == 100 and "settings.py" in task["diff"]
        report = get("evolution/" + e)
        assert report["rounds"][0]["after"]["decision"] == "accepted"
        print(
            "PASS task score 100, evolution promoted, replay and access controls",
            flush=True,
        )


def coding():
    from webdemo.tests.coding_fixture import finished, post, service, turn

    def model(req, delta):
        text = str(req["messages"])
        if "private-working-tree-marker" in text:
            return delta(content="private-working-tree-marker")
        return delta(("ReadFile", {"file_path": "unstaged.txt"}))

    with service("coding-independent", model) as (work, repo, url, c):
        (repo / "unstaged.txt").write_text("private-working-tree-marker")
        sid = post(c, "sessions", directory=str(repo))["session_id"]
        path = "sessions/" + sid
        for prompt in (
            "Read the uncommitted file.",
            "Repeat its content from history.",
        ):
            t = turn(c, path, prompt)
            view = finished(c, path, t["turn_id"])
            assert view["turns"][-1]["state"] == "completed"
        assert (
            c.post(
                "sessions",
                headers={"Origin": "https://untrusted.invalid"},
                json={"directory": str(repo)},
            ).status_code
            == 403
        )
        assert (
            httpx.post(
                url + "/api/coding/sessions", json={"directory": str(repo)}
            ).status_code
            == 403
        )
        assert not (work / "missing").exists()
        calls = [e for e in view["events"] if e["kind"] == "tool_result"]
        assert (
            len(calls) == 1
            and "private-working-tree-marker" in calls[0]["payload"]["output"]
        )
        (work / "smoke.json").write_text(
            json.dumps(
                {"session": sid, "call": calls[0], "turns": view["turns"]}, indent=2
            )
        )
        print("PASS independent Coding", sid)


if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    coding() if "--coding" in sys.argv else main()
