"""Read models over immutable artifacts and the engine's control database."""

from datetime import datetime, timezone
from difflib import unified_diff
import json
from pathlib import Path

from mewcode.rsi.contracts import digest
from mewcode.rsi.fixtures import BENCHMARK, regular_files, tree_digest, file_digest
from mewcode.rsi.failure_analysis import FailureAnalyzer
from .store import DEFAULT, PUBLIC, ROOT, db, jobs, sources, read, rows


def repo_list():
    built = [
        dict(
            id=p.name,
            name=p.name,
            case=p.name,
            statement=(p / "statement.md").read_text(),
            kind="Benchmark",
        )
        for p in sorted(PUBLIC.iterdir())
        if p.is_dir()
    ]
    with db() as con:
        added = [
            dict(
                id=r["id"],
                name=r["name"],
                case=None,
                commit=r["commit_id"],
                kind="Registered",
                statement="Run a task against this pinned repository.",
            )
            for r in con.execute("SELECT * FROM repos")
        ]
    return built + added


def repo_path(id):
    if id in {p.name for p in PUBLIC.iterdir()}:
        return PUBLIC / id / "repo"
    with db() as con:
        r = con.execute("SELECT fixture FROM repos WHERE id=?", (id,)).fetchone()
    if not r:
        raise KeyError(id)
    return Path(r[0]) / "repo"


def runs_path(state):
    return state if state.name == "day-2-acceptance" else state / "runs"


def check_totals(state):
    totals = {}
    for path in (state / "evaluator-private").glob("*.json"):
        audit = read(path)
        if audit:
            checks = audit["checks"]
            totals[audit["receipt_digest"]] = (
                sum(c["reason"] == "passed" for c in checks),
                len(checks),
            )
    return totals


def index():
    tasks = []
    versions = []
    evolutions = []
    alljobs = jobs()
    lookup = {j["id"]: j for j in alljobs}
    for study, (state, mode) in sources().items():
        totals = check_totals(state)
        receipts = {
            r["cell_id"]: json.loads(r["receipt"]) for r in rows(state, "cells")
        }
        heads = {r["base"]: r["version"] for r in rows(state, "rsi_head")}
        sessions = rows(state, "rsi_sessions")
        docs = []
        for r in rows(state, "evaluations"):
            d = json.loads(r["document"])
            docs.append((r["evaluation_id"], None, d))
        for r in rows(state, "rsi_attempts"):
            d = (
                json.loads(r["report"])
                if r["report"]
                else {
                    **json.loads(r["plan"]),
                    "cases": [],
                    "parent": r["parent"],
                    "decision": "pending",
                    "changed_paths": [
                        e["path"] for e in json.loads(r["proposal"])["edits"]
                    ],
                    "hypothesis": json.loads(r["proposal"])["hypothesis"],
                }
            )
            docs.append((r["base"], r["session"], d))
        for base, sid, d in docs:
            v = d["version"]
            versions.append(
                dict(
                    id=f"{study}~{base}~{v}",
                    study=study,
                    base=base,
                    mode=mode,
                    session=sid,
                    active=heads.get(base) == v,
                    version=v,
                    parent=d.get("parent"),
                    decision=d.get("decision", "baseline"),
                    score=d.get("score"),
                    date=d.get("time"),
                    digest=d["strategy_digest"],
                    hypothesis=d.get("hypothesis"),
                    paths=d.get("changed_paths", []),
                    cases=[
                        dict(
                            case=c["case"],
                            score=c["score"],
                            cost=c["cost"],
                            duration_ms=c["duration_ms"],
                            tasks=[f"{study}~{r['cell_id']}" for r in c["runs"]],
                            samples=[
                                dict(
                                    task=f"{study}~{r['cell_id']}",
                                    index=r["run"],
                                    score=r["score"],
                                    duration_ms=r["duration_ms"],
                                )
                                for r in c["runs"]
                            ],
                        )
                        for c in d["cases"]
                    ],
                    prediction=d.get("prediction"),
                    evidence=d.get("evidence", []),
                    model=d["model_id"],
                    cost=d.get("cost"),
                    duration_ms=d.get("duration_ms"),
                )
            )
        for p in sorted(runs_path(state).glob("*/identity.json")):
            ident = read(p)
            if not ident:
                continue
            cell = ident["cell"]
            cid = ident["cell_id"]
            out = read(p.parent / "outcome.json", {})
            owner = lookup.get(cell["evaluation_id"])
            receipt = receipts.get(cid) or read(p.parent / "receipt.json")
            passed, checks = (
                totals.get(digest(receipt), (None, None)) if receipt else (None, None)
            )
            tasks.append(
                dict(
                    passed=passed,
                    checks=checks,
                    id=f"{study}~{cid}",
                    study=study,
                    mode=mode,
                    cell=cid,
                    session=ident["session_id"],
                    job=owner["id"] if owner and owner["kind"] == "task" else None,
                    case=cell["case_id"],
                    version=cell["expected_version"],
                    evaluation=cell["evaluation_id"],
                    index=cell["run_index"],
                    provider=ident["context"]["provider_id"],
                    model=ident["context"]["model_id"],
                    runtime_status=out.get("status", "running"),
                    status=owner["status"]
                    if owner and owner["kind"] == "task"
                    else out.get("status", "running"),
                    score=receipt.get("score") if receipt else None,
                    cost=receipt.get("cost") if receipt else None,
                    usage=out.get("usage"),
                    duration_ms=out.get("duration_ms"),
                    date=datetime.fromtimestamp(
                        p.stat().st_mtime, timezone.utc
                    ).isoformat(),
                )
            )
        for r in sessions:
            j = lookup.get(r["id"])
            vs = [v for v in versions if v["study"] == study and v["base"] == r["base"]]
            ref = next((v for v in vs if v["version"] == r["expected"]), None)
            evolutions.append(
                dict(
                    id=r["id"] if j else f"{study}~{r['id']}",
                    study=study,
                    mode=mode,
                    base=r["base"],
                    session=r["id"],
                    status=j["status"]
                    if j
                    else ("completed" if r["reason"] else "interrupted"),
                    reason=r["reason"],
                    config=json.loads(r["config"]),
                    version=r["expected"],
                    score=ref["score"] if ref else None,
                    date=j["created"]
                    if j
                    else max((v["date"] or "" for v in vs), default=""),
                    job=j["id"] if j else None,
                )
            )
    for j in alljobs:
        if j["kind"] == "evolution" and not any(e["id"] == j["id"] for e in evolutions):
            evolutions.append(
                dict(
                    id=j["id"],
                    study=j["study"],
                    mode=j["mode"],
                    base=j["config"]["base"],
                    session=j["id"],
                    status=j["status"],
                    reason=j["error"],
                    config=j["config"],
                    version=j["config"]["start_version"],
                    score=None,
                    date=j["created"],
                    job=j["id"],
                )
            )
    for items in (tasks, evolutions, versions):
        items.sort(key=lambda r: r["date"] or "", reverse=True)
    return tasks, evolutions, versions


def locate(items, id):
    return next((x for x in items if x["id"] == id), None) or missing(id)


def missing(id):
    raise KeyError(id)


def task_detail(id):
    t, e, v = index()
    byjob = next((x for x in t if x["job"] == id), None)
    item = byjob or next((x for x in t if x["id"] == id), None)
    job = next(
        (
            j
            for j in jobs()
            if j["kind"] == "task" and j["id"] in (id, (item or {}).get("job"))
        ),
        None,
    )
    if not item and not job:
        raise KeyError(id)
    if not item:
        return {
            "id": id,
            "job": id,
            "status": job["status"],
            "case": job["config"]["repo"],
            "mode": job["mode"],
            "date": job["created"],
            "version": job["config"]["start_version"],
            "provider": job["config"].get("provider"),
            "score": None,
            "request": job["config"],
            "error": job["error"],
            "events": [],
            "diff": "",
            "files": {},
            "receipt": None,
        }
    state = sources()[item["study"]][0]
    path = runs_path(state) / item["cell"]
    trace = []
    for line in (
        (path / "events.jsonl").read_text().splitlines()
        if (path / "events.jsonl").exists()
        else []
    ):
        try:
            trace.append(json.loads(line))
        except json.JSONDecodeError:
            break
    receipt = read(path / "receipt.json") or next(
        (
            json.loads(r["receipt"])
            for r in rows(state, "cells")
            if r["cell_id"] == item["cell"]
        ),
        None,
    )
    art = path / "artifact"
    if receipt:
        if tree_digest(art) != receipt["artifact_digest"] or (
            "trace_digest" in receipt
            and file_digest(path / "events.jsonl") != receipt["trace_digest"]
        ):
            raise ValueError("Sealed evidence failed its digest check")
    replays = [
        {"id": j["id"], "status": j["status"]}
        for j in jobs()
        if j["kind"] == "evaluation" and j["config"]["task_id"] == item["id"]
    ]
    files = contents(art) if art.is_dir() else {}
    return {
        **item,
        "status": job["status"] if job else item["status"],
        "runtime_status": item["runtime_status"],
        "request": job["config"] if job else None,
        "error": job["error"] if job else None,
        "receipt": receipt,
        "replays": replays,
        "events": trace,
        "files": files,
        "diff": diff(contents(repo_path(item["case"])), files) if art.is_dir() else "",
    }


def contents(folder):
    files = {}
    for path in regular_files(folder):
        with path.open("rb") as stream:
            preview = stream.read(65537)
        value = preview[:65536].decode("utf-8", errors="replace")
        if len(preview) > 65536:
            value += "\n[64 KiB preview · SHA-256 " + file_digest(path) + "]\n"
        files[path.relative_to(folder).as_posix()] = value
    return files


def diff(before, after):
    lines = []
    for path in sorted(before.keys() | after.keys()):
        if before.get(path) == after.get(path):
            continue
        lines.append(f"diff --git a/{path} b/{path}\n")
        if path not in before:
            lines.append("new file\n")
        if path not in after:
            lines.append("deleted file\n")
        for line in unified_diff(
            before.get(path, "").splitlines(True),
            after.get(path, "").splitlines(True),
            "a/" + path if path in before else "/dev/null",
            "b/" + path if path in after else "/dev/null",
        ):
            lines.append(line)
            if not line.endswith("\n"):
                lines.append("\n\\ No newline at end of file\n")
    return "".join(lines)


def snapshot(v):
    path = sources()[v["study"]][0] / "objects" / v["digest"]
    if not path.exists() and v["version"] == 1:
        path = DEFAULT
    if tree_digest(path) != v["digest"]:
        raise ValueError("Snapshot integrity check failed")
    return path


def comparison(a, b, ts):
    if (a["study"], a["base"]) != (b["study"], b["base"]):
        raise ValueError("Select versions from the same experiment lineage")
    before = {c["case"]: c for c in a["cases"]}
    metrics = []
    for c in b["cases"]:
        prev = before.get(c["case"])
        if prev:
            metrics.append(
                dict(
                    case=c["case"],
                    before=prev["score"],
                    after=c["score"],
                    delta=round(c["score"] - prev["score"], 2),
                )
            )

    def aggregate(v):
        ids = {t for c in v["cases"] for t in c["tasks"]}
        tasks = [t for t in ts if t["id"] in ids]

        def rate(pred):
            return (
                round(100 * sum(pred(t) for t in tasks) / len(tasks), 2)
                if tasks
                else None
            )

        usage = [
            sum(t["usage"].values())
            for t in tasks
            if t["usage"] and sum(t["usage"].values()) > 0
        ]
        totals = [(t["passed"], t["checks"]) for t in tasks if t["checks"]]
        test_pass = (
            round(100 * sum(p for p, c in totals) / sum(c for p, c in totals), 2)
            if totals and len(totals) == len(tasks)
            else None
        )
        return dict(
            test_pass=test_pass,
            completion=rate(lambda t: t["status"] == "completed"),
            full_score=rate(lambda t: t["score"] == 100),
            tokens=sum(usage) if tasks and len(usage) == len(tasks) else None,
            cost=v["cost"],
            duration_ms=v["duration_ms"],
        )

    return dict(
        before=a,
        after=b,
        cases=metrics,
        regression_rate=round(
            100 * sum(c["delta"] < 0 for c in metrics) / len(metrics), 2
        )
        if metrics
        else None,
        old_metrics=aggregate(a),
        new_metrics=aggregate(b),
        diff=diff(contents(snapshot(a)), contents(snapshot(b))),
    )


def findings(version, tasks):
    from mewcode.rsi.matrix import requests, observe, stored_receipts
    from mewcode.rsi.baseline import db_open
    from contextlib import closing

    state = sources()[version["study"]][0]
    lock = read(ROOT / "evaluation" / BENCHMARK / "freeze.lock.json")
    pins = {c["case_id"]: c for c in lock["benchmark"]["cases"]}
    ids = {id for c in version["cases"] for id in c["tasks"]}
    sample = next((t for t in tasks if t["id"] in ids), None)
    if not sample:
        return []
    with closing(db_open(state, read_only=True)) as conn:
        eid = sample["evaluation"]
        plan = json.loads(
            conn.execute(
                "SELECT plan FROM campaigns WHERE evaluation_id=?", (eid,)
            ).fetchone()[0]
        )
        receipts = stored_receipts(conn, eid)
    observations = []
    for req in requests(plan):
        cell = req.cell
        observation = observe(
            state, req, receipts[cell.case_id, cell.run_index], lock, pins[cell.case_id]
        )
        observations.append(
            dict(observation, task=f"{version['study']}~{cell.cell_id}")
        )
    return FailureAnalyzer().analyze(observations)


def evolution(id):
    tasks, es, vs = index()
    e = locate(es, id)
    vs = [v for v in vs if v["study"] == e["study"] and v["base"] == e["base"]]
    rounds = []
    for v in sorted(
        (v for v in vs if v["session"] == e["session"]), key=lambda x: x["version"]
    ):
        parent = next(p for p in vs if p["version"] == v["parent"])
        rounds.append(
            {**comparison(parent, v, tasks), "findings": findings(parent, tasks)}
        )
    base = next((v for v in vs if v["version"] == 1), None)
    job = next((j for j in jobs() if j["id"] == id), None)
    start = (
        job["config"]["start_version"]
        if job
        else rounds[0]["before"]["version"]
        if rounds
        else e["version"]
    )
    trial = next((v for v in vs if v["version"] == start), None)
    return {
        **e,
        "trial": trial,
        "rounds": rounds,
        "baseline": base,
        "findings": findings(trial, tasks) if trial else [],
        "error": next((j["error"] for j in jobs() if j["id"] == id), None),
    }
