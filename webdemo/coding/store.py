"""Coding 专属 SQLite 状态。请求回执与业务结果共享事务。"""

import hashlib
import json
import os
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timezone
from pathlib import Path
from uuid import uuid4

from .models import Conflict
from .worktrees import APP_DATA, Worktree


def data_dir():
    return (
        Path(os.getenv("CODING_WEB_HOME", APP_DATA / "CodingWorkspace"))
        .expanduser()
        .resolve()
    )


def encode(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def now():
    return datetime.now(UTC).isoformat()


def uid():
    return str(uuid4())


SCHEMA = """
CREATE TABLE IF NOT EXISTS trees(id TEXT PRIMARY KEY, identity TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, tree TEXT NOT NULL REFERENCES trees(id),
 cwd TEXT NOT NULL, profile TEXT NOT NULL, title TEXT NOT NULL, created TEXT NOT NULL, active TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS snapshots(id TEXT PRIMARY KEY, document TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS turns(id TEXT PRIMARY KEY, session TEXT NOT NULL REFERENCES sessions(id),
 snapshot TEXT NOT NULL REFERENCES snapshots(id), state TEXT NOT NULL, accepted TEXT NOT NULL,
 started TEXT, ended TEXT, reason TEXT, worker TEXT, UNIQUE(session,id));
CREATE UNIQUE INDEX IF NOT EXISTS one_active ON turns(session)
 WHERE state IN ('accepted','running','waiting_permission','cancelling');
CREATE TABLE IF NOT EXISTS events(session TEXT NOT NULL, seq INTEGER NOT NULL, turn TEXT NOT NULL,
 source TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, recorded TEXT NOT NULL,
 PRIMARY KEY(session,seq), UNIQUE(turn,source), FOREIGN KEY(session,turn) REFERENCES turns(session,id));
CREATE TABLE IF NOT EXISTS receipts(scope TEXT NOT NULL, request TEXT NOT NULL, hash TEXT NOT NULL,
 result TEXT NOT NULL, PRIMARY KEY(scope,request));
CREATE TABLE IF NOT EXISTS leases(tree TEXT PRIMARY KEY REFERENCES trees(id), generation INTEGER NOT NULL DEFAULT 0,
 state TEXT NOT NULL DEFAULT 'free', turn TEXT, worker TEXT, acquired TEXT);
CREATE TABLE IF NOT EXISTS permissions(id TEXT PRIMARY KEY, turn TEXT NOT NULL REFERENCES turns(id),
 call TEXT NOT NULL, worker TEXT NOT NULL, state TEXT NOT NULL, summary TEXT NOT NULL, answer TEXT);
CREATE TABLE IF NOT EXISTS actions(id TEXT NOT NULL, turn TEXT NOT NULL REFERENCES turns(id), worker TEXT NOT NULL,
 call TEXT, generation INTEGER, target TEXT NOT NULL, state TEXT NOT NULL, PRIMARY KEY(turn,id));
"""


class Store:
    def __init__(self, root=None):
        self.root = Path(root or data_dir()).resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.root / "coding.sqlite"
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=8, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def create_session(self, directory, profile):
        tree, cwd = Worktree.identify(directory)
        if self.root.is_relative_to(tree.root):
            raise ValueError("Coding 数据目录必须位于工作树之外")
        identity = encode(asdict(tree))
        with self.connection(True) as db:
            db.execute("INSERT OR IGNORE INTO trees VALUES(?,?)", (uid(), identity))
            tree_id = db.execute(
                "SELECT id FROM trees WHERE identity=?", (identity,)
            ).fetchone()[0]
            db.execute("INSERT OR IGNORE INTO leases(tree) VALUES(?)", (tree_id,))
            sid, stamp = uid(), now()
            db.execute(
                "INSERT INTO sessions VALUES(?,?,?,?,?,?,?)",
                (sid, tree_id, cwd, profile, "", stamp, stamp),
            )
            return self.session(sid, db)

    def session(self, sid, db):
        row = db.execute(
            """SELECT s.id session_id, s.tree worktree_id, s.cwd cwd_relative, s.profile profile_id,
                            s.title, s.created created_at, s.active last_active_at, t.identity
                            FROM sessions s JOIN trees t ON s.tree=t.id WHERE s.id=?""",
            (sid,),
        ).fetchone()
        if row is None:
            raise KeyError(sid)
        r = dict(row)
        r["identity"] = json.loads(r["identity"])
        r["directory"] = str(Path(r["identity"]["root"]) / r["cwd_relative"])
        return r

    def existing(self, db, scope, request, payload):
        row = db.execute(
            "SELECT hash,result FROM receipts WHERE scope=? AND request=?",
            (scope, str(request)),
        ).fetchone()
        if row is None:
            return None
        if row["hash"] != digest(payload):
            raise Conflict("请求 ID 已用于其他内容")
        return json.loads(row["result"])

    def remember(self, db, scope, request, payload, result):
        db.execute(
            "INSERT INTO receipts VALUES(?,?,?,?)",
            (scope, str(request), digest(payload), encode(result)),
        )
        return result

    def event(self, db, sid, tid, source, kind, payload):
        prior = db.execute(
            "SELECT seq,kind,payload,session FROM events WHERE turn=? AND source=?",
            (tid, source),
        ).fetchone()
        value = encode(payload)
        if prior:
            if (prior["kind"], prior["payload"], prior["session"]) != (
                kind,
                value,
                sid,
            ):
                raise Conflict("事件来源与已存记录冲突")
            return prior["seq"]
        seq = db.execute(
            "SELECT coalesce(max(seq),0)+1 FROM events WHERE session=?", (sid,)
        ).fetchone()[0]
        db.execute(
            "INSERT INTO events VALUES(?,?,?,?,?,?,?)",
            (sid, seq, tid, source, kind, value, now()),
        )
        return seq

    def events(self, db, sid, after=0, limit=None):
        sql = "SELECT * FROM events WHERE session=? AND seq>? AND kind!='native_record' ORDER BY seq"
        params = [sid, after]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [
            dict(
                session_id=r["session"],
                turn_id=r["turn"],
                seq=r["seq"],
                source_id=r["source"],
                schema_version=1,
                kind=r["kind"],
                payload=json.loads(r["payload"]),
                recorded_at=r["recorded"],
            )
            for r in db.execute(sql, params)
        ]

    def submit(self, sid, request, text, resolve_snapshot):
        with self.connection(True) as db:
            session = self.session(sid, db)
            scope = sid + "/submit"
            existing = self.existing(db, scope, request, text)
            if existing:
                return existing, False
            Worktree(**session["identity"]).verify(session["cwd_relative"])
            active = db.execute(
                "SELECT id FROM turns WHERE session=? AND ended IS NULL", (sid,)
            ).fetchone()
            if active:
                raise Conflict("会话已有运行中的轮次", turn_id=active[0])
            document = resolve_snapshot(session["profile_id"])
            snapshot, tid, stamp = digest(document), uid(), now()
            db.execute(
                "INSERT OR IGNORE INTO snapshots VALUES(?,?)",
                (snapshot, encode(document)),
            )
            db.execute(
                "INSERT INTO turns(id,session,snapshot,state,accepted) VALUES(?,?,?,'accepted',?)",
                (tid, sid, snapshot, stamp),
            )
            self.event(db, sid, tid, "user", "user", {"text": text})
            self.event(
                db,
                sid,
                tid,
                "accepted",
                "state",
                {
                    "state": "accepted",
                    "model": document.get("model"),
                    "provider": document.get("provider"),
                },
            )
            db.execute(
                "UPDATE sessions SET active=?, title=CASE WHEN title='' THEN ? ELSE title END WHERE id=?",
                (stamp, text[:72], sid),
            )
            result = {
                "session_id": sid,
                "turn_id": tid,
                "snapshot_id": snapshot,
                "state": "accepted",
            }
            return self.remember(db, scope, request, text, result), True
