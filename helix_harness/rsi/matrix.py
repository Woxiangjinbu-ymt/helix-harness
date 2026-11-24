"""把受信的 cell 回执与冻结矩阵、会话和封存轨迹逐项绑定。"""

from collections import defaultdict
from datetime import datetime, timezone
import json

from .contracts import EvalCell
from .evaluator import CellFailure, ScoreRequest, check_run
from .fixtures import file_digest


def requests(plan):
    for case in plan["cases"]:
        for index in range(1, plan["runs_per_case"] + 1):
            cell = EvalCell(plan["evaluation_id"], case, index, plan["version"], plan["run_fingerprint"])
            yield ScoreRequest(cell, plan["strategy_digest"], plan["judge_digest"],
                               plan["provider"], plan["model_id"], plan["thinking_mode"])


def metrics(rows):
    costs = [r["cost"] for r in rows if r["cost"] is not None]
    return {"score": round(sum(r["score"] for r in rows) / len(rows), 2),
            "cost": round(sum(costs) / len(costs), 6) if costs else None,
            "duration_ms": round(sum(r["duration_ms"] for r in rows) / len(rows))}


def stored_receipts(db, eid):
    rows = db.execute("SELECT case_id,run_index,session_id,cell_id,receipt FROM cells WHERE evaluation_id=?", (eid,))
    result = {}
    for case, index, session, cid, raw in rows:
        receipt = json.loads(raw)
        if (case, index, session, cid) != tuple(receipt[k] for k in ("case_id", "run", "session_id", "cell_id")):
            raise CellFailure("evaluation_failed")
        result[case, index] = receipt
    return result


def observe(state, req, receipt, lock, case):
    req.validate_receipt(receipt)
    run = state / "runs" / req.cell.cell_id
    outcome, _ = check_run(run, req, lock, case)
    if (receipt["repo_commit"] != case["repo_commit"] or
            any(receipt[k] != outcome[k] for k in ("session_id", "trace_digest", "artifact_digest", "duration_ms"))):
        raise CellFailure("evaluation_failed")
    # 只向分析器投影计数及可定位的事件序号。候选文本、私测和工具输出均不进入模型输入。
    trace = run / "events.jsonl"
    calls, writes, errors, checks = {}, [], [], []
    turns = 0
    for line in trace.read_text().splitlines():
        e = json.loads(line)
        if e["type"] == "tool_use":
            calls[e["tool_id"]] = e["tool"]
        elif e["type"] == "tool_result":
            tool = calls[e["tool_id"]]
            if e["is_error"]:
                errors.append(e["seq"])
            elif tool in {"WriteFile", "EditFile"}:
                writes.append(e["seq"])
            elif tool == "Bash":
                checks.append(e["seq"])
        elif e["type"] in {"turn_complete", "loop_complete"}:
            turns = e["turn"]
    if (type(turns) is not int or turns < 0 or turns != outcome["turns"]
            or file_digest(trace) != receipt["trace_digest"]):
        raise CellFailure("evaluation_failed")
    return {"case": case["case_id"], "session": receipt["session_id"], "run": req.cell.run_index,
            "score": receipt["score"], "terminal": outcome["status"], "turns": turns,
            "writes": len(writes), "public_checks": len(checks), "tool_errors": len(errors),
            "events": {"writes": writes, "public_checks": checks, "tool_errors": errors}}


def verified_matrix(db, state, plan, lock):
    records = stored_receipts(db, plan["evaluation_id"])
    pins = {c["case_id"]: c for c in lock["benchmark"]["cases"]}
    expected = list(requests(plan))
    if list(pins) != plan["cases"] or set(records) != {(r.cell.case_id, r.cell.run_index) for r in expected}:
        raise CellFailure("evaluation_failed")
    if len({r["session_id"] for r in records.values()}) != len(records):
        raise CellFailure("evaluation_failed")
    grouped = defaultdict(list)
    observations = []
    for req in expected:
        case = req.cell.case_id
        receipt = records[case, req.cell.run_index]
        observations.append(observe(state, req, receipt, lock, pins[case]))
        grouped[case].append(receipt)
    cases = [{"case": name, "runs": rows, **metrics(rows)} for name, rows in grouped.items()]
    return {**plan, "cases": cases, **metrics(cases)}, observations


def utc_now():
    return datetime.now(timezone.utc).isoformat()
