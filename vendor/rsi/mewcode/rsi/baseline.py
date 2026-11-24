"""运行三题正式基线，或从同一份封存产物独立复评分。"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile

from mewcode.config import ConfigError, ProviderConfig, load_config
from .contracts import EvalCell
from .evaluator import CellFailure, Evaluator, ScoreRequest, check_run, verified_release
from .fixtures import BENCHMARK
from .runtime import MewCodeRuntime, ROOT, RunSpec, runtime_context


def safe_id(value: str):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,120}", value):
        raise CellFailure("invalid_request")
    return value


def request_for(plan: dict, case: str) -> ScoreRequest:
    if case not in plan["cases"]:
        raise CellFailure("invalid_request")
    cell = EvalCell(plan["evaluation_id"], case, 1, plan["version"], plan["run_fingerprint"])
    return ScoreRequest(cell, plan["strategy_digest"], plan["judge_digest"], plan["provider"],
                        plan["model_id"], plan["thinking_mode"])


def db_open(state: Path, *, read_only: bool = False):
    if state.resolve().is_relative_to(ROOT):
        raise CellFailure("invalid_request")
    path = state.resolve() / "control.sqlite"
    if read_only:
        return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=15)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.close(os.open(path, os.O_CREAT | os.O_RDWR, 0o600))
    db = sqlite3.connect(path, timeout=15)
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS campaigns (
            evaluation_id TEXT PRIMARY KEY, plan TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS cells (
            evaluation_id TEXT NOT NULL REFERENCES campaigns, case_id TEXT NOT NULL, run_index INTEGER NOT NULL,
            session_id TEXT UNIQUE NOT NULL, cell_id TEXT UNIQUE NOT NULL, receipt TEXT NOT NULL,
            PRIMARY KEY(evaluation_id, case_id, run_index)
        );
        CREATE TABLE IF NOT EXISTS evaluations (
            evaluation_id TEXT PRIMARY KEY REFERENCES campaigns, document TEXT NOT NULL
        );
    """)
    return db


def make_plan(evaluation_id: str, provider: ProviderConfig):
    lock, judge = verified_release()
    policy = json.loads((ROOT / "evaluation/policy/docker.json").read_text())
    spec = RunSpec(evaluation_id, lock["benchmark"]["cases"][0]["case_id"])
    ctx = runtime_context(lock, policy, provider, spec.max_turns, spec.timeout_seconds)
    mode = ("on" if provider.thinking else "off") if provider.protocol == "anthropic" else "provider_default"
    return {
        "evaluation_id": safe_id(evaluation_id), "agent_id": "mewcode", "version": 1, "benchmark_id": BENCHMARK,
        "benchmark_digest": lock["benchmark_digest"], "judge_digest": judge,
        "strategy_digest": lock["initial_strategy_digest"], "provider": provider.name, "model_id": provider.model,
        "thinking_mode": mode, "runs_per_case": 1, "context": asdict(ctx),
        "run_fingerprint": ctx.run_fingerprint(lock["initial_strategy_digest"]),
        "cases": [c["case_id"] for c in lock["benchmark"]["cases"]],
    }


def pin_plan(db, plan):
    with db:
        db.execute("INSERT OR IGNORE INTO campaigns VALUES (?,?)", (plan["evaluation_id"], json.dumps(plan)))
        existing, = db.execute("SELECT plan FROM campaigns WHERE evaluation_id=?", (plan["evaluation_id"],)).fetchone()
        if json.loads(existing) != plan:
            raise CellFailure("version_changed")


def get_receipt(db, evaluation_id: str, case: str):
    row = db.execute("SELECT receipt FROM cells WHERE evaluation_id=? AND case_id=? AND run_index=1",
                     (evaluation_id, case)).fetchone()
    return json.loads(row[0]) if row else None


def save_receipt(db, request: ScoreRequest, receipt: dict):
    request.validate_receipt(receipt)
    try:
        with db:
            db.execute("INSERT OR IGNORE INTO cells VALUES (?,?,?,?,?,?)", (
                request.cell.evaluation_id, request.cell.case_id, request.cell.run_index,
                receipt["session_id"], request.cell.cell_id, json.dumps(receipt),
            ))
            if get_receipt(db, request.cell.evaluation_id, request.cell.case_id) != receipt:
                raise CellFailure("evaluation_failed")
    except sqlite3.IntegrityError as exc:
        raise CellFailure("evaluation_failed") from exc


def publish(db, state: Path, plan: dict):
    eid = plan["evaluation_id"]
    with db:
        db.execute("BEGIN IMMEDIATE")
        actual = set(db.execute("SELECT case_id, run_index FROM cells WHERE evaluation_id=?", (eid,)))
        if actual != {(case, 1) for case in plan["cases"]}:
            raise CellFailure("evaluation_failed")
        receipts = [get_receipt(db, eid, c) for c in plan["cases"]]
        cases = []
        for case, r in zip(plan["cases"], receipts, strict=True):
            req = request_for(plan, case)
            req.validate_receipt(r)
            cases.append({"case": case, "score": r["score"], "cost": r["cost"],
                          "duration_ms": r["duration_ms"], "runs": [r]})
        if len({r["session_id"] for r in receipts}) != len(receipts):
            raise CellFailure("evaluation_failed")
        costs = [c["cost"] for c in cases if c["cost"] is not None]
        document = {**plan, "kind": "formal_baseline", "cases": cases,
                    "score": round(sum(c["score"] for c in cases) / len(cases), 2),
                    "cost": round(sum(costs) / len(cases), 6) if len(costs) == len(cases) else None,
                    "duration_ms": round(sum(c["duration_ms"] for c in cases) / len(cases))}
        row = db.execute("SELECT document FROM evaluations WHERE evaluation_id=?", (eid,)).fetchone()
        if row:
            stored = json.loads(row[0])
            if {k: v for k, v in stored.items() if k != "time"} != document:
                raise CellFailure("version_changed")
            document = stored
        else:
            document["time"] = datetime.now(timezone.utc).isoformat()
            db.execute("INSERT INTO evaluations VALUES (?,?)", (eid, json.dumps(document)))
    # 数据库事务是发布点；中断后可从同一条正式记录重建可读导出。
    folder = state / "baselines"
    folder.mkdir(mode=0o700, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=folder, delete=False) as tmp:
        json.dump(document, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
    os.replace(tmp.name, folder / f"{eid}.json")
    return document


async def baseline(state: Path, evaluation_id: str, provider: ProviderConfig):
    plan = make_plan(evaluation_id, provider)
    state = state.resolve()
    runtime = MewCodeRuntime(state / "runs")
    evaluator = Evaluator(state)
    db = db_open(state)
    try:
        pin_plan(db, plan)
        lock, _ = verified_release()
        for case in lock["benchmark"]["cases"]:
            name = case["case_id"]
            req = request_for(plan, name)
            stored = get_receipt(db, evaluation_id, name)
            if stored:
                req.validate_receipt(stored)
                outcome, _ = check_run(state / "runs" / req.cell.cell_id, req, lock, case)
                if any(stored[k] != outcome[k] for k in ("session_id", "trace_digest", "artifact_digest", "duration_ms")):
                    raise CellFailure("evaluation_failed")
                continue
            try:
                outcome = await runtime.run(
                    RunSpec(evaluation_id, name, expected_fingerprint=plan["run_fingerprint"]), provider)
            except (KeyError, OSError, RuntimeError, ValueError, TimeoutError) as exc:
                raise CellFailure("evaluation_failed") from exc
            if outcome["cell_id"] != req.cell.cell_id or outcome["status"] not in {"completed", "turn_limit", "timed_out"}:
                raise CellFailure("evaluation_failed")
            receipt = await evaluator.score(req)
            save_receipt(db, req, receipt)
        if make_plan(evaluation_id, provider) != plan:
            raise CellFailure("version_changed")
        return publish(db, state, plan)
    finally:
        db.close()


async def rescore(state: Path, evaluation_id: str, case: str):
    state = state.resolve()
    if not (state / "control.sqlite").is_file():
        raise CellFailure("invalid_request")
    db = db_open(state, read_only=True)
    try:
        row = db.execute("SELECT plan FROM campaigns WHERE evaluation_id=?", (safe_id(evaluation_id),)).fetchone()
        if not row:
            raise CellFailure("invalid_request")
        req = request_for(json.loads(row[0]), case)
        recorded = get_receipt(db, evaluation_id, case)
        if not recorded:
            raise CellFailure("invalid_request")
        receipt = await Evaluator(state).score(req)
        if receipt != recorded:
            raise CellFailure("evaluation_failed")
        return receipt
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    run = sub.add_parser("baseline")
    replay = sub.add_parser("rescore")
    for p in (run, replay):
        p.add_argument("--state-dir", type=Path, required=True)
        p.add_argument("--evaluation", required=True)
    run.add_argument("--provider", required=True)
    run.add_argument("--provider-config", type=Path, required=True)
    replay.add_argument("--case", required=True)
    args = parser.parse_args()
    try:
        if args.operation == "baseline":
            providers = [p for p in load_config(args.provider_config).providers if p.name == args.provider]
            if len(providers) != 1:
                raise CellFailure("invalid_request")
            result = asyncio.run(baseline(args.state_dir, args.evaluation, providers[0]))
        else:
            result = asyncio.run(rescore(args.state_dir, args.evaluation, args.case))
    except CellFailure as exc:
        print(json.dumps({"status": "failed", "failure_code": exc.code}), file=sys.stderr)
        return 1
    except (ConfigError, sqlite3.Error, OSError, ValueError) as exc:
        code = "invalid_request" if isinstance(exc, ConfigError) else "evaluation_failed"
        print(json.dumps({"status": "failed", "failure_code": code}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
