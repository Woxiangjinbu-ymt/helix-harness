"""单仓库自进化（Per-Repo RSI）完整单元与端到端测试。

覆盖：
1. 仓库级 Harness 装配（兼容 default-v1 + playbook.yaml + tool_guidance.yaml + GuidedTool + Hook 护栏）
2. 在线偏差与工具摩擦捕获（Friction Detector + EIT 归并与复发重开）
3. 端到端“股票分析网站”单仓库进化闭环（Friction -> F23 合成 -> 基线校准 -> Critic 防作弊重修 -> 隔离评测 -> No-Regression 防回退晋升 -> 主动回滚 -> 导出 .mewcode/）
4. SkillAdam 方差自适应预算与 RRSI PlaybookPruner
5. Web API (/api/repo-rsi) 与 Coding Workspace F24 本地绑定生效
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
from typing import Any

import pytest
import yaml

from mewcode.client import LLMClient
from mewcode.hooks.engine import HookEngine
from mewcode.hooks.models import HookContext
from mewcode.rsi.default_optimizer import Edit
from mewcode.rsi.repo import (
    AdaptiveEditBudget,
    GuidedTool,
    HarnessCritic,
    PlaybookPruner,
    RepoRSIOrchestrator,
    RepoRSIStore,
    RepoSuiteEvaluator,
    TurnTrace,
    apply_playbook_ops,
    approve_case,
    build_guidance_hooks,
    export_active_harness_to_repo,
    scan_and_record_frictions,
    synthesize_case_from_friction,
    validate_and_assemble_harness,
)
from mewcode.rsi.repo.case_synthesizer import git_head_commit
from mewcode.tools.base import StreamEnd, TextDelta, ToolCallComplete
from mewcode.tools.edit_file import EditFile


def _init_stock_web_repo(repo_dir: Path) -> str:
    """初始化一个模拟的股票分析网站 Git 仓库，供端到端评测与进化测试使用。"""
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "src").mkdir(parents=True, exist_ok=True)
    (repo_dir / "src" / "Dashboard.tsx").write_text(
        """export function StockDashboard() {
  return (
    <div className="layout-legacy flex">
      <main className="w-1/2">
        <div id="kline-chart">Legacy Chart</div>
      </main>
      <aside className="sidebar-indicators w-1/2">
        <table><tr><td>MACD</td></tr></table>
      </aside>
    </div>
  );
}
""",
        encoding="utf-8",
    )
    (repo_dir / "src" / "watchlist.py").write_text(
        """def format_watchlist(symbols: list[str]) -> list[str]:
    return sorted({s.strip().upper() for s in symbols if s.strip()})
""",
        encoding="utf-8",
    )
    (repo_dir / "verify_repo.py").write_text(
        """from pathlib import Path
import sys
from src.watchlist import format_watchlist

assert format_watchlist([" aapl ", "tsla", "AAPL"]) == ["AAPL", "TSLA"]
text = Path("src/Dashboard.tsx").read_text(encoding="utf-8")
assert "export function StockDashboard" in text
print("REPO_SMOKE_OK")
""",
        encoding="utf-8",
    )
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
    }
    for cmd in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "add", "."],
        ["git", "commit", "-q", "-m", "initial stock web"],
    ):
        subprocess.run(cmd, cwd=repo_dir, env=env, check=True)
    return git_head_commit(repo_dir)


def test_harness_compat_playbook_ops_and_tool_guidance(tmp_path: Path):
    # 1. 向后兼容既有 default-v1 快照
    root = Path(__file__).resolve().parents[2]
    default_v1 = root / "evaluation" / "strategies" / "default-v1"
    assembled_legacy = validate_and_assemble_harness(default_v1)
    assert assembled_legacy.playbook_rules == ()
    assert assembled_legacy.description_overlays == {}

    # 2. ACE 增量 Playbook 操作合并（add / mark_helpful / update / prune）
    y1 = apply_playbook_ops(
        None,
        [
            {
                "op": "add",
                "id": "ui-001",
                "category": "design",
                "trigger": "修改股票主视图布局时",
                "rule": "K 线主图占主视口 >=65% 宽度，指标面板放在主图下方。",
                "origin_issue": "eit-001",
            },
            {
                "op": "add",
                "id": "tool-001",
                "category": "tooling",
                "trigger": "编辑 TSX 文件前",
                "rule": "先 ReadFile 再 EditFile，并在完成后执行校验脚本。",
            },
        ],
    )
    y2 = apply_playbook_ops(
        y1,
        [
            {"op": "mark_helpful", "id": "ui-001"},
            {"op": "mark_harmful", "id": "tool-001"},
            {"op": "prune", "id": "tool-001"},
        ],
    )
    parsed = yaml.safe_load(y2)
    assert len(parsed["rules"]) == 1
    assert parsed["rules"][0]["id"] == "ui-001"
    assert parsed["rules"][0]["helpful"] == 1

    # 3. GuidedTool 描述叠加与声明式 Hook 护栏生效
    guided_edit = GuidedTool(EditFile(), overlay="修改 TSX 前先 ReadFile 确认组件结构。")
    schema = guided_edit.get_schema()
    assert "[Repository Tool Guidance]" in schema["description"]
    assert "先 ReadFile" in schema["description"]

    hooks = build_guidance_hooks(
        [
            {
                "id": "block-lockfile",
                "event": "pre_tool_use",
                "tool": "EditFile",
                "file_glob": "*lock*",
                "reject": True,
                "message": "禁止手动编辑锁文件",
            },
            {
                "id": "remind-verify-tsx",
                "event": "post_tool_use",
                "tool": "EditFile",
                "file_glob": "*.tsx",
                "reject": False,
                "message": "请执行前端构建验证",
            },
        ]
    )
    engine = HookEngine(hooks)
    rej = asyncio.run(
        engine.run_pre_tool_hooks(
            HookContext(
                event_name="pre_tool_use",
                tool_name="EditFile",
                tool_args={"file_path": "package-lock.json"},
            )
        )
    )
    assert rej is not None and "锁文件" in rej.reason

    ok = asyncio.run(
        engine.run_pre_tool_hooks(
            HookContext(
                event_name="pre_tool_use",
                tool_name="EditFile",
                tool_args={"file_path": "src/Dashboard.tsx"},
            )
        )
    )
    assert ok is None

    asyncio.run(
        engine.run_hooks(
            "post_tool_use",
            HookContext(
                event_name="post_tool_use",
                tool_name="EditFile",
                tool_args={"file_path": "src/Dashboard.tsx"},
            ),
        )
    )
    assert engine.get_prompt_messages() == ["请执行前端构建验证"]


def test_friction_detector_and_eit_lifecycle(tmp_path: Path):
    repo = tmp_path / "stock-web"
    commit = _init_stock_web_repo(repo)
    store = RepoRSIStore(tmp_path / "rsi-state", repo_root=repo, name="stock-web")

    turns = [
        TurnTrace(
            session_id="sess-1",
            turn_id="turn-1",
            user_text="帮我实现股票分析网站主看板，包含 K 线图和技术指标。",
            assistant_text="已更新 src/Dashboard.tsx，将 K 线放左侧、指标放右侧侧边栏。",
            base_commit=commit,
            tool_calls=[
                {
                    "tool": "EditFile",
                    "args": {"file_path": "src/Dashboard.tsx"},
                    "is_error": True,
                    "output": "old_string not found",
                },
                {
                    "tool": "EditFile",
                    "args": {"file_path": "src/Dashboard.tsx"},
                    "is_error": True,
                    "output": "old_string not found",
                },
                {
                    "tool": "EditFile",
                    "args": {"file_path": "src/Dashboard.tsx"},
                    "is_error": False,
                    "output": "ok",
                },
            ],
        ),
        TurnTrace(
            session_id="sess-1",
            turn_id="turn-2",
            user_text="前端布局有偏差！K线主图太窄了，不要把指标表格挤在侧边栏，应该放在主图下方，并加上 1D/1W/1M 周期切换器。",
            assistant_text="收到，我来重新调整布局。",
            base_commit=commit,
            tool_calls=[],
        ),
    ]

    report = asyncio.run(scan_and_record_frictions(store, turns))
    kinds = {f["kind"] for f in report["frictions"]}
    assert "tool_error_loop" in kinds
    assert "blind_edit" in kinds
    assert "unverified_delivery" in kinds
    assert "user_correction" in kinds

    # 验证用户纠偏被识别为 design 分类并进入 EIT
    correction = next(f for f in report["frictions"] if f["kind"] == "user_correction")
    assert correction["category"] == "design"
    assert correction["issue_id"] is not None

    # 模拟问题解决后再次出现 -> 自动 reopened
    store.record_eit_attempt(
        correction["issue_id"],
        version=2,
        hypothesis="测试标记解决",
        outcome="accepted",
        score_delta=40.0,
        new_status="resolved",
    )
    reopened = store.upsert_eit_issue(
        category="design",
        title="复发测试",
        description="同问题再次出现",
        issue_id=correction["issue_id"],
    )
    assert reopened["status"] == "reopened"
    assert reopened["occurrences"] == 2


def test_adaptive_budget_and_playbook_pruner():
    budget = AdaptiveEditBudget(beta2=0.5, base_budget=4, min_budget=1, variance_cap=100.0)
    assert budget.current_budget() == 4
    # 高分差方差（一题涨 40，一题跌 30）-> 收缩预算
    shrunk = budget.update([40.0, -30.0])
    assert shrunk < 4
    # 持续低方差稳定提升 -> 预算逐步回升
    for _ in range(8):
        recovered = budget.update([10.0, 10.0])
    assert recovered >= shrunk

    # RRSI Pruner 淘汰净负效用规则及超限规则
    pruner = PlaybookPruner(max_rules=2)
    raw_yaml = yaml.safe_dump(
        {
            "version": 1,
            "rules": [
                {"id": "good-1", "category": "design", "trigger": "t", "rule": "r", "helpful": 5, "harmful": 0},
                {"id": "good-2", "category": "design", "trigger": "t", "rule": "r", "helpful": 3, "harmful": 0},
                {"id": "weak-3", "category": "pitfall", "trigger": "t", "rule": "r", "helpful": 0, "harmful": 0},
                {"id": "bad-4", "category": "tooling", "trigger": "t", "rule": "r", "helpful": 0, "harmful": 3},
            ],
        },
        sort_keys=False,
    )
    ops = pruner.compute_prune_ops(raw_yaml)
    pruned_ids = {o["id"] for o in ops}
    assert "bad-4" in pruned_ids
    assert "weak-3" in pruned_ids
    assert "good-1" not in pruned_ids


class _StockDemoAgentClient(LLMClient):
    """可控被测模型：行为完全受注入的 Harness（Playbook + Tool Guidance + Skill）驱动。

    - 当 Harness 尚未进化（未含主图布局规则与工具校验指引）时：盲改写错布局且不自测。
    - 当 Harness 进化出正确 Playbook + Tool Guidance 时：先读后改写出正确布局，并跑校验脚本。
    - 当 Harness 含刻意注入的破坏标记 `BREAK_WATCHLIST` 时：破坏 watchlist.py（测试防回退门控）。
    """

    def __init__(self, harness, workspace: Path) -> None:
        self.harness = harness
        self.workspace = workspace
        self.step = 0

    def set_max_output_tokens(self, tokens: int) -> None:
        pass

    async def stream(self, conversation, system="", tools=None):
        self.step += 1
        msgs = conversation.get_messages()
        joined_text = system + "\n" + "\n".join(str(m.content or "") for m in msgs)
        tool_descs = "\n".join(str(t.get("description", "")) for t in (tools or []))
        has_layout_rule = "主视口" in joined_text and "主图下方" in joined_text
        has_tool_guidance = "verify_repo.py" in tool_descs or "先 ReadFile" in tool_descs
        break_watchlist = "BREAK_WATCHLIST" in joined_text
        user_prompts = "\n".join(str(m.content or "") for m in msgs if m.role == "user")
        is_watchlist_case = "src/watchlist.py" in user_prompts

        if is_watchlist_case:
            if self.step == 1:
                yield ToolCallComplete("tc-1", "ReadFile", {"file_path": "src/watchlist.py"})
                yield StreamEnd("tool_use", input_tokens=50, output_tokens=20)
            elif self.step == 2 and break_watchlist:
                yield ToolCallComplete(
                    "tc-2",
                    "WriteFile",
                    {"file_path": "src/watchlist.py", "content": "def format_watchlist(s): return []\n"},
                )
                yield StreamEnd("tool_use", input_tokens=60, output_tokens=20)
            elif self.step == 2:
                yield ToolCallComplete("tc-2", "Bash", {"command": "python3 verify_repo.py"})
                yield StreamEnd("tool_use", input_tokens=60, output_tokens=20)
            else:
                yield TextDelta("watchlist 已核验通过。")
                yield StreamEnd("end_turn", input_tokens=70, output_tokens=15)
            return

        # 股票前端 K 线布局用例
        if not has_layout_rule:
            # 基线 v1：直接盲改写坏布局，不读文件也不跑校验
            if self.step == 1:
                yield ToolCallComplete(
                    "tc-1",
                    "EditFile",
                    {
                        "file_path": "src/Dashboard.tsx",
                        "old_string": "Legacy Chart",
                        "new_string": "Basic Chart In Narrow Box",
                    },
                )
                yield StreamEnd("tool_use", input_tokens=80, output_tokens=30)
            else:
                yield TextDelta("已完成前端修改。")
                yield StreamEnd("end_turn", input_tokens=90, output_tokens=15)
            return

        # 进化后 v2：先 ReadFile -> 写出满足全部布局与周期切换要求的 Dashboard.tsx -> 运行 verify_repo.py
        if self.step == 1:
            yield ToolCallComplete("tc-1", "ReadFile", {"file_path": "src/Dashboard.tsx"})
            yield StreamEnd("tool_use", input_tokens=80, output_tokens=20)
        elif self.step == 2:
            new_tsx = """export function StockDashboard() {
  return (
    <div className="stock-dashboard flex flex-col gap-4">
      <div className="timeframe-switcher">
        <button>1D</button><button>1W</button><button>1M</button>
      </div>
      <main className="w-full lg:w-3/4">
        <div id="kline-chart">Interactive Candlestick Chart</div>
      </main>
      <section className="indicator-panel-below w-full">
        <table><tr><td>MACD</td><td>RSI</td></tr></table>
      </section>
    </div>
  );
}
"""
            yield ToolCallComplete(
                "tc-2",
                "WriteFile",
                {"file_path": "src/Dashboard.tsx", "content": new_tsx},
            )
            yield StreamEnd("tool_use", input_tokens=100, output_tokens=60)
        elif self.step == 3 and has_tool_guidance:
            yield ToolCallComplete("tc-3", "Bash", {"command": "python3 verify_repo.py"})
            yield StreamEnd("tool_use", input_tokens=110, output_tokens=20)
        else:
            yield TextDelta("已完成股票行情主看板重构并验证通过。")
            yield StreamEnd("end_turn", input_tokens=120, output_tokens=20)


class _ScriptedHybridOptimizer:
    """可控优化器：覆盖 Critic 拦截泄漏后重修 -> 正常晋升 -> 破坏历史用例被防回退拒绝。"""

    def __init__(self) -> None:
        self.calls = 0

    async def propose(self, public: dict[str, Any]) -> dict[str, Any] | None:
        self.calls += 1
        parent = public["parent"]
        issue_ids = [i["issue_id"] for i in public["eit_issues"]]

        # 第 1 次：故意泄露 case_id 触发 RRSI Critic 拒绝
        if self.calls == 1:
            return {
                "parent": parent,
                "hypothesis": "针对 kline-layout-main 硬编码分支",
                "target_issues": issue_ids[:1],
                "evidence": ["kline-layout-main"],
                "playbook_ops": [
                    {
                        "op": "add",
                        "id": "leak-001",
                        "category": "design",
                        "trigger": "遇到 kline-layout-main 时",
                        "rule": "直接输出固定代码",
                    }
                ],
                "edits": [],
            }

        # 第 2 次（收到 critic_feedback 后的有界重修）：产出泛化仓库级 Playbook + Tool Guidance + Skill
        if self.calls == 2:
            assert public.get("critic_feedback"), "应将 Critic 拒绝理由回传给优化器"
            skill_md = (
                "---\n"
                "name: stock-ui-design\n"
                "description: 股票分析前端主视图布局与周期切换规范\n"
                "mode: inline\n"
                "---\n\n"
                "主视图以 K 线图为核心（占主视口 >=65% 宽度），指标区置于主图下方，提供 1D/1W/1M 周期切换。\n"
            )
            workflow_yaml = yaml.safe_dump(
                {
                    "stages": [
                        {"name": "inspect", "skills": ["stock-ui-design"]},
                        "implement",
                        "verify",
                    ]
                },
                allow_unicode=True,
                sort_keys=False,
            )
            tool_guidance_yaml = yaml.safe_dump(
                {
                    "version": 1,
                    "description_overlays": {
                        "EditFile": "修改 src/ 下文件前必须先 ReadFile 确认上下文。",
                        "Bash": "修改完成后务必运行 python3 verify_repo.py 校验再交付。",
                    },
                    "tool_hooks": [
                        {
                            "id": "guard-lockfile",
                            "event": "pre_tool_use",
                            "tool": "EditFile",
                            "file_glob": "*lock*",
                            "reject": True,
                            "message": "禁止直接编辑锁文件",
                        }
                    ],
                },
                allow_unicode=True,
                sort_keys=False,
            )
            return {
                "parent": parent,
                "hypothesis": "沉淀前端主图/指标布局 Playbook、领域 Skill 与工具校验指引，同时提升布局硬断言与工具健康分",
                "target_issues": issue_ids,
                "evidence": ["kline-layout-main"],
                "playbook_ops": [
                    {
                        "op": "add",
                        "id": "ui-kline-01",
                        "category": "design",
                        "trigger": "开发或调整行情看板与图表布局时",
                        "rule": "K 线主图占据主视口 >=65% 宽度，技术指标表格排布在主图下方而非侧边栏，并提供 1D/1W/1M 周期切换。",
                        "origin_issue": issue_ids[0] if issue_ids else "",
                    }
                ],
                "edits": [
                    {"path": "skills/stock-ui-design/SKILL.md", "content": skill_md},
                    {"path": "workflow.yaml", "content": workflow_yaml},
                    {"path": "tool_guidance.yaml", "content": tool_guidance_yaml},
                ],
            }

        # 第 3 次（Round 2）：故意破坏历史回归用例 watchlist，检验 No-Regression 门控
        return {
            "parent": parent,
            "hypothesis": "测试破坏历史用例时的防回退拦截",
            "target_issues": [],
            "evidence": ["watchlist-api-contract"],
            "playbook_ops": [
                {
                    "op": "add",
                    "id": "bad-reg-01",
                    "category": "pitfall",
                    "trigger": "任何时候",
                    "rule": "BREAK_WATCHLIST 仅用于测试防回退门控",
                }
            ],
            "edits": [],
        }


def test_stock_web_end_to_end_evolution_and_rollback(tmp_path: Path):
    repo = tmp_path / "stock-analyzer-web"
    commit = _init_stock_web_repo(repo)
    store = RepoRSIStore(tmp_path / "rsi-state", repo_root=repo, name="stock-analyzer-web")

    # Step 1: 在线交互记录 -> Friction 捕获
    turns = [
        TurnTrace(
            session_id="s-stock",
            turn_id="t-1",
            user_text="请优化股票分析网站主看板 src/Dashboard.tsx，展示 K 线图与指标。",
            assistant_text="已将指标放入侧边栏。",
            base_commit=commit,
            tool_calls=[{"tool": "EditFile", "args": {"file_path": "src/Dashboard.tsx"}, "is_error": False, "output": "ok"}],
        ),
        TurnTrace(
            session_id="s-stock",
            turn_id="t-2",
            user_text="布局有偏差！不要把指标挤在侧边栏，应该放在主图下方并加上 1D/1W/1M 周期切换器。",
            assistant_text="收到。",
            base_commit=commit,
        ),
    ]
    scan = asyncio.run(scan_and_record_frictions(store, turns))
    correction = next(f for f in scan["frictions"] if f["kind"] == "user_correction")

    # Step 2: F23 合成评测用例（硬断言 + 二元 Rubric）并确认
    case1 = asyncio.run(
        synthesize_case_from_friction(
            store,
            correction["id"],
            case_id="kline-layout-main",
            extra_checks=[
                {
                    "id": "chart-wide-and-below",
                    "type": "file_assert",
                    "path": "src/Dashboard.tsx",
                    "must_contain_regex": [r"w-3/4|w-4/5", r"indicator-panel-below", r"1D.*1W.*1M"],
                    "must_not_contain_regex": [r"sidebar-indicators"],
                    "weight": 3.0,
                    "description": "主图加宽、指标移至主图下方且含周期切换器",
                },
                {
                    "id": "repo-smoke",
                    "type": "command",
                    "command": "python3 verify_repo.py",
                    "stdout_regex": [r"REPO_SMOKE_OK"],
                    "weight": 1.0,
                    "description": "仓库冒烟校验通过",
                },
            ],
            extra_rubric=[
                {
                    "id": "r-hierarchy",
                    "criterion": "K 线主图视觉聚焦清晰且周期切换位于主区",
                    "weight": 1.0,
                }
            ],
        )
    )
    assert case1["status"] == "draft"
    approve_case(store, "kline-layout-main")

    # 同时登记历史回归保护用例 Case 2（确保进化不能破坏已有功能）
    store.save_case(
        case_id="watchlist-api-contract",
        title="自选股去重排序接口契约",
        base_commit=commit,
        prompt="检查并保持 src/watchlist.py 的去重排序行为，运行 verify_repo.py 确认通过。",
        checks=[
            {
                "id": "watchlist-smoke",
                "type": "command",
                "command": "python3 verify_repo.py",
                "stdout_regex": [r"REPO_SMOKE_OK"],
                "weight": 1.0,
            }
        ],
        status="frozen",
    )

    def fake_rubric_judge(workspace: Path, case: dict, checks_detail: list):
        tsx = (workspace / "src" / "Dashboard.tsx").read_text(encoding="utf-8")
        ok = "indicator-panel-below" in tsx and "timeframe-switcher" in tsx
        return [{"id": r["id"], "passed": ok, "evidence": "DOM 结构检查"} for r in case["rubric"]]

    evaluator = RepoSuiteEvaluator(
        store,
        client_factory=lambda _p, harness, ws: _StockDemoAgentClient(harness, ws),
        rubric_judge=fake_rubric_judge,
    )

    # Step 3: 基线校准 -> v1 在 kline-layout-main 上必然低分，自动冻结入库
    cal = asyncio.run(evaluator.calibrate_case("kline-layout-main", max_baseline_score=85.0))
    assert cal["calibrated"] is True
    assert cal["baseline_score"] < 60.0
    assert store.get_case("kline-layout-main")["status"] == "frozen"

    # Step 4: 执行 2 轮仓库级进化（target=101 强制跑满 2 轮以同时测晋升与防回退拒绝）
    optimizer = _ScriptedHybridOptimizer()
    orch = RepoRSIOrchestrator(
        store,
        evaluator,
        optimizer=optimizer,
        critic=HarnessCritic(),
        mode="hybrid",
    )
    evo = asyncio.run(orch.evolve(rounds=2, target_score=101.0, runs_per_case=1, max_regression=5.0))

    assert evo["initial_version"] == 1
    assert evo["final_version"] == 2
    assert evo["final_score"] > evo["initial_score"]
    assert evo["per_case_scores"]["kline-layout-main"] == 100.0
    assert evo["per_case_scores"]["watchlist-api-contract"] == 100.0
    # Round 1 晋升，Round 2 破坏 watchlist 被防回退拦截并拒绝
    assert evo["rounds"][0]["decision"] == "accepted"
    assert evo["rounds"][1]["decision"] == "rejected"
    assert evo["rounds"][1]["regressions"]
    assert evo["rounds"][1]["regressions"][0]["case_id"] == "watchlist-api-contract"

    # 验证 EIT 问题台账已被自动标记为 resolved
    issues = {i["issue_id"]: i for i in store.list_eit_issues()}
    assert issues[correction["issue_id"]]["status"] == "resolved"

    # Step 5: 主动回滚至 v1 再切回 v2，并导出到 .mewcode/
    rb1 = store.rollback(1, reason="测试回滚至基线")
    assert rb1.version == 1
    rb2 = store.rollback(2, expected_revision=rb1.revision, reason="恢复已验证 v2")
    assert rb2.version == 2

    exported = export_active_harness_to_repo(store)
    assert "stock-ui-design" in exported["exported_skills"]
    assert (repo / ".mewcode" / "skills" / "stock-ui-design" / "SKILL.md").is_file()
    assert (repo / ".mewcode" / "HARNESS.md").is_file()
