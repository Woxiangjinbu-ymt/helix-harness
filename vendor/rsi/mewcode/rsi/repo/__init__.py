"""单仓库自进化（Per-Repo RSI）核心引擎。

在保持既有冻结 Benchmark（repo-python-v1）散列与评测逻辑完全不动的前提下，
提供面向真实开发仓库的“在线偏差感知 -> 仓库回归用例合成 -> 融合 RSI 优化 -> 隔离评测与防回退晋升”完整闭环。
"""

from .case_synthesizer import approve_case, synthesize_case_from_friction
from .evaluator import (
    CaseEvalResult,
    RepoSuiteEvaluator,
    combine_scores,
    run_checks,
    score_trajectory,
)
from .friction import (
    TurnTrace,
    detect_deterministic_frictions,
    load_turns_from_coding_db,
    scan_and_record_frictions,
)
from .harness import (
    GuidedTool,
    PlaybookRule,
    RepoHarnessInputs,
    apply_playbook_ops,
    build_guidance_hooks,
    init_default_harness,
    validate_and_assemble_harness,
)
from .optimizer import (
    AdaptiveEditBudget,
    CriticVerdict,
    HarnessCritic,
    PlaybookPruner,
    RepoHybridOptimizer,
    materialize_proposal_edits,
)
from .orchestrator import RepoRSIOrchestrator, export_active_harness_to_repo
from .store import HeadRef, RepoRSIStore, RepoSnapshots, repo_id_for_root

__all__ = [
    "AdaptiveEditBudget",
    "CaseEvalResult",
    "CriticVerdict",
    "GuidedTool",
    "HarnessCritic",
    "HeadRef",
    "PlaybookPruner",
    "PlaybookRule",
    "RepoHarnessInputs",
    "RepoHybridOptimizer",
    "RepoRSIOrchestrator",
    "RepoRSIStore",
    "RepoSnapshots",
    "RepoSuiteEvaluator",
    "TurnTrace",
    "apply_playbook_ops",
    "approve_case",
    "build_guidance_hooks",
    "combine_scores",
    "detect_deterministic_frictions",
    "export_active_harness_to_repo",
    "init_default_harness",
    "load_turns_from_coding_db",
    "materialize_proposal_edits",
    "repo_id_for_root",
    "run_checks",
    "scan_and_record_frictions",
    "score_trajectory",
    "synthesize_case_from_friction",
    "validate_and_assemble_harness",
]
