"""One cancellable OS process per job, with a parent-liveness pipe."""

import asyncio
import json
import os
import shutil
import signal
import sys

from mewcode.rsi.baseline import baseline
from mewcode.rsi.contracts import EvalCell
from mewcode.rsi.evaluator import Evaluator, ScoreRequest, verified_release
from mewcode.rsi.fixtures import tree_digest
from mewcode.rsi.orchestrator import optimize
from mewcode.rsi.runtime import MewCodeRuntime, RunSpec
from . import catalog, custom
from .mock import providers, proposer
from .store import sources, HOME, DEFAULT, db, emit, finish, jobs, read


class Tail:
    def __init__(self, job, state, configs):
        self.job, self.state, self.cfg = job, state, configs
        self.offset = {}
        self.sessions = {}

    def drain(self):
        for path in (self.state / "runs").glob("*/events.jsonl"):
            if path not in self.sessions:
                identity = read(path.parent / "identity.json")
                if not identity:
                    continue
                self.sessions[path] = identity["cell"]
            cell = self.sessions[path]
            if cell["evaluation_id"] != self.job and (
                self.cfg.get("new_line") is False
                and cell["expected_version"] <= self.cfg["start_version"]
            ):
                continue
            if cell["evaluation_id"] != self.job and self.cfg.get("new_line") is False:
                from .store import rows

                if not any(
                    r["session"] == self.job
                    and json.loads(r["plan"])["evaluation_id"] == cell["evaluation_id"]
                    for r in rows(self.state, "rsi_attempts")
                ):
                    continue
            with path.open("rb") as stream:
                stream.seek(self.offset.get(path, 0))
                while line := stream.readline():
                    if not line.endswith(b"\n"):
                        break
                    e = json.loads(line)
                    kind = {"model_request": "thinking", "tool_use": "tool_call"}.get(
                        e["type"], e["type"]
                    )
                    emit(
                        self.job,
                        kind,
                        dict(
                            e,
                            case=cell["case_id"],
                            version=cell["expected_version"],
                            cell=path.parent.name,
                        ),
                        f"{path.parent.name}:{e['seq']}",
                    )
                    self.offset[path] = stream.tell()

    async def watch(self):
        while True:
            self.drain()
            await asyncio.sleep(0.15)


async def execute(j):
    id = j["id"]
    cfg = j["config"]
    state = HOME / "studies" / j["study"]
    state.mkdir(parents=True, exist_ok=True)
    provider = providers()[cfg["provider"]] if j["kind"] != "evaluation" else None
    with db() as con:
        con.execute("UPDATE jobs SET status='running' WHERE id=?", (id,))
    tail = Tail(id, state, cfg)

    def notify(kind, data):
        tail.drain()
        emit(id, kind, data)

    emit(
        id,
        "run_started",
        {
            "mode": j["mode"],
            "provider": cfg["provider"],
            "version": cfg["start_version"],
        },
    )
    watch = asyncio.create_task(tail.watch())
    try:
        verified_release()
        if j["kind"] == "evaluation":
            task = catalog.task_detail(cfg["task_id"])
            original = task["receipt"]
            source = sources()[task["study"]][0] / "runs" / task["cell"]
            replay = state / "replay"
            target = replay / "runs" / task["cell"]
            target.mkdir(parents=True)
            for name in ("identity.json", "events.jsonl", "outcome.json"):
                shutil.copyfile(source / name, target / name)
            from mewcode.rsi.fixtures import copy_files

            (target / "artifact").mkdir()
            copy_files(source / "artifact", target / "artifact")
            cell = EvalCell(**read(target / "identity.json")["cell"])
            req = ScoreRequest(
                cell,
                original["strategy_digest"],
                original["judge_digest"],
                original["provider"],
                original["model_id"],
                original["thinking_mode"],
            )
            notify(
                "evaluation_started",
                {"case": cell.case_id, "scope": "sealed artifact replay"},
            )
            receipt = await Evaluator(replay).score(req)
            if receipt != original:
                raise RuntimeError("receipt_mismatch")
            (state / "verified-receipt.json").write_text(json.dumps(receipt))
            notify("evaluation_completed", {"score": receipt["score"], "matched": True})
        elif j["kind"] == "evolution":
            if cfg["new_line"]:
                notify("trial_started", {"benchmark": cfg["benchmark"], "cases": 3})
                notify("evaluation_started", {"scope": "baseline matrix"})
                base = await baseline(state, cfg["base"], provider)
                notify(
                    "evaluation_completed",
                    {"scope": "baseline matrix", "score": base["score"]},
                )
            else:
                notify("trial_reused", {"version": cfg["start_version"]})
            report = await optimize(
                state,
                cfg["base"],
                id,
                provider,
                proposer(provider),
                runs=cfg["runs"],
                rounds=cfg["rounds"],
                target=cfg["target"],
                notify=notify,
                starting_version=cfg["start_version"],
            )
            notify(
                "evolution_completed",
                {
                    "stop_reason": report["stop_reason"],
                    "score": report["reference_score"],
                },
            )
        else:
            snapshot = (
                DEFAULT
                if cfg["version"] == "default"
                else catalog.snapshot(
                    catalog.locate(catalog.index()[2], cfg["version"])
                )
            )
            if tree_digest(snapshot) != cfg["digest"]:
                raise ValueError("Harness snapshot changed")
            if cfg["repo"].startswith("repo-"):
                await custom.run(id, state, cfg, provider, snapshot)
                return
            # Free text is an explicit task-local instruction overlay. Published snapshots are immutable.
            effective = state / "task-strategy"
            shutil.copytree(snapshot, effective)
            with (effective / "prompt.md").open("a") as out:
                out.write("\n\n## Workspace request\n" + cfg["prompt"] + "\n")
            digest = tree_digest(effective)
            spec = RunSpec(
                id,
                cfg["repo"],
                version=cfg["start_version"],
                max_turns=cfg["turns"],
                timeout_seconds=cfg["timeout"],
                strategy_dir=effective,
                strategy_digest=digest,
            )
            outcome = await MewCodeRuntime(state / "runs").run(spec, provider)
            notify(
                "task_completed",
                {"status": outcome["status"], "cell": outcome["cell_id"]},
            )
            if outcome["status"] not in ("completed", "turn_limit", "timed_out"):
                raise RuntimeError("runtime_failed")
            folder = state / "runs" / outcome["cell_id"]
            cell = EvalCell(**read(folder / "identity.json")["cell"])
            _, judge = verified_release()
            mode = (
                ("on" if provider.thinking else "off")
                if provider.protocol == "anthropic"
                else "provider_default"
            )
            req = ScoreRequest(cell, digest, judge, provider.name, provider.model, mode)
            notify("evaluation_started", {"case": spec.case_id})
            receipt = await Evaluator(state).score(req)
            (folder / "receipt.json").write_text(json.dumps(receipt))
            notify(
                "evaluation_completed",
                {"score": receipt["score"], "case": spec.case_id},
            )
    finally:
        watch.cancel()
        await asyncio.gather(watch, return_exceptions=True)
        tail.drain()


async def main(id):
    j = next(j for j in jobs() if j["id"] == id)
    task = asyncio.create_task(execute(j))
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, task.cancel)

    def parent_gone():
        if not os.read(0, 1):
            loop.remove_reader(0)
            task.cancel()

    loop.add_reader(0, parent_gone)
    try:
        await task
    except asyncio.CancelledError:
        finish(id, "cancelled")
    except Exception as exc:
        import traceback

        traceback.print_exc(file=sys.stderr)
        finish(id, "failed", getattr(exc, "code", type(exc).__name__))
    else:
        finish(id, "completed")
    finally:
        loop.remove_reader(0)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
