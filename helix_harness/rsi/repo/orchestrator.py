"""仓库级 RSI 进化编排器：闭环调度基线、自适应预算提案、前置 Critic、隔离评测与防回退晋升。"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean
from typing import Any, Callable
from uuid import uuid4

from ..default_optimizer import InvalidProposal, StrategyOptimizer
from .evaluator import CaseEvalResult, RepoSuiteEvaluator
from .harness import apply_playbook_ops
from .optimizer import (
    AdaptiveEditBudget,
    HarnessCritic,
    PlaybookPruner,
    RepoHybridOptimizer,
    materialize_proposal_edits,
)
from .store import RepoRSIStore


def _silent_notify(kind: str, payload: dict[str, Any]) -> None:
    pass


class RepoRSIOrchestrator:
    """面向单个仓库的自进化闭环控制器。"""

    def __init__(
        self,
        store: RepoRSIStore,
        evaluator: RepoSuiteEvaluator,
        *,
        optimizer: StrategyOptimizer | None = None,
        optimizer_provider: Any | None = None,
        critic: HarnessCritic | None = None,
        mode: str = "hybrid",
    ) -> None:
        self.store = store
        self.evaluator = evaluator
        self.mode = mode
        if optimizer is not None:
            self.optimizer = optimizer
        elif optimizer_provider is not None:
            self.optimizer = RepoHybridOptimizer(optimizer_provider, mode=mode)
        else:
            self.optimizer = None
        self.critic = critic or HarnessCritic()
        self.pruner = PlaybookPruner()

    async def evaluate_version(
        self,
        version: int,
        digest_key: str,
        cases: list[dict[str, Any]],
        *,
        run_id: str,
        runs_per_case: int = 1,
        notify: Callable[[str, dict[str, Any]], None] = _silent_notify,
    ) -> tuple[float, dict[str, float], list[dict[str, Any]]]:
        harness = self.store.snapshots.load(digest_key)
        per_case: dict[str, float] = {}
        observations: list[dict[str, Any]] = []
        for case in cases:
            cid = case["case_id"]
            run_scores: list[float] = []
            for r_idx in range(1, runs_per_case + 1):
                notify("cell_started", {"version": version, "case_id": cid, "run": r_idx})
                res: CaseEvalResult = await self.evaluator.evaluate_case(
                    case,
                    harness,
                    version=version,
                    run_id=run_id,
                    run_index=r_idx,
                )
                run_scores.append(res.score)
                obs = res.to_public_observation()
                observations.append(obs)
                notify(
                    "cell_completed",
                    {
                        "version": version,
                        "case_id": cid,
                        "run": r_idx,
                        "score": res.score,
                        "checks_score": res.checks_score,
                        "trajectory_score": res.trajectory_score,
                        "rubric_score": res.rubric_score,
                    },
                )
            per_case[cid] = round(mean(run_scores), 2)
        total_mean = round(mean(per_case.values()), 2) if per_case else 0.0
        return total_mean, per_case, observations

    async def evolve(
        self,
        *,
        rounds: int = 3,
        target_score: float = 95.0,
        runs_per_case: int = 1,
        max_regression: float = 5.0,
        regression_floor: float = 70.0,
        base_budget: int = 4,
        min_budget: int = 1,
        prune_interval: int = 3,
        include_calibrated: bool = False,
        notify: Callable[[str, dict[str, Any]], None] = _silent_notify,
    ) -> dict[str, Any]:
        """执行仓库级多轮验证进化，严格要求均分提升且无历史回归用例退化。"""
        if self.optimizer is None:
            raise ValueError("启动进化需要提供 optimizer 或 optimizer_provider")
        if rounds < 1 or runs_per_case < 1:
            raise ValueError("rounds 与 runs_per_case 必须为正整数")

        statuses = ("frozen", "calibrated") if include_calibrated else ("frozen",)
        cases = self.store.list_cases(statuses=statuses)
        if not cases:
            raise ValueError("当前仓库没有已冻结的回归评测用例（请先合成并校准至少一条用例）")

        run_id = f"evo-{uuid4().hex[:10]}"
        config = {
            "rounds": rounds,
            "target_score": target_score,
            "runs_per_case": runs_per_case,
            "max_regression": max_regression,
            "mode": self.mode,
            "base_budget": base_budget,
            "min_budget": min_budget,
            "cases": [c["case_id"] for c in cases],
        }
        self.store.create_evolution_run(run_id, f"{type(self.optimizer).__name__}:{self.mode}", config)

        head = self.store.head()
        budget = AdaptiveEditBudget(base_budget=base_budget, min_budget=min_budget)
        case_ids = {c["case_id"] for c in cases}

        # 1. 基线测量（每次进化固定同一套回归集，确保前后严格可比）
        notify("baseline_started", {"version": head.version, "cases": sorted(case_ids)})
        ref_mean, ref_per_case, ref_observations = await self.evaluate_version(
            head.version,
            head.digest,
            cases,
            run_id=f"{run_id}-base",
            runs_per_case=runs_per_case,
            notify=notify,
        )
        self.store.record_baseline_score(head.version, ref_mean, ref_per_case)
        notify(
            "baseline_completed",
            {"version": head.version, "mean_score": ref_mean, "per_case": ref_per_case},
        )

        initial_version = head.version
        initial_mean = ref_mean
        history: list[dict[str, Any]] = []
        stop_reason = "round_limit"

        for r_idx in range(1, rounds + 1):
            head = self.store.head()
            if ref_mean >= target_score:
                stop_reason = "target_reached"
                break

            edit_budget = budget.current_budget()
            current_harness = self.store.snapshots.load(head.digest)
            public_payload: dict[str, Any] = {
                "repo": self.store.meta(),
                "parent": head.version,
                "mode": self.mode,
                "round": r_idx,
                "edit_budget": edit_budget,
                "reference_score": ref_mean,
                "per_case_scores": ref_per_case,
                "cases": [
                    {
                        "case_id": c["case_id"],
                        "title": c["title"],
                        "prompt": c["prompt"],
                    }
                    for c in cases
                ],
                "strategy": current_harness.files,
                "observations": ref_observations,
                "eit_issues": self.store.list_eit_issues(),
                "history": history[-6:],
            }

            notify(
                "round_started",
                {
                    "round": r_idx,
                    "parent": head.version,
                    "edit_budget": edit_budget,
                    "running_variance": round(budget.running_variance, 3),
                },
            )

            # 2. 提案 + 前置 Critic 与装配校验门控（允许被拒后带反对理由有界重修 1 次）
            proposal: dict[str, Any] | None = None
            edits = ()
            verdict = None
            cand_digest: str | None = None
            critic_rejected = False
            for attempt_idx in range(2):
                try:
                    proposal = await self.optimizer.propose(public_payload)
                except InvalidProposal as exc:
                    verdict_dict = {"verdict": "reject", "reasons": [str(exc)], "risk_notes": []}
                    history.append(
                        {"round": r_idx, "decision": "invalid_proposal", "critic": verdict_dict}
                    )
                    critic_rejected = True
                    break
                if proposal is None:
                    stop_reason = "no_proposal"
                    break

                # 周期性 Pruner：若到清理周期，自动把净负效用/溢出规则的 prune ops 注入
                if prune_interval > 0 and r_idx % prune_interval == 0:
                    prune_ops = self.pruner.compute_prune_ops(
                        current_harness.files.get("playbook.yaml")
                    )
                    if prune_ops:
                        existing_ops = list(proposal.get("playbook_ops") or [])
                        existing_ids = {o.get("id") for o in existing_ops if isinstance(o, dict)}
                        for po in prune_ops:
                            if po["id"] not in existing_ids:
                                existing_ops.append(po)
                        proposal = dict(proposal, playbook_ops=existing_ops)

                try:
                    edits = materialize_proposal_edits(current_harness.files, proposal)
                except (InvalidProposal, ValueError) as exc:
                    if attempt_idx == 0:
                        public_payload["critic_feedback"] = [f"格式/合并失败：{exc}"]
                        notify("critic_retry", {"round": r_idx, "reasons": [str(exc)]})
                        continue
                    history.append(
                        {
                            "round": r_idx,
                            "decision": "invalid_proposal",
                            "critic": {"verdict": "reject", "reasons": [str(exc)]},
                        }
                    )
                    critic_rejected = True
                    break

                verdict = await self.critic.screen(
                    edits,
                    hypothesis=str(proposal.get("hypothesis", "")),
                    case_ids=case_ids,
                    edit_budget=edit_budget,
                    mode=self.mode,
                )
                if not verdict.accepted:
                    if attempt_idx == 0:
                        public_payload["critic_feedback"] = list(verdict.reasons)
                        notify("critic_retry", {"round": r_idx, "reasons": list(verdict.reasons)})
                        continue
                    critic_rejected = True
                    break

                # 装配并封存快照（若 YAML/Skill 装配校验失败同样给予 1 次有界修复机会）
                try:
                    cand_digest = self.store.snapshots.seal_edits(head.digest, edits)
                    break
                except InvalidProposal as exc:
                    if attempt_idx == 0:
                        public_payload["critic_feedback"] = [str(exc)]
                        notify("critic_retry", {"round": r_idx, "reasons": [str(exc)]})
                        continue
                    history.append(
                        {
                            "round": r_idx,
                            "decision": "invalid_proposal",
                            "hypothesis": proposal.get("hypothesis", ""),
                            "critic": {"verdict": "reject", "reasons": [str(exc)]},
                        }
                    )
                    critic_rejected = True
                    break

            if proposal is None and not critic_rejected:
                break
            if critic_rejected or verdict is None or not verdict.accepted or cand_digest is None:
                if not history or history[-1].get("round") != r_idx:
                    verdict_dict = verdict.to_dict() if verdict else {"verdict": "reject", "reasons": ["invalid_proposal"]}
                    history.append(
                        {
                            "round": r_idx,
                            "decision": "critic_rejected",
                            "hypothesis": (proposal or {}).get("hypothesis", ""),
                            "critic": verdict_dict,
                        }
                    )
                    notify("critic_rejected", {"round": r_idx, "critic": verdict_dict})
                continue

            # 4. 隔离 Worktree 跑全量仓库回归集
            tentative_version = max(v["version"] for v in self.store.list_versions()) + 1
            cand_mean, cand_per_case, cand_observations = await self.evaluate_version(
                tentative_version,
                cand_digest,
                cases,
                run_id=f"{run_id}-r{r_idx}",
                runs_per_case=runs_per_case,
                notify=notify,
            )

            # 5. 更新 SkillAdam 逐题分差方差 -> 动态调整下轮编辑预算
            deltas = [cand_per_case[c] - ref_per_case[c] for c in sorted(case_ids)]
            next_budget = budget.update(deltas)

            # 6. 严格提分 + 历史回归不退化门控
            strictly_better = cand_mean > ref_mean
            regressions: list[dict[str, Any]] = []
            for cid in sorted(case_ids):
                before = ref_per_case[cid]
                after = cand_per_case[cid]
                if before >= regression_floor and (before - after) > max_regression:
                    regressions.append(
                        {"case_id": cid, "before": before, "after": after, "drop": round(before - after, 2)}
                    )
            promoted = strictly_better and not regressions
            decision = "accepted" if promoted else "rejected"
            summary_text = (
                f"Round {r_idx}: {ref_mean:.2f} -> {cand_mean:.2f} ({decision})"
                + (f", regressions={len(regressions)}" if regressions else "")
            )

            ver_rec, new_head = self.store.record_candidate(
                parent=head.version,
                expected_revision=head.revision,
                candidate_digest=cand_digest,
                status=decision,
                mean_score=cand_mean,
                per_case=cand_per_case,
                proposal=proposal,
                critic=verdict.to_dict(),
                summary=summary_text,
                promote=promoted,
            )

            # 7. 回写 EIT 问题台账（追踪每次尝试及是否真正缓解/解决）
            target_issues = [
                str(i) for i in (proposal.get("target_issues") or []) if isinstance(i, str)
            ]
            all_issues = {i["issue_id"]: i for i in self.store.list_eit_issues()}
            for iid in target_issues:
                if iid not in all_issues:
                    continue
                linked = all_issues[iid].get("linked_cases") or []
                new_status = None
                if promoted:
                    if linked and all(cand_per_case.get(lc, 0.0) >= 85.0 for lc in linked if lc in cand_per_case):
                        new_status = "resolved"
                    else:
                        new_status = "mitigated"
                self.store.record_eit_attempt(
                    iid,
                    version=ver_rec["version"],
                    hypothesis=str(proposal.get("hypothesis", "")),
                    outcome=decision,
                    score_delta=cand_mean - ref_mean,
                    new_status=new_status,
                )

            round_record = {
                "round": r_idx,
                "version": ver_rec["version"],
                "parent": head.version,
                "decision": decision,
                "mean_score": cand_mean,
                "score_delta": round(cand_mean - ref_mean, 2),
                "per_case": cand_per_case,
                "regressions": regressions,
                "changed_paths": [e.path for e in edits],
                "hypothesis": proposal.get("hypothesis", ""),
                "critic": verdict.to_dict(),
                "next_edit_budget": next_budget,
                "running_variance": round(budget.running_variance, 3),
            }
            history.append(round_record)
            notify("round_completed", round_record)

            if promoted:
                ref_mean = cand_mean
                ref_per_case = cand_per_case
                ref_observations = cand_observations
                head = new_head
                if ref_mean >= target_score:
                    stop_reason = "target_reached"
                    break

        final_head = self.store.head()
        result = {
            "run_id": run_id,
            "mode": self.mode,
            "initial_version": initial_version,
            "initial_score": initial_mean,
            "final_version": final_head.version,
            "final_revision": final_head.revision,
            "final_digest": final_head.digest,
            "final_score": ref_mean,
            "score_gain": round(ref_mean - initial_mean, 2),
            "per_case_scores": ref_per_case,
            "stop_reason": stop_reason,
            "rounds": history,
            "eit_issues": self.store.list_eit_issues(),
        }
        self.store.finish_evolution_run(
            run_id,
            status="completed",
            stop_reason=stop_reason,
            eit_variance=budget.running_variance,
            result=result,
        )
        return result


def export_active_harness_to_repo(
    store: RepoRSIStore, target_repo_root: Path | None = None
) -> dict[str, Any]:
    """将当前已验证的活动 Harness 同步导出到仓库 .mewcode/ 目录，使 CLI/TUI 也能直接热加载。"""
    meta = store.meta()
    repo_root = (target_repo_root or Path(meta["repo_root"])).expanduser().resolve()
    head = store.head()
    harness = store.snapshots.load(head.digest)

    mew_dir = repo_root / ".mewcode"
    skills_dir = mew_dir / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)

    exported_skills: list[str] = []
    for name, skill_data in harness.skills.items():
        sdir = skills_dir / name
        sdir.mkdir(parents=True, exist_ok=True)
        raw_skill = harness.files.get(f"skills/{name}/SKILL.md")
        if raw_skill:
            (sdir / "SKILL.md").write_text(raw_skill, encoding="utf-8")
            exported_skills.append(name)

    # 写出人类可读的仓库进化手册与元信息（供 CLI/TUI 查阅或 @./.mewcode/HARNESS.md 引用）
    (mew_dir / "HARNESS.md").write_text(harness.instructions + "\n", encoding="utf-8")
    binding_meta = {
        "repo_id": head.repo_id,
        "version": head.version,
        "revision": head.revision,
        "digest": head.digest,
        "exported_skills": exported_skills,
        "tool_overlays": harness.description_overlays,
        "tool_hooks": list(harness.tool_hooks),
    }
    (mew_dir / "harness_binding.json").write_text(
        json.dumps(binding_meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return binding_meta
