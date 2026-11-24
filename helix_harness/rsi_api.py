"""RSI 旧接口与按需启动的本机实验服务。"""
import asyncio
import fcntl
import json
import os
import subprocess
import sys
import uuid

from .paths import PROJECT

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

processes = {}
monitors = set()
_server_lock = None
_mock_server = None


async def ensure_store():
    global _server_lock
    if _server_lock is not None:
        return
    lock = None
    try:
        from .store import HOME, ROOT, TERMINAL, finish, init, jobs

        if HOME.is_relative_to(PROJECT) or HOME.is_relative_to(ROOT):
            raise ValueError("RSI storage must be outside source tree")
        HOME.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = (HOME / "server.lock").open("a")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        init()
        for job in jobs():
            if job["status"] not in TERMINAL:
                finish(job["id"], "interrupted", "backend_restart")
    except Exception as exc:
        if lock is not None:
            lock.close()
        raise HTTPException(503, "RSI storage unavailable") from exc
    _server_lock = lock


def ensure_mock():
    global _mock_server
    from . import mock
    if _mock_server is None:
        try:
            _mock_server = mock.serve()
        except OSError as exc:
            raise HTTPException(503, "Demo provider unavailable") from exc


async def shutdown():
    global _server_lock, _mock_server
    try:
        for process in list(processes.values()):
            if process.returncode is None:
                process.terminate()
        if monitors:
            await asyncio.gather(*list(monitors), return_exceptions=True)
        if _mock_server is not None:
            await asyncio.to_thread(_mock_server.shutdown)
            _mock_server.server_close()
    finally:
        if _server_lock is not None:
            _server_lock.close()
        _server_lock = _mock_server = None


router = APIRouter(dependencies=[Depends(ensure_store)])


class Form(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Task(Form):
    repo: str = Field(max_length=100)
    prompt: str = Field(default="", max_length=6000)
    version: str = "default"
    provider: str = "demo-mock"
    turns: int = Field(default=12, ge=1, le=24)
    timeout: int = Field(default=90, ge=5, le=180)


class Evolution(Form):
    benchmark: str = "repo-python-v1"
    version: str = "default"
    provider: str = "demo-mock"
    rounds: int = Field(default=2, ge=1, le=5)
    runs: int = Field(default=1, ge=1, le=3)
    target: float = Field(default=100, ge=1, le=100)


class Replay(Form):
    task_id: str = Field(min_length=1, max_length=200)


class Repo(Form):
    name: str = Field(pattern="^[a-zA-Z0-9_-]{1,64}$")


def job_record(id, kind):
    from . import catalog
    from .store import jobs

    return catalog.locate([j for j in jobs() if j["kind"] == kind], id)


async def monitor(id, proc):
    from .store import TERMINAL, jobs, finish

    try:
        await proc.wait()
        job = next(j for j in jobs() if j["id"] == id)
        if job["status"] not in TERMINAL:
            finish(
                id,
                "cancelled" if job["status"] == "cancelling" else "failed",
                "worker_exit",
            )
    finally:
        processes.pop(id, None)
        if proc.stdin:
            proc.stdin.close()


async def launch(kind, config):
    from mewcode.rsi.fixtures import BENCHMARK, tree_digest
    from . import catalog, mock
    from .store import DEFAULT, HISTORY, jobs

    if len(processes) >= 3:
        raise HTTPException(429, "Three jobs are already active")
    providers = mock.providers()
    if config["provider"] not in providers:
        raise ValueError("Unknown provider")
    provider = providers[config["provider"]]
    mode = "Mock" if provider.name == "demo-mock" else "Real"
    id = uuid.uuid4().hex
    study = id
    cfg = dict(config, start_version=1, digest=tree_digest(DEFAULT))
    if kind == "evolution":
        if cfg["benchmark"] != BENCHMARK:
            raise ValueError("Unknown benchmark")
        cfg.update(base="base-" + id[:12], new_line=True)
    else:
        repo = catalog.locate(catalog.repo_list(), cfg["repo"])
        if not cfg["prompt"].strip():
            cfg["prompt"] = repo["statement"]
    if cfg["version"] != "default":
        v = catalog.locate(catalog.index()[2], cfg["version"])
        catalog.snapshot(v)
        cfg.update(start_version=v["version"], digest=v["digest"])
        if kind == "evolution":
            if v["study"] in HISTORY or not v["active"]:
                raise HTTPException(
                    409, "Select an active Web lineage; archives are read-only"
                )
            original = next(
                j
                for j in jobs()
                if j["study"] == v["study"] and j["kind"] == "evolution"
            )
            if original["config"]["provider"] != provider.name:
                raise HTTPException(409, "Lineage provider is pinned")
            study = v["study"]
            cfg.update(base=v["base"], new_line=False)
    if mode == "Mock":
        ensure_mock()
    return await enqueue(kind, cfg, study, mode, id)


async def enqueue(kind, cfg, study, mode, id):
    from .store import db, finish, now

    with db() as con:
        con.execute("BEGIN IMMEDIATE")
        if (
            con.execute(
                "SELECT count(*) FROM jobs WHERE status IN ('queued','running','cancelling')"
            ).fetchone()[0]
            >= 3
        ):
            raise HTTPException(429, "Three jobs are already active")
        if (
            kind == "evolution"
            and con.execute(
                "SELECT 1 FROM jobs WHERE study=? AND status IN ('queued','running','cancelling')",
                (study,),
            ).fetchone()
        ):
            raise HTTPException(409, "An evolution is running on this lineage")
        con.execute(
            "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)",
            (id, kind, json.dumps(cfg), study, mode, "queued", now(), None, None),
        )
    try:
        entry = "import sys,asyncio; sys.path.insert(0,sys.argv[1]); from helix_harness.worker import main; asyncio.run(main(sys.argv[2]))"
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-B",
            "-c",
            entry,
            str(PROJECT),
            id,
            cwd=PROJECT,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    except OSError:
        finish(id, "failed", "worker_launch_failed")
        raise HTTPException(503, "Worker unavailable")
    processes[id] = proc
    watcher = asyncio.create_task(monitor(id, proc))
    monitors.add(watcher)
    watcher.add_done_callback(monitors.discard)
    return {"id": id, "status": "queued", "mode": mode}


@router.get("/api/health")
def health():
    from .store import ROOT, read

    from mewcode.rsi.evaluator import verified_release, CellFailure

    policy = read(ROOT / "evaluation/policy/docker.json")
    try:
        verified_release()
        release = True
    except CellFailure:
        release = False
    try:
        result = subprocess.run(
            [
                "docker",
                "image",
                "inspect",
                policy["image"],
                "--format",
                "{{.Os}}/{{.Architecture}}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        ready = result.returncode == 0 and result.stdout.strip() == policy["platform"]
    except (OSError, subprocess.TimeoutExpired):
        ready = False
    return dict(engine="MewCode", release_verified=release, sandbox_ready=ready)


@router.get("/api/providers")
def providers():
    from . import mock

    return [
        dict(id=p.name, model=p.model, mode="Mock" if p.name == "demo-mock" else "Real")
        for p in mock.providers().values()
    ]


@router.get("/api/repositories")
def repos():
    from . import catalog

    return catalog.repo_list()


@router.post("/api/repositories", status_code=201)
def add_repo(form: Repo):
    from . import repositories

    return repositories.register(form.name)


@router.get("/api/repositories/{id}")
def repository(id: str):
    from . import catalog

    return catalog.locate(repos(), id)


@router.get("/api/repositories/{id}/files")
def files(id: str):
    from . import catalog

    return list(catalog.contents(catalog.repo_path(id)))


@router.get("/api/repositories/{id}/contents")
def repo_contents(id: str):
    from . import catalog

    return catalog.contents(catalog.repo_path(id))


@router.get("/api/benchmarks")
def benches():
    from mewcode.rsi.fixtures import BENCHMARK

    return [
        dict(
            id=BENCHMARK,
            split="unspecified",
            cases=[r for r in repos() if r["kind"] == "Benchmark"],
        )
    ]


@router.get("/api/benchmarks/{id}")
def bench(id: str):
    from . import catalog

    return catalog.locate(benches(), id)


@router.post("/api/tasks/run", status_code=202)
async def start_task(form: Task):
    return await launch("task", form.model_dump())


@router.post("/api/evaluations/run", status_code=202)
async def run_evaluation(form: Replay):
    from . import catalog

    task = catalog.task_detail(form.task_id)
    receipt = task["receipt"]
    if not receipt or "judge_digest" not in receipt:
        raise HTTPException(409, "A frozen benchmark receipt is required")
    id = uuid.uuid4().hex
    cfg = dict(
        task_id=task["id"],
        provider=task["provider"],
        start_version=task["version"],
        digest=receipt["strategy_digest"],
    )
    return await enqueue("evaluation", cfg, id, task["mode"], id)


@router.post("/api/evolution/start", status_code=202)
async def start_evolution(form: Evolution):
    return await launch("evolution", form.model_dump())


@router.get("/api/tasks")
def tasks():
    from . import catalog, mock
    from .store import jobs

    ts = catalog.index()[0]
    provider_map = mock.providers()
    for j in jobs():
        if j["kind"] != "task":
            continue
        row = next((r for r in ts if r["job"] == j["id"]), None)
        if row:
            row["status"] = j["status"]
        else:
            p = provider_map.get(j["config"].get("provider"))
            ts.append(
                dict(
                    id=j["id"],
                    job=j["id"],
                    case=j["config"]["repo"],
                    mode=j["mode"],
                    status=j["status"],
                    date=j["created"],
                    score=None,
                    version=j["config"]["start_version"],
                    provider=j["config"].get("provider"),
                    model=p.model if p else j["config"].get("provider"),
                )
            )
    return sorted(ts, key=lambda t: t["date"], reverse=True)


@router.get("/api/tasks/{id}")
def task(id: str):
    from . import catalog

    return catalog.task_detail(id)


@router.get("/api/tasks/{id}/trajectory")
def trajectory(id: str):
    return task(id)["events"]


@router.get("/api/tasks/{id}/diff")
def task_diff(id: str):
    return {"diff": task(id)["diff"]}


@router.get("/api/tasks/{id}/evaluation")
def evaluation(id: str):
    return task(id)["receipt"]


@router.get("/api/evolution")
def evolutions():
    from . import catalog

    return catalog.index()[1]


@router.get("/api/evolution/{id}")
def evolution(id: str):
    from . import catalog

    return catalog.evolution(id)


@router.get("/api/evolution/{id}/rounds")
def rounds(id: str):
    return evolution(id)["rounds"]


@router.post("/api/{group}/{id}/cancel")
async def cancel(group: str, id: str):
    from .store import TERMINAL, db

    job = job_record(
        id,
        {"tasks": "task", "evolution": "evolution", "evaluations": "evaluation"}[group],
    )
    if job["status"] in TERMINAL or id not in processes:
        raise HTTPException(409, "Job has finished")
    with db() as con:
        con.execute(
            "UPDATE jobs SET status='cancelling' WHERE id=? AND status IN ('queued','running')",
            (id,),
        )
    proc = processes[id]
    if proc.returncode is None:
        proc.terminate()
    return {"id": id, "status": "cancelling"}


@router.get("/api/{group}/{id}/events")
async def stream(group: str, id: str, request: Request, after: int = Query(0, ge=0)):
    from . import catalog
    from .store import TERMINAL, jobs, journal

    kind = {"tasks": "task", "evolution": "evolution", "evaluations": "evaluation"}[
        group
    ]
    archived = None
    if not any(j["id"] == id and j["kind"] == kind for j in jobs()):
        if kind == "task":
            task = await asyncio.to_thread(catalog.task_detail, id)
            archived = [
                dict(
                    event_id=e["seq"],
                    run_id=id,
                    event_type={
                        "tool_use": "tool_call",
                        "model_request": "thinking",
                    }.get(e["type"], e["type"]),
                    timestamp=None,
                    data=e,
                )
                for e in task["events"]
            ]
        elif kind == "evolution":
            report = await asyncio.to_thread(catalog.evolution, id)
            recorded = (
                [("evaluation_completed", report["baseline"])]
                if report["baseline"]
                else []
            )
            recorded.extend(
                ("version_" + r["after"]["decision"], r["after"])
                for r in report["rounds"]
                if r["after"]["date"]
            )
            archived = [
                dict(
                    event_id=i,
                    run_id=id,
                    event_type=k,
                    timestamp=v["date"],
                    data={"version": v["version"], "score": v["score"]},
                )
                for i, (k, v) in enumerate(recorded, 1)
            ]
        else:
            raise KeyError(id)
    try:
        cursor = max(after, int(request.headers.get("last-event-id", "0")))
    except ValueError:
        raise HTTPException(400, "Invalid event cursor")

    async def events():
        nonlocal cursor
        if archived is not None:
            for e in archived:
                if e["event_id"] > cursor:
                    yield f"id: {e['event_id']}\ndata: {json.dumps(e)}\n\n"
            return
        while True:
            batch = journal(id, cursor)
            for e in batch:
                cursor = e["event_id"]
                yield f"id: {cursor}\ndata: {json.dumps(e)}\n\n"
                if e["event_type"] in (
                    "run_completed",
                    "run_cancelled",
                    "run_interrupted",
                    "run_failed",
                ):
                    return
            if not batch:
                if job_record(id, kind)["status"] in TERMINAL and not journal(
                    id, cursor
                ):
                    return
                if await request.is_disconnected():
                    return
                yield ": heartbeat\n\n"
                await asyncio.sleep(0.4)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/api/evaluations")
def evaluations():
    from . import catalog

    return [t for t in catalog.index()[0] if t["score"] is not None]


@router.get("/api/evaluations/compare")
def compare(left: str, right: str):
    from . import catalog

    ts, _, vs = catalog.index()
    return catalog.comparison(catalog.locate(vs, left), catalog.locate(vs, right), ts)


@router.get("/api/evaluations/{id}")
def eval_detail(id: str):
    from .store import HOME, jobs, read

    replay = next(
        (j for j in jobs() if j["kind"] == "evaluation" and j["id"] == id), None
    )
    if replay:
        return dict(
            id=id,
            status=replay["status"],
            task_id=replay["config"]["task_id"],
            error=replay["error"],
            receipt=read(HOME / "studies" / id / "verified-receipt.json"),
        )
    return evaluation(id)


@router.get("/api/versions")
def versions():
    from . import catalog

    return catalog.index()[2]


@router.get("/api/versions/{id}")
def version(id: str):
    from . import catalog

    vs = versions()
    v = catalog.locate(vs, id)
    files = catalog.contents(catalog.snapshot(v))
    parent = next(
        (
            p
            for p in vs
            if p["study"] == v["study"]
            and p["base"] == v["base"]
            and p["version"] == v["parent"]
        ),
        None,
    )
    return {
        **v,
        "files": files,
        "diff": catalog.diff(catalog.contents(catalog.snapshot(parent)), files)
        if parent
        else "",
        "manual_rollback": False,
    }


@router.get("/api/versions/{id}/diff")
def version_diff(id: str):
    return {"diff": version(id)["diff"]}


@router.post("/api/versions/{id}/rollback")
def rollback(id: str):
    from . import catalog

    catalog.locate(versions(), id)
    raise HTTPException(
        409,
        "VersionManager retains the parent automatically on rejection. Manual rollback is unavailable.",
    )


@router.get("/api/dashboard/overview")
def overview():
    from mewcode.rsi.fixtures import BENCHMARK
    from . import catalog

    ts, es, vs = catalog.index()
    return dict(
        engine="MewCode",
        benchmark=BENCHMARK,
        tasks=ts,
        evolutions=es,
        versions=vs,
        benchmark_tasks=len(benches()[0]["cases"]),
    )


@router.get("/api/dashboard/recent-runs")
def recent():
    return dict(tasks=tasks()[:10], evolution=evolutions()[:10])


@router.get("/api/dashboard/metrics")
def metrics():
    from . import catalog

    ts, es, vs = catalog.index()
    return {
        mode: dict(
            tasks=len([t for t in ts if t["mode"] == mode]),
            experiments=sum(e["mode"] == mode for e in es),
            scored=sum(t["mode"] == mode and t["score"] is not None for t in ts),
        )
        for mode in ("Real", "Mock")
    }
