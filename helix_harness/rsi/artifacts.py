"""按评测、题目和运行序号导出完整任务仓库，或从原封存产物复评分。"""

import argparse
import asyncio
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
from zipfile import ZipFile, ZipInfo

from .baseline import db_open
from .contracts import digest
from .evaluator import CellFailure, Evaluator, verified_release
from .fixtures import file_digest, regular_files
from .matrix import observe, requests, stored_receipts


def locate(state, evaluation, case, index):
    lock, judge = verified_release()
    with closing(db_open(state, read_only=True)) as db:
        db.execute("BEGIN")
        row = db.execute("SELECT plan FROM campaigns WHERE evaluation_id=?", (evaluation,)).fetchone()
        receipt = stored_receipts(db, evaluation).get((case, index))
        if not row or receipt is None:
            raise CellFailure("invalid_request")
        plan = json.loads(row[0])
    if (plan["evaluation_id"] != evaluation or plan["judge_digest"] != judge
            or plan["benchmark_digest"] != lock["benchmark_digest"]):
        raise CellFailure("version_changed")
    req = next((r for r in requests(plan) if r.cell.case_id == case and r.cell.run_index == index), None)
    pin = next((p for p in lock["benchmark"]["cases"] if p["case_id"] == case), None)
    if req is None or pin is None:
        raise CellFailure("invalid_request")
    observe(state, req, receipt, lock, pin)
    return req, receipt


def export_archive(source: Path, archive: Path, expected: str):
    """先核验 ZIP 内实际字节，再独占发布；重试核对已发布的同一份文件。"""
    if archive.parent.is_symlink():
        raise CellFailure("evaluation_failed")
    archive.parent.mkdir(mode=0o700, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=archive.parent) as tmp:
        with ZipFile(tmp, "w") as bundle:
            for path in regular_files(source):
                name = path.relative_to(source).as_posix()
                with path.open("rb") as src, bundle.open(ZipInfo(name), "w", force_zip64=True) as dst:
                    shutil.copyfileobj(src, dst)
        with ZipFile(tmp) as bundle:
            files = bundle.namelist()
            contents = {}
            for name in files:
                with bundle.open(name) as stream:
                    contents[name] = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest(contents) != expected:
            raise CellFailure("version_changed")
        tmp.flush()
        os.fsync(tmp.fileno())
        archive_hash = file_digest(Path(tmp.name))
        try:
            os.link(tmp.name, archive)
        except FileExistsError:
            if file_digest(archive) != archive_hash:
                raise CellFailure("version_changed")
    return {"archive": str(archive), "archive_sha256": archive_hash, "artifact_digest": expected, "files": files}


async def run(args):
    state = args.state_dir.resolve()
    req, receipt = locate(state, args.evaluation, args.case, args.run)
    if args.action == "rescore":
        replayed = await Evaluator(state).score(req)
        if replayed != receipt:
            raise CellFailure("evaluation_failed")
        return replayed
    cid = req.cell.cell_id
    result = export_archive(state / "runs" / cid / "artifact", state / "exports" / (cid + ".zip"),
                            receipt["artifact_digest"])
    return {**result, "evaluation_id": req.cell.evaluation_id, "case_id": req.cell.case_id,
            "run": req.cell.run_index, "cell_id": cid, "session_id": receipt["session_id"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("export", "rescore"))
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--evaluation", required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--run", type=int, required=True)
    try:
        print(json.dumps(asyncio.run(run(parser.parse_args())), ensure_ascii=False, indent=2))
        return 0
    except CellFailure as exc:
        code = exc.code
    except (OSError, sqlite3.Error, ValueError, KeyError, RuntimeError):
        code = "evaluation_failed"
    print(json.dumps({"status": "failed", "failure_code": code}), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
