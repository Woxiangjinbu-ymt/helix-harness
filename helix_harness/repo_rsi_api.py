"""单仓库自进化（Per-Repo RSI）本机 Web API。

打通日常 Coding Workspace 与单仓库 RSI 引擎：
- 纳管本地 Git 仓库并管理其独立的版本谱系、EIT 台账、回归集
- 扫描 coding.sqlite 识别用户纠偏与工具摩擦
- F23：从 Friction 合成/确认/校准仓库评测用例
- 启动多轮融合进化（hybrid / ace / skilladam）与主动回滚
- F24：将已验证 Harness 绑定到 Coding Workspace 的对应仓库工作树（下轮自动生效）
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from mewcode.rsi.repo import (
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
from mewcode.rsi.repo.case_synthesizer import git_head_commit
from . import providers as web_providers
from .coding import routes as coding_routes
from .paths import APP_DATA

router = APIRouter(prefix="/api/repo-rsi", tags=["repo-rsi"])


def repo_rsi_root() -> Path:
    root = Path(os.getenv("REPO_RSI_HOME", APP_DATA / "RepoRSI")).expanduser().resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def _store_for(repo_id: str) -> RepoRSIStore:
    state_dir = repo_rsi_root() / repo_id
    if not (state_dir / "repo_rsi.sqlite").is_file():
        raise HTTPException(404, f"仓库 RSI 状态不存在：{repo_id}")
    return RepoRSIStore(state_dir)


def _select_provider(name: str | None):
    if not name:
        return None
    _, provider = web_providers.select(name)
    return provider


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


@router.post("/repos", status_code=201)
def register_repo(form: RegisterRepoForm):
    root = Path(form.repo_root).expanduser().resolve()
    if not root.is_dir():
        raise HTTPException(422, f"仓库目录不存在：{root}")
    rid = repo_id_for_root(root)
    store = RepoRSIStore(repo_rsi_root() / rid, repo_root=root, name=form.name)
    return store.summary()


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
                }
            )
    return {"repos": items}


@router.get("/repos/{repo_id}")
def get_repo(repo_id: str):
    return _store_for(repo_id).summary()


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
