"""单仓库自进化（Per-Repo RSI）本机 Web API。

打通日常 Coding Workspace 与单仓库 RSI 引擎：
- 纳管本地 Git 仓库并管理其独立的版本谱系、EIT 台账、回归集
- 扫描 coding.sqlite 识别用户纠偏与工具摩擦
- F23：从 Friction 合成/确认/校准仓库评测用例
- 支持同步与后台异步多轮融合进化（hybrid / ace / skilladam）、实时进度查询、版本文件/Diff 检查与主动回滚
- F24：将已验证 Harness 绑定到 Coding Workspace 的对应仓库工作树（下轮自动生效）
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
import difflib
import os
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from mtcode.config import ProviderConfig
from pydantic import BaseModel, Field

from helix_harness.rsi.repo import (
    RepoRSIOrchestrator,
    RepoRSIStore,
    RepoSuiteEvaluator,
    approve_case,
    export_active_harness_to_repo,
    load_turns_from_coding_db,
    repo_id_for_root,
    scan_and_record_frictions,
    synthesize_case_from_friction,
)
from helix_harness.rsi.repo.case_synthesizer import git_head_commit
from helix_harness.rsi.repo.store import utc_now
from . import providers as web_providers
from .coding import profiles as coding_profiles
from .coding import routes as coding_routes
from .coding.store import encode
from .coding.worktrees import Worktree
from .paths import APP_DATA

router = APIRouter(prefix="/api/repo-rsi", tags=["repo-rsi"])

# 本机后台作业表（存放异步校准/进化实时进度与最终结果）
_JOBS: dict[str, dict[str, Any]] = {}
_TASKS: dict[str, asyncio.Task[Any]] = {}


def repo_rsi_root() -> Path:
    root = Path(os.getenv("REPO_RSI_HOME", APP_DATA / "RepoRSI")).expanduser().resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def _store_for(repo_id: str) -> RepoRSIStore:
    state_dir = repo_rsi_root() / repo_id
    if not (state_dir / "repo_rsi.sqlite").is_file():
        raise HTTPException(404, f"仓库 RSI 状态不存在：{repo_id}")
    return RepoRSIStore(state_dir)


def _select_provider(name: str | None) -> ProviderConfig | None:
    """解析原生 providers.yaml 或 Coding Web 保存的模型配置（绝不泄露密钥给前端）。"""
    if not name:
        return None
    if name.startswith("web:"):
        cstore = coding_routes.store()
        snap = coding_profiles._profile_snapshot(cstore, name)
        secret = coding_profiles.secret_for(snap, cstore)
        return ProviderConfig(
            name=snap["profile_id"],
            protocol=snap["protocol"],
            base_url=snap["endpoint"],
            model=snap["model"],
            api_key=secret,
            thinking=bool(snap.get("thinking", False)),
            context_window=snap.get("context_window"),
            max_output_tokens=snap.get("max_output_tokens"),
        )
    _, provider = web_providers.select(name)
    return provider


def _coding_binding_for(repo_root: str) -> dict[str, Any] | None:
    """查询该仓库当前是否已在 Coding Workspace 中绑定了已验证 Harness。"""
    try:
        cstore = coding_routes.store()
        if not cstore.path.is_file():
            return None
        tree, _ = Worktree.identify(repo_root)
        identity = encode(asdict(tree))
        with cstore.connection() as db:
            row = db.execute(
                "SELECT id FROM trees WHERE identity=?", (identity,)
            ).fetchone()
            if not row:
                return None
            binding = cstore.get_repo_harness(row["id"], db)
            if not binding:
                return None
            return {
                "worktree_id": binding["worktree_id"],
                "repo_id": binding["repo_id"],
                "version": binding["version"],
                "revision": binding["revision"],
                "digest": binding["digest"],
                "updated": binding["updated"],
            }
    except Exception:
        return None


def _unified_diff(old_files: dict[str, str], new_files: dict[str, str]) -> str:
    chunks: list[str] = []
    for path in sorted(old_files.keys() | new_files.keys()):
        before = (old_files.get(path) or "").splitlines(keepends=True)
        after = (new_files.get(path) or "").splitlines(keepends=True)
        if before == after:
            continue
        header = [f"diff --git a/{path} b/{path}\n"]
        if path not in old_files:
            header.append("new file\n")
        elif path not in new_files:
            header.append("deleted file\n")
        diff_lines = list(
            difflib.unified_diff(
                before,
                after,
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            )
        )
        chunks.append("".join(header + diff_lines))
    return "\n".join(chunks)


class RegisterRepoForm(BaseModel):
    repo_root: str
    name: str = ""


class ScanFrictionForm(BaseModel):
    session_id: str | None = None
    provider: str | None = None
    limit_sessions: int = Field(default=10, ge=1, le=50)


class CreateCaseForm(BaseModel):
    friction_id: str | None = None
    case_id: str | None = None
    title: str = ""
    prompt: str = ""
    base_commit: str | None = None
    base_patch: str = ""
    checks: list[dict[str, Any]] = Field(default_factory=list)
    rubric: list[dict[str, Any]] = Field(default_factory=list)
    weights: dict[str, float] | None = None
    provider: str | None = None
    auto_approve: bool = False
    freeze: bool = False


class ApproveCaseForm(BaseModel):
    prompt: str | None = None
    checks: list[dict[str, Any]] | None = None
    rubric: list[dict[str, Any]] | None = None
    weights: dict[str, float] | None = None


class CalibrateCaseForm(BaseModel):
    provider: str
    judge_provider: str | None = None
    max_baseline_score: float = Field(default=85.0, ge=0.0, le=100.0)


class EvolveForm(BaseModel):
    provider: str
    optimizer_provider: str | None = None
    judge_provider: str | None = None
    mode: Literal["hybrid", "ace", "skilladam"] = "hybrid"
    rounds: int = Field(default=3, ge=1, le=10)
    runs_per_case: int = Field(default=1, ge=1, le=5)
    target_score: float = Field(default=95.0, ge=1.0, le=100.0)
    max_regression: float = Field(default=5.0, ge=0.0, le=50.0)
    include_calibrated: bool = False


class RollbackForm(BaseModel):
    target_version: int = Field(ge=1)
    expected_revision: int | None = None
    reason: str = "Web 主动回滚"


class BindHarnessForm(BaseModel):
    version: int | None = Field(default=None, ge=1)
    export_to_repo: bool = False


@router.get("/providers")
def list_available_providers():
    """列出可供单仓库 RSI 使用的真实模型配置（原生 + Coding Web，均不含密钥）。"""
    cstore = coding_routes.store()
    items = []
    for p in coding_profiles.list_profiles(cstore):
        if p.get("source") == "demo" or not p.get("configured"):
            continue
        items.append(
            {
                "id": p["profile_id"],
                "provider": p["provider"],
                "model": p["model"],
                "protocol": p.get("protocol", ""),
                "source": p["source"],
            }
        )
    return {"providers": items}


@router.post("/repos", status_code=201)
def register_repo(form: RegisterRepoForm):
    root = Path(form.repo_root).expanduser().resolve()
    if not root.is_dir():
        raise HTTPException(422, f"仓库目录不存在：{root}")
    rid = repo_id_for_root(root)
    store = RepoRSIStore(repo_rsi_root() / rid, repo_root=root, name=form.name)
    data = store.summary()
    data["coding_binding"] = _coding_binding_for(str(root))
    return data


@router.get("/repos")
def list_repos():
    items = []
    for entry in sorted(repo_rsi_root().iterdir()):
        if entry.is_dir() and (entry / "repo_rsi.sqlite").is_file():
            store = RepoRSIStore(entry)
            meta = store.meta()
            head = store.head()
            ver = store.get_version(head.version)
            items.append(
                {
                    "repo_id": meta["repo_id"],
                    "name": meta["name"],
                    "repo_root": meta["repo_root"],
                    "head_version": head.version,
                    "head_revision": head.revision,
                    "head_digest": head.digest,
                    "mean_score": ver["mean_score"],
                    "open_issues": sum(
                        1
                        for i in store.list_eit_issues()
                        if i["status"] in ("open", "reopened")
                    ),
                    "frozen_cases": len(store.list_cases(statuses=("frozen",))),
                    "total_cases": len(store.list_cases()),
                    "coding_binding": _coding_binding_for(meta["repo_root"]),
                }
            )
    return {"repos": items}


@router.get("/repos/{repo_id}")
def get_repo(repo_id: str):
    store = _store_for(repo_id)
    data = store.summary()
    data["coding_binding"] = _coding_binding_for(data["meta"]["repo_root"])
    return data


@router.get("/repos/{repo_id}/versions/{version}")
def get_repo_version(repo_id: str, version: int):
    """查看指定版本的策略包完整文件及相对父版本的 unified diff。"""
    store = _store_for(repo_id)
    ver = store.get_version(version)
    assembled = store.snapshots.load(ver["digest"])
    parent_files: dict[str, str] = {}
    if ver["parent"] is not None:
        parent_ver = store.get_version(ver["parent"])
        parent_files = store.snapshots.load(parent_ver["digest"]).files
    return {
        "version": ver,
        "files": assembled.files,
        "diff": _unified_diff(parent_files, assembled.files) if parent_files else "",
        "playbook_count": len(assembled.playbook_rules),
        "tool_overlays": assembled.description_overlays,
        "tool_hooks": list(assembled.tool_hooks),
        "skills": sorted(assembled.skills.keys()),
    }


@router.post("/repos/{repo_id}/friction/scan")
async def scan_friction(repo_id: str, form: ScanFrictionForm):
    store = _store_for(repo_id)
    coding_db = coding_routes.store().path
    turns = load_turns_from_coding_db(
        coding_db,
        repo_root=store.meta()["repo_root"],
        session_id=form.session_id,
        limit_sessions=form.limit_sessions,
    )
    provider = _select_provider(form.provider)
    return await scan_and_record_frictions(store, turns, provider=provider)


@router.post("/repos/{repo_id}/cases", status_code=201)
async def create_case(repo_id: str, form: CreateCaseForm):
    store = _store_for(repo_id)
    if form.friction_id:
        provider = _select_provider(form.provider)
        return await synthesize_case_from_friction(
            store,
            form.friction_id,
            case_id=form.case_id,
            provider=provider,
            extra_checks=form.checks,
            extra_rubric=form.rubric,
            base_commit=form.base_commit,
            base_patch=form.base_patch,
            auto_approve=form.auto_approve,
        )
    if not form.case_id or not form.prompt.strip():
        raise HTTPException(422, "手工创建评测用例时需提供 case_id 与 prompt")
    commit = form.base_commit or git_head_commit(Path(store.meta()["repo_root"]))
    status = "frozen" if form.freeze else ("calibrated" if form.auto_approve else "draft")
    return store.save_case(
        case_id=form.case_id,
        title=form.title or form.case_id,
        base_commit=commit,
        base_patch=form.base_patch,
        prompt=form.prompt,
        checks=form.checks,
        rubric=form.rubric,
        weights=form.weights,
        status=status,
    )


@router.post("/repos/{repo_id}/cases/{case_id}/approve")
def approve_repo_case(repo_id: str, case_id: str, form: ApproveCaseForm):
    store = _store_for(repo_id)
    return approve_case(
        store,
        case_id,
        checks=form.checks,
        rubric=form.rubric,
        prompt=form.prompt,
        weights=form.weights,
    )


@router.post("/repos/{repo_id}/cases/{case_id}/calibrate")
async def calibrate_repo_case(repo_id: str, case_id: str, form: CalibrateCaseForm):
    store = _store_for(repo_id)
    provider = _select_provider(form.provider)
    judge = _select_provider(form.judge_provider or form.provider)
    evaluator = RepoSuiteEvaluator(store, provider=provider, judge_provider=judge)
    return await evaluator.calibrate_case(
        case_id, max_baseline_score=form.max_baseline_score
    )


@router.post("/repos/{repo_id}/cases/{case_id}/calibrate/start", status_code=202)
async def start_calibrate_job(repo_id: str, case_id: str, form: CalibrateCaseForm):
    """后台异步启动单题基线校准，避免长模型请求触发前端超时。"""
    store = _store_for(repo_id)
    store.get_case(case_id)
    provider = _select_provider(form.provider)
    judge = _select_provider(form.judge_provider or form.provider)
    job_id = f"cal-{uuid4().hex[:10]}"
    job: dict[str, Any] = {
        "job_id": job_id,
        "repo_id": repo_id,
        "kind": "calibrate",
        "case_id": case_id,
        "status": "running",
        "started_at": utc_now(),
        "ended_at": None,
        "events": [{"time": utc_now(), "kind": "calibrate_started", "data": {"case_id": case_id}}],
        "result": None,
        "error": None,
    }
    _JOBS[job_id] = job

    async def _run():
        try:
            evaluator = RepoSuiteEvaluator(store, provider=provider, judge_provider=judge)
            res = await evaluator.calibrate_case(
                case_id, max_baseline_score=form.max_baseline_score
            )
            job["result"] = res
            job["status"] = "completed"
            job["events"].append({"time": utc_now(), "kind": "calibrate_completed", "data": res})
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = str(exc)
            job["events"].append({"time": utc_now(), "kind": "failed", "data": {"error": str(exc)}})
        finally:
            job["ended_at"] = utc_now()
            _TASKS.pop(job_id, None)

    _TASKS[job_id] = asyncio.create_task(_run())
    return job


@router.post("/repos/{repo_id}/evolve")
async def evolve_repo(repo_id: str, form: EvolveForm):
    store = _store_for(repo_id)
    provider = _select_provider(form.provider)
    opt_provider = _select_provider(form.optimizer_provider or form.provider)
    judge = _select_provider(form.judge_provider or form.provider)
    evaluator = RepoSuiteEvaluator(store, provider=provider, judge_provider=judge)
    orch = RepoRSIOrchestrator(
        store, evaluator, optimizer_provider=opt_provider, mode=form.mode
    )
    return await orch.evolve(
        rounds=form.rounds,
        target_score=form.target_score,
        runs_per_case=form.runs_per_case,
        max_regression=form.max_regression,
        include_calibrated=form.include_calibrated,
    )


@router.post("/repos/{repo_id}/evolve/start", status_code=202)
async def start_evolve_job(repo_id: str, form: EvolveForm):
    """后台异步启动仓库级多轮进化，并实时记录每阶段事件供前端轮询展示。"""
    store = _store_for(repo_id)
    provider = _select_provider(form.provider)
    opt_provider = _select_provider(form.optimizer_provider or form.provider)
    judge = _select_provider(form.judge_provider or form.provider)
    job_id = f"evo-{uuid4().hex[:10]}"
    job: dict[str, Any] = {
        "job_id": job_id,
        "repo_id": repo_id,
        "kind": "evolve",
        "mode": form.mode,
        "status": "running",
        "started_at": utc_now(),
        "ended_at": None,
        "events": [],
        "result": None,
        "error": None,
    }
    _JOBS[job_id] = job

    def _notify(kind: str, data: dict[str, Any]) -> None:
        job["events"].append({"time": utc_now(), "kind": kind, "data": data})

    async def _run():
        try:
            evaluator = RepoSuiteEvaluator(store, provider=provider, judge_provider=judge)
            orch = RepoRSIOrchestrator(
                store, evaluator, optimizer_provider=opt_provider, mode=form.mode
            )
            res = await orch.evolve(
                rounds=form.rounds,
                target_score=form.target_score,
                runs_per_case=form.runs_per_case,
                max_regression=form.max_regression,
                include_calibrated=form.include_calibrated,
                notify=_notify,
            )
            job["result"] = res
            job["status"] = "completed"
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = str(exc)
            _notify("failed", {"error": str(exc)})
        finally:
            job["ended_at"] = utc_now()
            _TASKS.pop(job_id, None)

    _TASKS[job_id] = asyncio.create_task(_run())
    return job


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    if job_id not in _JOBS:
        raise HTTPException(404, f"作业不存在：{job_id}")
    return _JOBS[job_id]


@router.get("/repos/{repo_id}/jobs")
def list_repo_jobs(repo_id: str):
    jobs = [j for j in _JOBS.values() if j["repo_id"] == repo_id]
    jobs.sort(key=lambda j: j["started_at"], reverse=True)
    return {"jobs": jobs[:20]}


@router.post("/repos/{repo_id}/rollback")
def rollback_repo(repo_id: str, form: RollbackForm):
    store = _store_for(repo_id)
    try:
        head = store.rollback(
            form.target_version,
            expected_revision=form.expected_revision,
            reason=form.reason,
        )
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "repo_id": head.repo_id,
        "version": head.version,
        "revision": head.revision,
        "digest": head.digest,
        "updated_at": head.updated_at,
    }


@router.post("/repos/{repo_id}/bind")
def bind_to_coding(repo_id: str, form: BindHarnessForm):
    """F24：将指定（或当前活动）已验证的仓库 Harness 本地副本绑定到 Coding Workspace。"""
    store = _store_for(repo_id)
    head = store.head()
    target_version = form.version if form.version is not None else head.version
    ver = store.get_version(target_version)
    if ver["status"] not in ("baseline", "accepted"):
        raise HTTPException(
            422,
            f"版本 v{target_version} 未通过评测验证（status={ver['status']}），不能绑定到 Coding",
        )
    assembled = store.snapshots.load(ver["digest"])
    bundle = {
        "instructions": assembled.instructions,
        "skills": assembled.skills,
        "description_overlays": assembled.description_overlays,
        "tool_hooks": list(assembled.tool_hooks),
    }
    binding = coding_routes.store().bind_repo_harness(
        store.meta()["repo_root"],
        repo_id=head.repo_id,
        version=target_version,
        revision=head.revision,
        harness_digest=ver["digest"],
        bundle=bundle,
    )
    exported = None
    if form.export_to_repo:
        exported = export_active_harness_to_repo(store)
    return {"binding": binding, "exported": exported}
