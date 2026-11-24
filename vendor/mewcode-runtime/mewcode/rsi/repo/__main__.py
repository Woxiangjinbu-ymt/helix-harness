"""单仓库自进化（Per-Repo RSI）命令行入口：python -m mewcode.rsi.repo ..."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from mewcode.config import load_config
from .case_synthesizer import approve_case, git_head_commit, synthesize_case_from_friction
from .evaluator import RepoSuiteEvaluator
from .friction import load_turns_from_coding_db, scan_and_record_frictions
from .orchestrator import RepoRSIOrchestrator, export_active_harness_to_repo
from .store import RepoRSIStore


def _load_provider(config_path: Path | None, provider_name: str | None):
    if not config_path or not provider_name:
        return None
    providers = {p.name: p for p in load_config(config_path).providers}
    if provider_name not in providers:
        raise ValueError(f"未在配置中找到 Provider：{provider_name}")
    return providers[provider_name]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mewcode.rsi.repo", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="初始化仓库级 RSI 存储与 v1 Harness")
    p_init.add_argument("--state-dir", type=Path, required=True)
    p_init.add_argument("--repo", type=Path, required=True)
    p_init.add_argument("--name", default="")

    p_status = sub.add_parser("status", help="查看当前仓库 Harness 版本、EIT 台账与回归集")
    p_status.add_argument("--state-dir", type=Path, required=True)

    p_scan = sub.add_parser("scan-friction", help="从 coding.sqlite 扫描交互纠偏与工具报错并写入 EIT")
    p_scan.add_argument("--state-dir", type=Path, required=True)
    p_scan.add_argument("--coding-db", type=Path, required=True)
    p_scan.add_argument("--session-id", default=None)
    p_scan.add_argument("--provider-config", type=Path, default=None)
    p_scan.add_argument("--provider", default=None)

    p_add = sub.add_parser("add-case", help="手工登记一条仓库回归评测用例（JSON 文件）")
    p_add.add_argument("--state-dir", type=Path, required=True)
    p_add.add_argument("--case-file", type=Path, required=True)
    p_add.add_argument("--freeze", action="store_true")

    p_syn = sub.add_parser("synthesize-case", help="从一条 Friction 事件自动合成评测用例")
    p_syn.add_argument("--state-dir", type=Path, required=True)
    p_syn.add_argument("--friction-id", required=True)
    p_syn.add_argument("--case-id", default=None)
    p_syn.add_argument("--provider-config", type=Path, default=None)
    p_syn.add_argument("--provider", default=None)
    p_syn.add_argument("--auto-approve", action="store_true")

    p_approve = sub.add_parser("approve-case", help="确认草稿评测用例")
    p_approve.add_argument("--state-dir", type=Path, required=True)
    p_approve.add_argument("--case-id", required=True)

    p_cal = sub.add_parser("calibrate", help="用当前活动 Harness 校准并冻结评测用例")
    p_cal.add_argument("--state-dir", type=Path, required=True)
    p_cal.add_argument("--case-id", required=True)
    p_cal.add_argument("--provider-config", type=Path, required=True)
    p_cal.add_argument("--provider", required=True)
    p_cal.add_argument("--judge-provider", default=None)
    p_cal.add_argument("--max-baseline-score", type=float, default=85.0)

    p_evo = sub.add_parser("evolve", help="启动仓库级 RSI 进化闭环")
    p_evo.add_argument("--state-dir", type=Path, required=True)
    p_evo.add_argument("--provider-config", type=Path, required=True)
    p_evo.add_argument("--provider", required=True)
    p_evo.add_argument("--optimizer-provider", default=None)
    p_evo.add_argument("--judge-provider", default=None)
    p_evo.add_argument("--mode", choices=("hybrid", "ace", "skilladam"), default="hybrid")
    p_evo.add_argument("--rounds", type=int, default=3)
    p_evo.add_argument("--runs", type=int, default=1)
    p_evo.add_argument("--target", type=float, default=95.0)
    p_evo.add_argument("--max-regression", type=float, default=5.0)

    p_rb = sub.add_parser("rollback", help="主动回滚至指定历史已验证版本")
    p_rb.add_argument("--state-dir", type=Path, required=True)
    p_rb.add_argument("--version", type=int, required=True)
    p_rb.add_argument("--reason", default="CLI 主动回滚")

    p_exp = sub.add_parser("export", help="将已验证活动 Harness 导出至仓库 .mewcode/ 目录")
    p_exp.add_argument("--state-dir", type=Path, required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            store = RepoRSIStore(args.state_dir, repo_root=args.repo, name=args.name)
            out = store.summary()
        elif args.command == "status":
            store = RepoRSIStore(args.state_dir)
            out = store.summary()
        elif args.command == "scan-friction":
            store = RepoRSIStore(args.state_dir)
            provider = _load_provider(args.provider_config, args.provider)
            turns = load_turns_from_coding_db(
                args.coding_db,
                repo_root=store.meta()["repo_root"],
                session_id=args.session_id,
            )
            out = asyncio.run(scan_and_record_frictions(store, turns, provider=provider))
        elif args.command == "add-case":
            store = RepoRSIStore(args.state_dir)
            data = json.loads(args.case_file.read_text(encoding="utf-8"))
            commit = data.get("base_commit") or git_head_commit(Path(store.meta()["repo_root"]))
            out = store.save_case(
                case_id=data["case_id"],
                title=data.get("title", data["case_id"]),
                base_commit=commit,
                base_patch=data.get("base_patch", ""),
                prompt=data["prompt"],
                checks=data.get("checks", []),
                rubric=data.get("rubric", []),
                weights=data.get("weights"),
                status="frozen" if args.freeze else data.get("status", "draft"),
            )
        elif args.command == "synthesize-case":
            store = RepoRSIStore(args.state_dir)
            provider = _load_provider(args.provider_config, args.provider)
            out = asyncio.run(
                synthesize_case_from_friction(
                    store,
                    args.friction_id,
                    case_id=args.case_id,
                    provider=provider,
                    auto_approve=args.auto_approve,
                )
            )
        elif args.command == "approve-case":
            store = RepoRSIStore(args.state_dir)
            out = approve_case(store, args.case_id)
        elif args.command == "calibrate":
            store = RepoRSIStore(args.state_dir)
            provider = _load_provider(args.provider_config, args.provider)
            judge = _load_provider(args.provider_config, args.judge_provider or args.provider)
            evaluator = RepoSuiteEvaluator(store, provider=provider, judge_provider=judge)
            out = asyncio.run(
                evaluator.calibrate_case(
                    args.case_id, max_baseline_score=args.max_baseline_score
                )
            )
        elif args.command == "evolve":
            store = RepoRSIStore(args.state_dir)
            provider = _load_provider(args.provider_config, args.provider)
            opt_provider = _load_provider(
                args.provider_config, args.optimizer_provider or args.provider
            )
            judge = _load_provider(args.provider_config, args.judge_provider or args.provider)
            evaluator = RepoSuiteEvaluator(store, provider=provider, judge_provider=judge)
            orch = RepoRSIOrchestrator(
                store, evaluator, optimizer_provider=opt_provider, mode=args.mode
            )
            out = asyncio.run(
                orch.evolve(
                    rounds=args.rounds,
                    target_score=args.target,
                    runs_per_case=args.runs,
                    max_regression=args.max_regression,
                )
            )
        elif args.command == "rollback":
            store = RepoRSIStore(args.state_dir)
            head = store.rollback(args.version, reason=args.reason)
            out = {"version": head.version, "revision": head.revision, "digest": head.digest}
        elif args.command == "export":
            store = RepoRSIStore(args.state_dir)
            out = export_active_harness_to_repo(store)
        else:
            raise ValueError(f"未知命令：{args.command}")
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
