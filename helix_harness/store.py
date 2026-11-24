"""HTTP job metadata and append-only public event journal. Engine data stays authoritative."""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .paths import APP_DATA, PROJECT
from helix_harness.rsi.runtime import ROOT
from helix_harness.rsi.fixtures import BENCHMARK

ARCHIVE = Path(
    os.getenv("RSI_ARCHIVE", APP_DATA / "RSI")
).resolve()
HOME = Path(os.getenv("RSI_WEB_HOME", ARCHIVE.parent / "WebConsole")).resolve()
PUBLIC = ROOT / "evaluation" / BENCHMARK / "public"
DEFAULT = ROOT / "evaluation/strategies/default-v1"
HISTORY = {
    "day-2-acceptance": "Mock",
    "day-3-baseline": "Real",
    "day-4-scripted-acceptance": "Mock",
    "day-5-real": "Real",
}
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def db():
    conn = sqlite3.connect(HOME / "console.sqlite", timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init():
    if HOME.is_relative_to(PROJECT):
        raise ValueError("Web state must be outside the source tree")
    HOME.mkdir(parents=True, mode=0o700, exist_ok=True)
    with db() as con:
        con.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, kind TEXT, config TEXT, study TEXT,
          mode TEXT, status TEXT, created TEXT, finished TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT, kind TEXT,
          at TEXT, data TEXT, ref TEXT, UNIQUE(job,ref));
        CREATE INDEX IF NOT EXISTS event_cursor ON events(job,id);
        CREATE TABLE IF NOT EXISTS repos(id TEXT PRIMARY KEY, name TEXT, commit_id TEXT, fixture TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS registered_commit ON repos(name,commit_id);
        """)


def emit(job, kind, data=None, ref=None):
    with db() as con:
        con.execute(
            "INSERT OR IGNORE INTO events(job,kind,at,data,ref) VALUES(?,?,?,?,?)",
            (job, kind, now(), json.dumps(data or {}, ensure_ascii=False), ref),
        )


def finish(job, status, error=None):
    kind = {
        "failed": "run_failed",
        "cancelled": "run_cancelled",
        "interrupted": "run_interrupted",
        "completed": "run_completed",
    }[status]
    with db() as con:
        updated = con.execute(
            "UPDATE jobs SET status=?,finished=?,error=? WHERE id=? AND status IN ('queued','running','cancelling')",
            (status, now(), error, job),
        )
        if not updated.rowcount:
            return
        con.execute(
            "INSERT OR IGNORE INTO events(job,kind,at,data,ref) VALUES(?,?,?,?,?)",
            (
                job,
                kind,
                now(),
                json.dumps({"status": status, "reason": error}),
                "terminal",
            ),
        )


def jobs():
    with db() as con:
        return [
            {**dict(r), "config": json.loads(r["config"])}
            for r in con.execute("SELECT * FROM jobs ORDER BY created DESC")
        ]


def journal(job, after=0):
    with db() as con:
        return [
            dict(
                event_id=r["id"],
                run_id=job,
                event_type=r["kind"],
                timestamp=r["at"],
                data=json.loads(r["data"]),
            )
            for r in con.execute(
                "SELECT * FROM events WHERE job=? AND id>? ORDER BY id LIMIT 500",
                (job, after),
            )
        ]


def sources():
    result = {k: (ARCHIVE / k, v) for k, v in HISTORY.items() if (ARCHIVE / k).is_dir()}
    for j in jobs():
        result[j["study"]] = (HOME / "studies" / j["study"], j["mode"])
    return result


def rows(state, table):
    path = state / "control.sqlite"
    if not path.exists():
        return []
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone():
            return []
        return [dict(r) for r in conn.execute("SELECT * FROM " + table)]
    finally:
        conn.close()


def read(path, default=None):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default
