"""仓库级 RSI 状态存储：内容寻址快照、SQLite 原子版本线、EIT 台账与回归用例库。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from typing import Any
from uuid import uuid4

from ..contracts import digest, require_digest
from ..default_optimizer import Edit, InvalidProposal
from ..fixtures import copy_files, regular_files, tree_digest
from .harness import (
    MAX_TOTAL_SNAPSHOT_BYTES,
    RepoHarnessInputs,
    init_default_harness,
    validate_and_assemble_harness,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sync_dir(folder: Path) -> None:
    fd = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def repo_id_for_root(repo_root: str | Path) -> str:
    resolved = str(Path(repo_root).expanduser().resolve())
    slug = re.sub(r"[^a-z0-9]+", "-", Path(resolved).name.lower()).strip("-")[:24] or "repo"
    return f"{slug}-{digest(resolved)[:12]}"


class RepoSnapshots:
    """仓库级 Harness 内容寻址快照库（支持 playbook.yaml 与 tool_guidance.yaml）。"""

    def __init__(self, state_dir: Path) -> None:
        self.objects = state_dir / "objects"
        if self.objects.is_symlink():
            raise ValueError("快照目录不能为符号链接")
        self.objects.mkdir(mode=0o700, parents=True, exist_ok=True)

    def get(self, key: str) -> Path:
        require_digest(key)
        folder = self.objects / key
        if not folder.is_dir() or tree_digest(folder) != key:
            raise ValueError(f"快照缺失或摘要不匹配：{key}")
        return folder

    def load(self, key: str) -> RepoHarnessInputs:
        folder = self.get(key)
        return validate_and_assemble_harness(folder, expected=key)

    def seal_folder(self, source: Path) -> str:
        """封存任意合法 Harness 目录到内容寻址库。"""
        assembled = validate_and_assemble_harness(source)
        key = assembled.digest
        if (self.objects / key).is_dir():
            self.get(key)
            return key
        with tempfile.TemporaryDirectory(dir=self.objects) as tmp:
            folder = Path(tmp) / "harness"
            folder.mkdir()
            copy_files(source, folder)
            validate_and_assemble_harness(folder, expected=key)
            for file_path in regular_files(folder):
                with file_path.open("rb") as stream:
                    os.fsync(stream.fileno())
            for directory, _, _ in os.walk(folder, topdown=False):
                _sync_dir(Path(directory))
            try:
                os.rename(folder, self.objects / key)
            except OSError:
                if not (self.objects / key).is_dir():
                    raise
        _sync_dir(self.objects)
        self.get(key)
        return key

    def seal_edits(
        self, parent_digest: str, edits: tuple[Edit, ...] | list[Edit]
    ) -> str:
        """基于父快照原子应用编辑、校验装配并封存新快照。"""
        source = self.get(parent_digest)
        with tempfile.TemporaryDirectory(dir=self.objects) as tmp:
            folder = Path(tmp) / "harness"
            folder.mkdir()
            copy_files(source, folder)
            for edit in edits:
                target = folder / edit.path
                if edit.content is None:
                    if not target.is_file():
                        raise InvalidProposal(f"无法删除不存在的文件：{edit.path}")
                    target.unlink()
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(edit.content, encoding="utf-8")
            key = tree_digest(folder)
            if edits and key == parent_digest:
                raise InvalidProposal("提案未产生实质性策略变更")
            total = sum(p.stat().st_size for p in regular_files(folder))
            if total > MAX_TOTAL_SNAPSHOT_BYTES:
                raise InvalidProposal("完整快照超过大小上限")
            try:
                validate_and_assemble_harness(folder, expected=key)
            except Exception as exc:
                raise InvalidProposal(f"策略未通过装配校验：{exc}") from exc
            if not (self.objects / key).is_dir():
                for file_path in regular_files(folder):
                    with file_path.open("rb") as stream:
                        os.fsync(stream.fileno())
                for directory, _, _ in os.walk(folder, topdown=False):
                    _sync_dir(Path(directory))
                try:
                    os.rename(folder, self.objects / key)
                except OSError:
                    if not (self.objects / key).is_dir():
                        raise
        _sync_dir(self.objects)
        self.get(key)
        return key


SCHEMA = """
CREATE TABLE IF NOT EXISTS repo_meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    repo_id TEXT NOT NULL,
    repo_root TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    config_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS harness_versions (
    version INTEGER PRIMARY KEY,
    digest TEXT NOT NULL,
    parent INTEGER,
    status TEXT NOT NULL,
    mean_score REAL,
    per_case_json TEXT NOT NULL,
    proposal_json TEXT,
    critic_json TEXT,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS harness_head (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL REFERENCES harness_versions(version),
    revision INTEGER NOT NULL,
    digest TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS head_history (
    revision INTEGER PRIMARY KEY,
    from_version INTEGER,
    to_version INTEGER NOT NULL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS eit_issues (
    issue_id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL,
    occurrences INTEGER NOT NULL DEFAULT 1,
    linked_cases_json TEXT NOT NULL DEFAULT '[]',
    attempted_fixes_json TEXT NOT NULL DEFAULT '[]',
    last_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS friction_events (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    summary TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    base_commit TEXT NOT NULL DEFAULT '',
    issue_id TEXT REFERENCES eit_issues(issue_id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS eval_cases (
    case_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    base_commit TEXT NOT NULL,
    base_patch TEXT NOT NULL DEFAULT '',
    prompt TEXT NOT NULL,
    checks_json TEXT NOT NULL,
    rubric_json TEXT NOT NULL,
    weights_json TEXT NOT NULL,
    baseline_score REAL,
    origin_friction_id TEXT,
    case_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evolution_runs (
    run_id TEXT PRIMARY KEY,
    optimizer_name TEXT NOT NULL,
    config_json TEXT NOT NULL,
    eit_variance REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL,
    stop_reason TEXT,
    result_json TEXT,
    created_at TEXT NOT NULL,
    ended_at TEXT
);

CREATE TABLE IF NOT EXISTS eval_receipts (
    run_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    case_id TEXT NOT NULL,
    run_index INTEGER NOT NULL,
    score REAL NOT NULL,
    checks_score REAL NOT NULL,
    trajectory_score REAL NOT NULL,
    rubric_score REAL,
    details_json TEXT NOT NULL,
    trace_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, version, case_id, run_index)
);
"""


@dataclass(frozen=True)
class HeadRef:
    repo_id: str
    version: int
    revision: int
    digest: str
    updated_at: str


class RepoRSIStore:
    """单个仓库的 RSI 持久化存储与原子版本控制器。"""

    def __init__(self, state_dir: Path, repo_root: Path | str | None = None, name: str = "") -> None:
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db_path = self.state_dir / "repo_rsi.sqlite"
        self.snapshots = RepoSnapshots(self.state_dir)
        self.runs_dir = self.state_dir / "runs"
        self.runs_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._init_db()
        if repo_root is not None:
            self.ensure_initialized(Path(repo_root).expanduser().resolve(), name=name)

    def _init_db(self) -> None:
        conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=10)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(SCHEMA)
        finally:
            conn.close()

    @contextmanager
    def connection(self, write: bool = False):
        conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def ensure_initialized(
        self,
        repo_root: Path,
        name: str = "",
        initial_harness_dir: Path | None = None,
    ) -> HeadRef:
        """幂等初始化仓库元数据与 v1 初始 Harness。"""
        with self.connection() as db:
            row = db.execute("SELECT * FROM repo_meta WHERE id=1").fetchone()
            if row is not None:
                return self.head()

        repo_id = repo_id_for_root(repo_root)
        repo_name = name.strip() or repo_root.name
        if initial_harness_dir is not None:
            v1_digest = self.snapshots.seal_folder(initial_harness_dir)
        else:
            with tempfile.TemporaryDirectory() as tmp:
                v1_folder = Path(tmp) / "v1"
                init_default_harness(v1_folder, repo_summary=repo_name)
                v1_digest = self.snapshots.seal_folder(v1_folder)

        now = utc_now()
        with self.connection(write=True) as db:
            if db.execute("SELECT 1 FROM repo_meta WHERE id=1").fetchone() is None:
                db.execute(
                    "INSERT INTO repo_meta VALUES (1,?,?,?,?,?)",
                    (repo_id, str(repo_root), repo_name, now, json.dumps({})),
                )
                db.execute(
                    "INSERT INTO harness_versions VALUES (1,?,NULL,'baseline',NULL,'{}',NULL,NULL,'初始仓库 Harness v1',?)",
                    (v1_digest, now),
                )
                db.execute(
                    "INSERT INTO harness_head VALUES (1,1,1,?,?)",
                    (v1_digest, now),
                )
                db.execute(
                    "INSERT INTO head_history VALUES (1,NULL,1,'init','初始化仓库 Harness v1',?)",
                    (now,),
                )
        return self.head()

    def meta(self) -> dict[str, Any]:
        with self.connection() as db:
            row = db.execute("SELECT * FROM repo_meta WHERE id=1").fetchone()
            if not row:
                raise ValueError("仓库 RSI 存储尚未初始化")
            return {
                "repo_id": row["repo_id"],
                "repo_root": row["repo_root"],
                "name": row["name"],
                "created_at": row["created_at"],
                "config": json.loads(row["config_json"]),
            }

    def head(self) -> HeadRef:
        with self.connection() as db:
            meta = db.execute("SELECT repo_id FROM repo_meta WHERE id=1").fetchone()
            row = db.execute("SELECT * FROM harness_head WHERE id=1").fetchone()
            if not meta or not row:
                raise ValueError("仓库 RSI 存储尚未初始化")
            return HeadRef(
                repo_id=meta["repo_id"],
                version=row["version"],
                revision=row["revision"],
                digest=row["digest"],
                updated_at=row["updated_at"],
            )

    def get_version(self, version: int) -> dict[str, Any]:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM harness_versions WHERE version=?", (version,)
            ).fetchone()
            if not row:
                raise KeyError(f"Harness 版本不存在：v{version}")
            return self._row_to_version(row)

    def list_versions(self) -> list[dict[str, Any]]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM harness_versions ORDER BY version ASC"
            ).fetchall()
            return [self._row_to_version(r) for r in rows]

    @staticmethod
    def _row_to_version(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "version": row["version"],
            "digest": row["digest"],
            "parent": row["parent"],
            "status": row["status"],
            "mean_score": row["mean_score"],
            "per_case": json.loads(row["per_case_json"]),
            "proposal": json.loads(row["proposal_json"]) if row["proposal_json"] else None,
            "critic": json.loads(row["critic_json"]) if row["critic_json"] else None,
            "summary": row["summary"],
            "created_at": row["created_at"],
        }

    def record_baseline_score(
        self, version: int, mean_score: float, per_case: dict[str, float]
    ) -> dict[str, Any]:
        with self.connection(write=True) as db:
            db.execute(
                "UPDATE harness_versions SET mean_score=?, per_case_json=? WHERE version=?",
                (round(mean_score, 2), json.dumps(per_case, ensure_ascii=False), version),
            )
            row = db.execute(
                "SELECT * FROM harness_versions WHERE version=?", (version,)
            ).fetchone()
            return self._row_to_version(row)

    def record_candidate(
        self,
        *,
        parent: int,
        expected_revision: int,
        candidate_digest: str,
        status: str,
        mean_score: float | None,
        per_case: dict[str, float],
        proposal: dict[str, Any] | None,
        critic: dict[str, Any] | None,
        summary: str,
        promote: bool,
    ) -> tuple[dict[str, Any], HeadRef]:
        """原子记录候选版本，并在 promote=True 时做乐观并发 CAS 晋升。"""
        self.snapshots.get(candidate_digest)
        now = utc_now()
        with self.connection(write=True) as db:
            head_row = db.execute("SELECT * FROM harness_head WHERE id=1").fetchone()
            if not head_row:
                raise ValueError("未初始化 harness_head")
            if promote and (
                head_row["version"] != parent or head_row["revision"] != expected_revision
            ):
                raise RuntimeError("reference_changed: 活动 Harness 版本已发生并发变更")
            next_v = db.execute(
                "SELECT coalesce(max(version), 0) + 1 FROM harness_versions"
            ).fetchone()[0]
            db.execute(
                "INSERT INTO harness_versions VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    next_v,
                    candidate_digest,
                    parent,
                    status,
                    round(mean_score, 2) if mean_score is not None else None,
                    json.dumps(per_case, ensure_ascii=False),
                    json.dumps(proposal, ensure_ascii=False) if proposal is not None else None,
                    json.dumps(critic, ensure_ascii=False) if critic is not None else None,
                    summary,
                    now,
                ),
            )
            if promote:
                new_rev = head_row["revision"] + 1
                db.execute(
                    "UPDATE harness_head SET version=?, revision=?, digest=?, updated_at=? WHERE id=1",
                    (next_v, new_rev, candidate_digest, now),
                )
                db.execute(
                    "INSERT INTO head_history VALUES (?,?,?,?,?,?)",
                    (new_rev, parent, next_v, "promote", summary, now),
                )
            v_row = db.execute(
                "SELECT * FROM harness_versions WHERE version=?", (next_v,)
            ).fetchone()
        return self._row_to_version(v_row), self.head()

    def rollback(
        self,
        target_version: int,
        expected_revision: int | None = None,
        reason: str = "主动回滚",
    ) -> HeadRef:
        """将活动指针安全回滚至任意历史已验证版本（baseline 或 accepted）。"""
        now = utc_now()
        with self.connection(write=True) as db:
            head_row = db.execute("SELECT * FROM harness_head WHERE id=1").fetchone()
            if not head_row:
                raise ValueError("未初始化 harness_head")
            if expected_revision is not None and head_row["revision"] != expected_revision:
                raise RuntimeError("reference_changed: 活动修订号不匹配")
            target = db.execute(
                "SELECT * FROM harness_versions WHERE version=?", (target_version,)
            ).fetchone()
            if not target:
                raise KeyError(f"目标版本不存在：v{target_version}")
            if target["status"] not in ("baseline", "accepted"):
                raise ValueError(f"目标版本 v{target_version} 未通过验证（status={target['status']}），禁止回滚")
            self.snapshots.get(target["digest"])
            new_rev = head_row["revision"] + 1
            db.execute(
                "UPDATE harness_head SET version=?, revision=?, digest=?, updated_at=? WHERE id=1",
                (target_version, new_rev, target["digest"], now),
            )
            db.execute(
                "INSERT INTO head_history VALUES (?,?,?,?,?,?)",
                (new_rev, head_row["version"], target_version, "rollback", reason, now),
            )
        return self.head()

    # --- EIT 问题台账与 Friction 管理 ---

    def upsert_eit_issue(
        self,
        *,
        category: str,
        title: str,
        description: str,
        issue_id: str | None = None,
        linked_case: str | None = None,
    ) -> dict[str, Any]:
        """新增或归并仓库级 EIT 问题；若已解决的问题再次出现，自动重开（reopened）。"""
        now = utc_now()
        with self.connection(write=True) as db:
            existing = None
            if issue_id:
                existing = db.execute(
                    "SELECT * FROM eit_issues WHERE issue_id=?", (issue_id,)
                ).fetchone()
            if existing is None:
                # 简单词项重叠归并同类未关闭/已解决条目
                candidates = db.execute(
                    "SELECT * FROM eit_issues WHERE category=?", (category,)
                ).fetchall()
                title_tokens = set(re.findall(r"[\w\u4e00-\u9fff]{2,}", title.lower()))
                for cand in candidates:
                    cand_tokens = set(
                        re.findall(r"[\w\u4e00-\u9fff]{2,}", cand["title"].lower())
                    )
                    if title_tokens and cand_tokens:
                        overlap = len(title_tokens & cand_tokens) / max(
                            1, min(len(title_tokens), len(cand_tokens))
                        )
                        if overlap >= 0.6:
                            existing = cand
                            break
            if existing is not None:
                cases = json.loads(existing["linked_cases_json"])
                if linked_case and linked_case not in cases:
                    cases.append(linked_case)
                new_status = (
                    "reopened" if existing["status"] == "resolved" else existing["status"]
                )
                db.execute(
                    """UPDATE eit_issues
                       SET description=?, status=?, occurrences=occurrences+1,
                           linked_cases_json=?, last_seen_at=?, updated_at=?
                       WHERE issue_id=?""",
                    (
                        description or existing["description"],
                        new_status,
                        json.dumps(cases, ensure_ascii=False),
                        now,
                        now,
                        existing["issue_id"],
                    ),
                )
                row = db.execute(
                    "SELECT * FROM eit_issues WHERE issue_id=?", (existing["issue_id"],)
                ).fetchone()
            else:
                count = db.execute("SELECT count(*) FROM eit_issues").fetchone()[0] + 1
                new_id = issue_id or f"eit-{count:03d}"
                cases = [linked_case] if linked_case else []
                db.execute(
                    "INSERT INTO eit_issues VALUES (?,?,?,?,?,1,?,'[]',?,?)",
                    (
                        new_id,
                        category,
                        title.strip(),
                        description.strip(),
                        "open",
                        json.dumps(cases, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
                row = db.execute(
                    "SELECT * FROM eit_issues WHERE issue_id=?", (new_id,)
                ).fetchone()
        return self._row_to_issue(row)

    def record_eit_attempt(
        self,
        issue_id: str,
        *,
        version: int,
        hypothesis: str,
        outcome: str,
        score_delta: float,
        new_status: str | None = None,
    ) -> None:
        now = utc_now()
        with self.connection(write=True) as db:
            row = db.execute(
                "SELECT * FROM eit_issues WHERE issue_id=?", (issue_id,)
            ).fetchone()
            if not row:
                return
            attempts = json.loads(row["attempted_fixes_json"])
            attempts.append(
                {
                    "version": version,
                    "hypothesis": hypothesis[:300],
                    "outcome": outcome,
                    "score_delta": round(score_delta, 2),
                    "at": now,
                }
            )
            status = new_status or row["status"]
            db.execute(
                "UPDATE eit_issues SET attempted_fixes_json=?, status=?, updated_at=? WHERE issue_id=?",
                (json.dumps(attempts[-12:], ensure_ascii=False), status, now, issue_id),
            )

    def list_eit_issues(self) -> list[dict[str, Any]]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM eit_issues ORDER BY occurrences DESC, updated_at DESC"
            ).fetchall()
            return [self._row_to_issue(r) for r in rows]

    @staticmethod
    def _row_to_issue(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "issue_id": row["issue_id"],
            "category": row["category"],
            "title": row["title"],
            "description": row["description"],
            "status": row["status"],
            "occurrences": row["occurrences"],
            "linked_cases": json.loads(row["linked_cases_json"]),
            "attempted_fixes": json.loads(row["attempted_fixes_json"]),
            "last_seen_at": row["last_seen_at"],
            "updated_at": row["updated_at"],
        }

    def add_friction(
        self,
        *,
        session_id: str,
        turn_id: str,
        kind: str,
        category: str,
        severity: str,
        summary: str,
        evidence: dict[str, Any],
        base_commit: str = "",
        issue_id: str | None = None,
        friction_id: str | None = None,
    ) -> dict[str, Any]:
        fid = friction_id or f"fric-{uuid4().hex[:12]}"
        now = utc_now()
        with self.connection(write=True) as db:
            db.execute(
                "INSERT OR REPLACE INTO friction_events VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    fid,
                    session_id,
                    turn_id,
                    kind,
                    category,
                    severity,
                    summary,
                    json.dumps(evidence, ensure_ascii=False),
                    base_commit,
                    issue_id,
                    now,
                ),
            )
            row = db.execute(
                "SELECT * FROM friction_events WHERE id=?", (fid,)
            ).fetchone()
        return self._row_to_friction(row)

    def list_frictions(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM friction_events ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._row_to_friction(r) for r in rows]

    def get_friction(self, friction_id: str) -> dict[str, Any]:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM friction_events WHERE id=?", (friction_id,)
            ).fetchone()
            if not row:
                raise KeyError(f"Friction 事件不存在：{friction_id}")
            return self._row_to_friction(row)

    @staticmethod
    def _row_to_friction(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "turn_id": row["turn_id"],
            "kind": row["kind"],
            "category": row["category"],
            "severity": row["severity"],
            "summary": row["summary"],
            "evidence": json.loads(row["evidence_json"]),
            "base_commit": row["base_commit"],
            "issue_id": row["issue_id"],
            "created_at": row["created_at"],
        }

    # --- 仓库回归用例（Repo-Eval Case）管理 ---

    def save_case(
        self,
        *,
        case_id: str,
        title: str,
        base_commit: str,
        prompt: str,
        checks: list[dict[str, Any]],
        rubric: list[dict[str, Any]] | None = None,
        weights: dict[str, float] | None = None,
        base_patch: str = "",
        status: str = "draft",
        baseline_score: float | None = None,
        origin_friction_id: str | None = None,
    ) -> dict[str, Any]:
        if not re.fullmatch(r"[a-z][a-z0-9-]{1,63}", case_id):
            raise ValueError(f"case_id 格式无效：{case_id!r}")
        rubric_list = rubric or []
        default_weights = (
            {"checks": 0.6, "trajectory": 0.2, "rubric": 0.2}
            if rubric_list
            else {"checks": 0.75, "trajectory": 0.25, "rubric": 0.0}
        )
        final_weights = weights or default_weights
        case_payload = {
            "case_id": case_id,
            "base_commit": base_commit,
            "base_patch": base_patch,
            "prompt": prompt,
            "checks": checks,
            "rubric": rubric_list,
            "weights": final_weights,
        }
        cdigest = digest(case_payload)
        now = utc_now()
        with self.connection(write=True) as db:
            db.execute(
                """INSERT INTO eval_cases VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(case_id) DO UPDATE SET
                     title=excluded.title, status=excluded.status,
                     base_commit=excluded.base_commit, base_patch=excluded.base_patch,
                     prompt=excluded.prompt, checks_json=excluded.checks_json,
                     rubric_json=excluded.rubric_json, weights_json=excluded.weights_json,
                     baseline_score=coalesce(excluded.baseline_score, eval_cases.baseline_score),
                     origin_friction_id=coalesce(excluded.origin_friction_id, eval_cases.origin_friction_id),
                     case_digest=excluded.case_digest, updated_at=excluded.updated_at""",
                (
                    case_id,
                    title.strip(),
                    status,
                    base_commit,
                    base_patch,
                    prompt.strip(),
                    json.dumps(checks, ensure_ascii=False),
                    json.dumps(rubric_list, ensure_ascii=False),
                    json.dumps(final_weights, ensure_ascii=False),
                    baseline_score,
                    origin_friction_id,
                    cdigest,
                    now,
                    now,
                ),
            )
            row = db.execute(
                "SELECT * FROM eval_cases WHERE case_id=?", (case_id,)
            ).fetchone()
        return self._row_to_case(row)

    def update_case_status(
        self, case_id: str, status: str, baseline_score: float | None = None
    ) -> dict[str, Any]:
        now = utc_now()
        with self.connection(write=True) as db:
            row = db.execute(
                "SELECT * FROM eval_cases WHERE case_id=?", (case_id,)
            ).fetchone()
            if not row:
                raise KeyError(f"评测用例不存在：{case_id}")
            db.execute(
                """UPDATE eval_cases SET status=?, baseline_score=coalesce(?, baseline_score), updated_at=?
                   WHERE case_id=?""",
                (status, baseline_score, now, case_id),
            )
            updated = db.execute(
                "SELECT * FROM eval_cases WHERE case_id=?", (case_id,)
            ).fetchone()
        return self._row_to_case(updated)

    def get_case(self, case_id: str) -> dict[str, Any]:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM eval_cases WHERE case_id=?", (case_id,)
            ).fetchone()
            if not row:
                raise KeyError(f"评测用例不存在：{case_id}")
            return self._row_to_case(row)

    def list_cases(self, statuses: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        with self.connection() as db:
            if statuses:
                placeholders = ",".join("?" for _ in statuses)
                rows = db.execute(
                    f"SELECT * FROM eval_cases WHERE status IN ({placeholders}) ORDER BY case_id ASC",
                    statuses,
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM eval_cases ORDER BY case_id ASC"
                ).fetchall()
            return [self._row_to_case(r) for r in rows]

    @staticmethod
    def _row_to_case(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "case_id": row["case_id"],
            "title": row["title"],
            "status": row["status"],
            "base_commit": row["base_commit"],
            "base_patch": row["base_patch"],
            "prompt": row["prompt"],
            "checks": json.loads(row["checks_json"]),
            "rubric": json.loads(row["rubric_json"]),
            "weights": json.loads(row["weights_json"]),
            "baseline_score": row["baseline_score"],
            "origin_friction_id": row["origin_friction_id"],
            "case_digest": row["case_digest"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # --- 评测回执与进化运行记录 ---

    def save_receipt(
        self,
        *,
        run_id: str,
        version: int,
        case_id: str,
        run_index: int,
        score: float,
        checks_score: float,
        trajectory_score: float,
        rubric_score: float | None,
        details: dict[str, Any],
        trace_path: str,
    ) -> None:
        now = utc_now()
        with self.connection(write=True) as db:
            db.execute(
                "INSERT OR REPLACE INTO eval_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    version,
                    case_id,
                    run_index,
                    round(score, 2),
                    round(checks_score, 2),
                    round(trajectory_score, 2),
                    round(rubric_score, 2) if rubric_score is not None else None,
                    json.dumps(details, ensure_ascii=False),
                    trace_path,
                    now,
                ),
            )

    def create_evolution_run(
        self, run_id: str, optimizer_name: str, config: dict[str, Any]
    ) -> None:
        now = utc_now()
        with self.connection(write=True) as db:
            db.execute(
                "INSERT OR REPLACE INTO evolution_runs VALUES (?,?,?,0.0,'running',NULL,NULL,?,NULL)",
                (run_id, optimizer_name, json.dumps(config, ensure_ascii=False), now),
            )

    def finish_evolution_run(
        self,
        run_id: str,
        *,
        status: str,
        stop_reason: str,
        eit_variance: float,
        result: dict[str, Any],
    ) -> None:
        now = utc_now()
        with self.connection(write=True) as db:
            db.execute(
                """UPDATE evolution_runs
                   SET status=?, stop_reason=?, eit_variance=?, result_json=?, ended_at=?
                   WHERE run_id=?""",
                (
                    status,
                    stop_reason,
                    round(eit_variance, 4),
                    json.dumps(result, ensure_ascii=False),
                    now,
                    run_id,
                ),
            )

    def summary(self) -> dict[str, Any]:
        """汇总仓库当前 RSI 全貌（元数据、活动头、版本数、EIT 列表、回归集、最近运行）。"""
        meta = self.meta()
        head = self.head()
        with self.connection() as db:
            history = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM head_history ORDER BY revision DESC LIMIT 20"
                ).fetchall()
            ]
            runs = [
                {
                    "run_id": r["run_id"],
                    "optimizer_name": r["optimizer_name"],
                    "status": r["status"],
                    "stop_reason": r["stop_reason"],
                    "eit_variance": r["eit_variance"],
                    "created_at": r["created_at"],
                    "ended_at": r["ended_at"],
                }
                for r in db.execute(
                    "SELECT * FROM evolution_runs ORDER BY created_at DESC LIMIT 10"
                ).fetchall()
            ]
        return {
            "meta": meta,
            "head": {
                "version": head.version,
                "revision": head.revision,
                "digest": head.digest,
                "updated_at": head.updated_at,
            },
            "versions": self.list_versions(),
            "eit_issues": self.list_eit_issues(),
            "cases": self.list_cases(),
            "frictions": self.list_frictions(limit=20),
            "head_history": history,
            "recent_runs": runs,
        }
