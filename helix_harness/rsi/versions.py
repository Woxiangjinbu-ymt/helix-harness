"""按内容封存策略。SQLite 原子提交候选决策、正式榜和 Reference。"""

from dataclasses import asdict
import json
import os
from pathlib import Path
import sqlite3
import tempfile

import yaml
from mtcode.skills.parser import SkillParseError
from .baseline import db_open, safe_id
from .contracts import FrozenContext, digest, require_digest
from .default_optimizer import InvalidProposal, Proposal
from .evaluator import CellFailure, verified_release
from .failure_analysis import FailureAnalyzer
from .fixtures import copy_files, regular_files, tree_digest
from .matrix import utc_now, verified_matrix
from .runtime import ROOT
from .strategy import assemble_strategy


def sync_dir(folder):
    fd = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Snapshots:
    def __init__(self, state):
        self.objects = state / "objects"
        if self.objects.is_symlink():
            raise CellFailure("version_changed")
        self.objects.mkdir(mode=0o700, exist_ok=True)

    def get(self, key):
        require_digest(key)
        folder = self.objects / key
        try:
            actual = tree_digest(folder)
        except (ValueError, OSError) as exc:
            raise CellFailure("version_changed") from exc
        if actual != key:
            raise CellFailure("version_changed")
        return folder

    def seal(self, source, parent_digest, edits=()):
        if tree_digest(source) != parent_digest:
            raise CellFailure("version_changed")
        with tempfile.TemporaryDirectory(dir=self.objects) as tmp:
            folder = Path(tmp) / "strategy"
            folder.mkdir()
            copy_files(source, folder)
            if tree_digest(folder) != parent_digest:
                raise CellFailure("version_changed")
            for edit in edits:
                target = folder / edit.path
                if edit.content is None:
                    if not target.is_file():
                        raise InvalidProposal("无法删除不存在的 Skill")
                    target.unlink()
                else:
                    target.parent.mkdir(exist_ok=True, parents=True)
                    target.write_text(edit.content, encoding="utf-8")
            key = tree_digest(folder)
            if edits and key == parent_digest:
                raise InvalidProposal("提案未改变策略内容")
            try:
                if sum(p.stat().st_size for p in regular_files(folder)) > 128 * 1024:
                    raise InvalidProposal("完整快照超过大小预算")
                assembled = assemble_strategy(folder, key)
                if any(not isinstance(s["description"], str) or not s["description"].strip()
                       for s in assembled.skills.values()):
                    raise InvalidProposal("Skill 需要文本描述")
            except (ValueError, TypeError, KeyError, AttributeError, OSError, RecursionError,
                    SkillParseError, yaml.YAMLError) as exc:
                raise InvalidProposal("策略未通过 Runtime 装配校验") from exc
            if not (self.objects / key).exists():
                for file in regular_files(folder):
                    with file.open("rb") as stream:
                        os.fsync(stream.fileno())
                for directory, _, _ in os.walk(folder, topdown=False):
                    sync_dir(directory)
                try:
                    os.rename(folder, self.objects / key)
                except OSError:
                    if not (self.objects / key).is_dir():
                        raise
            sync_dir(self.objects)
            self.get(key)
            return key


class VersionManager:
    def __init__(self, state: Path, plan, lock):
        self.state = state.resolve()
        self.base = plan["evaluation_id"]
        self.plan = plan
        self.lock = lock
        if not (self.state / "control.sqlite").is_file():
            raise CellFailure("baseline_required")
        self.db = db_open(self.state)
        self.db.row_factory = sqlite3.Row
        try:
            formal = self.db.execute("SELECT document FROM evaluations WHERE evaluation_id=?", (self.base,)).fetchone()
            if not formal:
                raise CellFailure("baseline_required")
            pinned = self.db.execute("SELECT plan FROM campaigns WHERE evaluation_id=?", (self.base,)).fetchone()
            if not pinned or json.loads(pinned[0]) != plan:
                raise CellFailure("version_changed")
            document = json.loads(formal[0])
            expected, _ = verified_matrix(self.db, self.state, plan, lock)
            # 沿用第 3 天正式基线的费用约定。
            if any(c["cost"] is None for c in expected["cases"]):
                expected["cost"] = None
            if document != dict(expected, kind="formal_baseline", time=document["time"]):
                raise CellFailure("version_changed")
            self.objects = Snapshots(self.state)
            self.objects.seal(ROOT / "evaluation/strategies/default-v1", plan["strategy_digest"])
            self._init_tables(document)
        except BaseException:
            self.db.close()
            raise

    def _init_tables(self, baseline):
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS rsi_board (
                base TEXT NOT NULL, version INTEGER NOT NULL, document TEXT NOT NULL,
                PRIMARY KEY(base, version));
            CREATE TABLE IF NOT EXISTS rsi_head (
                base TEXT PRIMARY KEY, version INTEGER NOT NULL,
                FOREIGN KEY(base,version) REFERENCES rsi_board(base,version));
            CREATE TABLE IF NOT EXISTS rsi_sessions (
                id TEXT PRIMARY KEY, base TEXT NOT NULL REFERENCES rsi_head(base), config TEXT NOT NULL,
                expected INTEGER NOT NULL, reason TEXT);
            CREATE TABLE IF NOT EXISTS rsi_attempts (
                base TEXT NOT NULL, version INTEGER NOT NULL, session TEXT NOT NULL REFERENCES rsi_sessions(id),
                parent INTEGER NOT NULL, plan TEXT NOT NULL, proposal TEXT NOT NULL, decision TEXT NOT NULL,
                report TEXT, PRIMARY KEY(base, version));
            CREATE UNIQUE INDEX IF NOT EXISTS rsi_pending ON rsi_attempts(session) WHERE decision='pending';
        """)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute("INSERT OR IGNORE INTO rsi_board VALUES (?,1,?)", (self.base, json.dumps(baseline)))
            self.db.execute("INSERT OR IGNORE INTO rsi_head VALUES (?,1)", (self.base,))
            if self.board(1) != baseline:
                raise CellFailure("version_changed")

    def board(self, version=None):
        if version is None:
            version = self.db.execute("SELECT version FROM rsi_head WHERE base=?", (self.base,)).fetchone()[0]
        row = self.db.execute("SELECT document FROM rsi_board WHERE base=? AND version=?", (self.base, version)).fetchone()
        if row is None:
            raise CellFailure("version_changed")
        return json.loads(row[0])

    def open_session(self, sid, config):
        safe_id(sid)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            head = self.board()
            self.db.execute("INSERT OR IGNORE INTO rsi_sessions VALUES (?,?,?,?,NULL)",
                            (sid, self.base, json.dumps(config), head["version"]))
            row = self.session(sid)
            if row["base"] != self.base or json.loads(row["config"]) != config:
                raise CellFailure("version_changed")

    def session(self, sid):
        row = self.db.execute("SELECT * FROM rsi_sessions WHERE id=? AND base=?", (sid, self.base)).fetchone()
        if not row:
            raise CellFailure("invalid_request")
        return row

    def current(self, sid):
        ref = self.board()
        if self.session(sid)["expected"] != ref["version"]:
            raise CellFailure("reference_changed")
        self.objects.get(ref["strategy_digest"])
        return ref

    def attempts(self, sid):
        return list(self.db.execute("SELECT * FROM rsi_attempts WHERE session=? AND base=? ORDER BY version",
                                    (sid, self.base)))

    def plan_for(self, document):
        return {k: self.plan["cases"] if k == "cases" else document[k] for k in self.plan}

    def evidence(self, document):
        _, rows = verified_matrix(self.db, self.state, self.plan_for(document), self.lock)
        return rows

    def stage(self, sid, reference, data, round_index):
        observations = self.evidence(reference)
        proposal = Proposal.parse(data, reference["version"], {r["session"] for r in observations}, self.plan["cases"])
        parent_key = reference["strategy_digest"]
        key = self.objects.seal(self.objects.get(parent_key), parent_key, proposal.edits)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            session = self.session(sid)
            history = self.attempts(sid)
            if (session["reason"] or self.current(sid) != reference or len(history) != round_index - 1
                    or round_index > json.loads(session["config"])["rounds"]):
                raise CellFailure("reference_changed")
            v = self.db.execute("SELECT coalesce(max(version),1)+1 FROM rsi_attempts WHERE base=?",
                                (self.base,)).fetchone()[0]
            eid = "candidate-" + digest([self.base, sid, v])
            plan = dict(self.plan, version=v, strategy_digest=key, evaluation_id=eid,
                        runs_per_case=json.loads(session["config"])["runs"],
                        run_fingerprint=FrozenContext(**self.plan["context"]).run_fingerprint(key))
            self.db.execute("INSERT INTO campaigns VALUES (?,?)", (eid, json.dumps(plan)))
            self.db.execute("INSERT INTO rsi_attempts VALUES (?,?,?,?,?,?,'pending',NULL)",
                            (self.base, v, sid, reference["version"], json.dumps(plan), json.dumps(asdict(proposal))))
            row = self.db.execute("SELECT * FROM rsi_attempts WHERE base=? AND version=?", (self.base, v)).fetchone()
        return row

    def finish(self, sid, version):
        if verified_release() != (self.lock, self.plan["judge_digest"]):
            raise CellFailure("version_changed")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT * FROM rsi_attempts WHERE base=? AND version=? AND session=?",
                                  (self.base, version, sid)).fetchone()
            if not row:
                raise CellFailure("invalid_request")
            if row["decision"] != "pending":
                return json.loads(row["report"])
            reference = self.current(sid)
            if reference["version"] != row["parent"]:
                raise CellFailure("reference_changed")
            plan = json.loads(row["plan"])
            pinned = self.db.execute("SELECT plan FROM campaigns WHERE evaluation_id=?",
                                     (plan["evaluation_id"],)).fetchone()
            if not pinned or json.loads(pinned[0]) != plan:
                raise CellFailure("version_changed")
            self.objects.get(plan["strategy_digest"])
            matrix, after = verified_matrix(self.db, self.state, plan, self.lock)
            before = self.evidence(reference)
            proposal = Proposal.parse(json.loads(row["proposal"]), row["parent"],
                                      {r["session"] for r in before}, self.plan["cases"])
            prediction = FailureAnalyzer().prediction(proposal, before, after)
            decision = "accepted" if matrix["score"] > reference["score"] else "rejected"
            report = dict(matrix, kind="candidate", time=utc_now(), parent=row["parent"], decision=decision,
                          hypothesis=proposal.hypothesis, evidence=proposal.evidence, prediction=prediction,
                          changed_paths=[e.path for e in proposal.edits])
            raw = json.dumps(report)
            if decision == "accepted":
                self.db.execute("INSERT INTO rsi_board VALUES (?,?,?)", (self.base, version, raw))
                n = self.db.execute("UPDATE rsi_head SET version=? WHERE base=? AND version=?",
                                    (version, self.base, row["parent"])).rowcount
                if n != 1:
                    raise CellFailure("reference_changed")
                self.db.execute("UPDATE rsi_sessions SET expected=? WHERE id=?", (version, sid))
            self.db.execute("UPDATE rsi_attempts SET decision=?, report=? WHERE base=? AND version=?",
                            (decision, raw, self.base, version))
            expected = json.loads(raw) if decision == "accepted" else reference
            if self.board() != expected:
                raise CellFailure("version_changed")
        return json.loads(raw)

    def stop(self, sid, reason, round_count):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.current(sid)
            if len(self.attempts(sid)) != round_count:
                raise CellFailure("reference_changed")
            self.db.execute("UPDATE rsi_sessions SET reason=? WHERE id=? AND reason IS NULL", (reason, sid))
