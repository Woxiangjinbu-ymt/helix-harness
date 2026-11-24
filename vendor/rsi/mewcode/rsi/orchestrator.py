"""Default RSI 正常入口及可恢复的一轮策略比较。"""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

import yaml
from mewcode.config import ConfigError, load_config
from .baseline import db_open, make_plan
from .contracts import digest
from .default_optimizer import DefaultStrategyOptimizer, InvalidProposal
from .evaluator import CellFailure, Evaluator, verified_release
from .failure_analysis import FailureAnalyzer
from .fixtures import regular_files
from .matrix import observe, requests, stored_receipts
from .runtime import MewCodeRuntime, ROOT, RunSpec
from .versions import VersionManager


def progress(db, sid):
    # 同一份读快照里读取榜、指针及实验日志，避免观察到并发晋升的两个时刻。
    if db.in_transaction:
        return _progress(db, sid)
    with db:
        db.execute("BEGIN")
        return _progress(db, sid)


def _progress(db, sid):
    row = db.execute("SELECT base,config,expected,reason FROM rsi_sessions WHERE id=?", (sid,)).fetchone()
    if not row:
        raise CellFailure("invalid_request")
    base, config, expected, reason = row
    board = [json.loads(r[0]) for r in db.execute(
        "SELECT document FROM rsi_board WHERE base=? ORDER BY version", (base,))]
    current = db.execute("SELECT version FROM rsi_head WHERE base=?", (base,)).fetchone()[0]
    if board[-1]["version"] != current:
        raise CellFailure("version_changed")
    attempts = []
    for version, parent, plan, proposal, decision, report in db.execute(
            "SELECT version,parent,plan,proposal,decision,report FROM rsi_attempts WHERE session=? ORDER BY version",
            (sid,)):
        if report:
            attempts.append(json.loads(report))
        else:
            eid = json.loads(plan)["evaluation_id"]
            attempts.append({"version": version, "parent": parent, "evaluation_id": eid, "decision": decision,
                             "hypothesis": json.loads(proposal)["hypothesis"], "scored_cells": len(stored_receipts(db, eid))})
    # 已结束会话保留当时的结果；表头另行显示这条版本线目前的 Reference。
    retained = next(doc for doc in board if doc["version"] == (expected if reason else current))
    return {"session": sid, "baseline": base, "config": json.loads(config), "head_version": current,
            "reference_version": retained["version"], "reference_score": retained["score"],
            "stop_reason": reason or ("reference_changed" if current != expected else None),
            "completed_rounds": sum(r["decision"] in ("accepted", "rejected") for r in attempts),
            "attempts": attempts, "scoreboard": board}


def public_input(manager, reference, history):
    snapshot = manager.objects.get(reference["strategy_digest"])
    observations = manager.evidence(reference)
    previous = []
    for row in history:
        report = json.loads(row["report"])
        previous.append({"version": row["version"], "decision": row["decision"], "hypothesis": report["hypothesis"],
                         "prediction": report["prediction"], "changed_paths": report["changed_paths"],
                         "case_scores": {c["case"]: c["score"] for c in report["cases"]},
                         "findings": FailureAnalyzer().analyze(manager.evidence(report))})
    public = ROOT / "evaluation" / manager.plan["benchmark_id"] / "public"
    statements = {}
    for pin in manager.lock["benchmark"]["cases"]:
        case = pin["case_id"]
        raw = (public / case / "statement.md").read_bytes()
        if hashlib.sha256(raw).hexdigest() != pin["statement_digest"]:
            raise CellFailure("benchmark_invalid")
        statements[case] = raw.decode()
    return {"parent": reference["version"], "score": reference["score"],
            "strategy": {p.relative_to(snapshot).as_posix(): p.read_text() for p in regular_files(snapshot)},
            "statements": statements,
            "observations": observations, "findings": FailureAnalyzer().analyze(observations), "history": previous}


def _silent(kind, data):
    pass


async def evaluate_pending(manager, sid, row, provider, runtime, evaluator, notify=_silent):
    plan = json.loads(row["plan"])
    snapshot = manager.objects.get(plan["strategy_digest"])
    cached = stored_receipts(manager.db, plan["evaluation_id"])
    pins = {case["case_id"]: case for case in manager.lock["benchmark"]["cases"]}
    for req in requests(plan):
        cell = req.cell
        manager.current(sid)
        key = (cell.case_id, cell.run_index)
        if key in cached:
            observe(manager.state, req, cached[key], manager.lock, pins[cell.case_id])
            continue
        run = manager.state / "runs" / cell.cell_id
        if not run.exists():
            spec = RunSpec(cell.evaluation_id, cell.case_id, cell.run_index, cell.expected_version,
                           strategy_dir=snapshot, strategy_digest=plan["strategy_digest"],
                           expected_fingerprint=cell.run_fingerprint)
            outcome = await runtime.run(spec, provider)
            if outcome["cell_id"] != cell.cell_id:
                raise CellFailure("evaluation_failed")
        # 已登记的 cell 只尝试从原封存产物评分；未封存时保留现场。
        notify("evaluation_started", {"case": cell.case_id, "version": row["version"]})
        receipt = await evaluator.score(req)
        notify("evaluation_completed", {"case": cell.case_id, "version": row["version"], "score": receipt["score"]})
        observe(manager.state, req, receipt, manager.lock, pins[cell.case_id])
        with manager.db:
            manager.db.execute("BEGIN IMMEDIATE")
            manager.current(sid)
            try:
                manager.db.execute("INSERT OR IGNORE INTO cells VALUES (?,?,?,?,?,?)",
                                   (cell.evaluation_id, cell.case_id, cell.run_index, receipt["session_id"],
                                    cell.cell_id, json.dumps(receipt)))
                if stored_receipts(manager.db, cell.evaluation_id).get(key) != receipt:
                    raise CellFailure("evaluation_failed")
            except sqlite3.IntegrityError as exc:
                raise CellFailure("evaluation_failed") from exc
    if make_plan(manager.base, provider) != manager.plan:
        raise CellFailure("version_changed")
    return manager.finish(sid, row["version"])


async def optimize(state, baseline_id, sid, provider, optimizer_provider, *, target=100, runs=2, rounds=2, notify=_silent, starting_version=None):
    if (type(target) not in (float, int) or not 0 <= target <= 100
            or any(type(n) is not int or n < 1 for n in (runs, rounds))):
        raise CellFailure("invalid_request")
    state = Path(state).resolve()
    plan = make_plan(baseline_id, provider)
    lock, judge = verified_release()
    if lock["benchmark_digest"] != plan["benchmark_digest"] or judge != plan["judge_digest"]:
        raise CellFailure("version_changed")
    manager = VersionManager(state, plan, lock)
    try:
        config = {"target": target, "runs": runs, "rounds": rounds, "optimizer_provider": optimizer_provider.name,
                  "optimizer_model": optimizer_provider.model,
                  "optimizer_config": digest([optimizer_provider.protocol, optimizer_provider.base_url,
                                              optimizer_provider.thinking, optimizer_provider.get_context_window(),
                                              optimizer_provider.get_max_output_tokens()])}
        manager.open_session(sid, config)
        if starting_version is not None and manager.current(sid)["version"] != starting_version:
            raise CellFailure("reference_changed")
        optimizer = DefaultStrategyOptimizer(optimizer_provider)
        runtime, evaluator = MewCodeRuntime(state / "runs"), Evaluator(state)
        while not manager.session(sid)["reason"]:
            reference = manager.current(sid)
            history = manager.attempts(sid)
            pending = history[-1] if history and history[-1]["decision"] == "pending" else None
            if not pending:
                if reference["score"] >= target:
                    manager.stop(sid, "target_reached", len(history))
                    break
                if len(history) >= rounds:
                    manager.stop(sid, "round_limit", len(history))
                    break
                try:
                    stage = {"round": len(history) + 1, "parent": reference["version"]}
                    notify("failure_analysis_started", stage)
                    public = public_input(manager, reference, history)
                    notify("failure_analysis_completed", dict(stage, findings=public["findings"]))
                    notify("optimization_started", stage)
                    data = await optimizer.propose(public)
                    notify("optimization_completed", dict(stage, proposed=data is not None))
                    if data is None:
                        manager.stop(sid, "no_proposal", len(history))
                        break
                    pending = manager.stage(sid, reference, data, len(history) + 1)
                    notify("version_created", dict(stage, version=pending["version"], hypothesis=data["hypothesis"]))
                except InvalidProposal:
                    manager.stop(sid, "invalid_proposal", len(history))
                    break
            info = {"round": len(manager.attempts(sid)), "version": pending["version"], "parent": pending["parent"]}
            notify("validation_started", info)
            report = await evaluate_pending(manager, sid, pending, provider, runtime, evaluator, notify)
            notify("validation_completed", dict(info, score=report["score"]))
            notify("version_" + report["decision"], info)
            if report["decision"] == "rejected":
                notify("rollback_completed", dict(info, retained=report["parent"]))
        return progress(manager.db, sid)
    finally:
        manager.db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    opt = sub.add_parser("optimize")
    show = sub.add_parser("inspect")
    for cmd in (opt, show):
        cmd.add_argument("--state-dir", type=Path, required=True)
        cmd.add_argument("--session", required=True)
    opt.add_argument("--baseline", required=True)
    opt.add_argument("--provider-config", type=Path, required=True)
    opt.add_argument("--provider", required=True)
    opt.add_argument("--optimizer-provider")
    opt.add_argument("--target", type=float, default=100)
    opt.add_argument("--runs", type=int, default=2)
    opt.add_argument("--rounds", type=int, default=2)
    show.add_argument("--scoreboard-yaml", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "optimize":
            providers = {p.name: p for p in load_config(args.provider_config).providers}
            if args.provider not in providers or (args.optimizer_provider or args.provider) not in providers:
                raise CellFailure("invalid_request")
            report = asyncio.run(optimize(args.state_dir, args.baseline, args.session, providers[args.provider],
                                         providers[args.optimizer_provider or args.provider],
                                         target=args.target, runs=args.runs, rounds=args.rounds))
        else:
            db = db_open(args.state_dir, read_only=True)
            try:
                report = progress(db, args.session)
            finally:
                db.close()
            if args.scoreboard_yaml:
                print(yaml.safe_dump(report["scoreboard"], allow_unicode=True, sort_keys=False), end="")
                return 0
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except CellFailure as exc:
        code = exc.code
    except (ConfigError, OSError, ValueError, KeyError, sqlite3.Error, RuntimeError):
        code = "evaluation_failed"
    print(json.dumps({"status": "failed", "failure_code": code}), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
