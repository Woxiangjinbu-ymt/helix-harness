"""融合型仓库 RSI 优化器（RepoHybridOptimizer）：代码化整合 Penguin 四大核心算法机制。

整合来源：
1. ACE (ace-rsi-toolkit)        —— Reflector -> Curator 增量 Playbook 操作（add/update/mark_helpful/mark_harmful/prune）
2. SkillAdam (skilladam-rsi)    —— Evolving Issue Tracker (EIT) + 二阶方差自适应编辑预算
3. RRSI (rrsi-rsi-toolkit)      —— 前置 Leakage/Gaming Critic 门控 + 周期性 Pruner 防膨胀
4. AWM (awm-rsi-toolkit)        —— 成功轨迹蒸馏进 workflow.yaml / tool_guidance.yaml
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import math
import re
from statistics import pvariance
from typing import Any

import yaml

from mewcode.client import create_client
from mewcode.conversation import ConversationManager
from mewcode.tools.base import TextDelta, ToolCallComplete, ToolCallStart
from ..default_optimizer import Edit, InvalidProposal
from .harness import (
    MAX_SINGLE_FILE_BYTES,
    STRATEGY_PATH_RE,
    apply_playbook_ops,
)


@dataclass
class AdaptiveEditBudget:
    """移植自 Penguin SkillAdam 的方差自适应编辑预算（二阶动量近似）。"""

    beta2: float = 0.9
    base_budget: int = 4
    min_budget: int = 1
    variance_cap: float = 200.0
    running_variance: float = 0.0

    def current_budget(self) -> int:
        if self.running_variance <= 0.0:
            return self.base_budget
        ratio = min(1.0, self.running_variance / max(1e-6, self.variance_cap))
        scaled = self.base_budget * (1.0 - 0.75 * math.sqrt(ratio))
        return max(self.min_budget, min(self.base_budget, int(round(scaled))))

    def update(self, per_case_deltas: list[float]) -> int:
        if len(per_case_deltas) >= 2:
            var = float(pvariance(per_case_deltas))
        elif len(per_case_deltas) == 1:
            var = 0.0
        else:
            return self.current_budget()
        self.running_variance = self.beta2 * self.running_variance + (1.0 - self.beta2) * var
        return self.current_budget()


# 反作弊 / 反评测泄漏模式（借鉴 Penguin RRSI Critic 宪法）
_GAMING_PATTERNS = (
    re.compile(r"\beval_cases\b|\brepo_rsi\.sqlite\b|\brubric_judge\b|\bchecks_json\b", re.IGNORECASE),
    re.compile(r"硬编码答案|绕过测试|直接返回预期输出|跳过\s*check|bypass\s+verifier", re.IGNORECASE),
)


@dataclass(frozen=True)
class CriticVerdict:
    accepted: bool
    reasons: tuple[str, ...] = ()
    risk_notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": "accept" if self.accepted else "reject",
            "reasons": list(self.reasons),
            "risk_notes": list(self.risk_notes),
        }


SEMANTIC_CRITIC_SYSTEM = """你是仓库级自进化 Harness 的前置审查器（RRSI Critic）。
请注意：这是「单仓库（Per-Repo）自进化」，因此**允许**仓库级设计规范、项目架构约定、组件布局规范、项目构建校验命令；
但必须**坚决拒绝**以下 5 类退化或作弊改动：
1. CASE_MEMORIZATION：硬编码具体评测用例 ID、针对单个题号写 if 分支、或把单题答案写成查表。
2. GRADER_GAMING：试图读取/欺骗/绕过评测脚本、Playwright 校验或 Rubric 裁判。
3. SAFETY_REGRESSION：删除或削弱安全边界、权限检查、验证步骤或错误处理。
4. UNBOUNDED_WORK：引入无止境重试、无限循环或要求等待永远不会到来的人工确认。
5. BLOAT_OR_NOOP：重复已有规则、空洞废话或无实际行为影响的无效改动。

返回严格 JSON（无 Markdown 围栏）：
{"verdict": "accept" | "reject", "reasons": ["..."], "risk_notes": ["..."]}"""


class HarnessCritic:
    """前置静态 + 可选语义审查器，拦截过拟合、作弊与膨胀提案，避免浪费评测成本。"""

    def __init__(self, critic_provider: Any | None = None) -> None:
        self.critic_provider = critic_provider

    def screen_static(
        self,
        edits: tuple[Edit, ...] | list[Edit],
        *,
        case_ids: set[str],
        edit_budget: int,
        mode: str = "hybrid",
    ) -> CriticVerdict:
        reasons: list[str] = []
        risk_notes: list[str] = []

        if not edits:
            reasons.append("DEGENERATE: 提案未包含任何有效文件修改")
            return CriticVerdict(False, tuple(reasons))

        if len(edits) > max(1, edit_budget):
            reasons.append(
                f"BUDGET_EXCEEDED: 本轮改动文件数 {len(edits)} 超出自适应编辑预算 {edit_budget}"
            )

        seen_paths: set[str] = set()
        for edit in edits:
            path, content = edit.path, edit.content
            if path in seen_paths or not STRATEGY_PATH_RE.fullmatch(path):
                reasons.append(f"CONTRACT_BREAK: 重复路径或超出策略白名单：{path}")
                continue
            seen_paths.add(path)
            if mode == "ace" and path != "playbook.yaml":
                reasons.append(f"MODE_SCOPE: ACE 模式仅允许更新 playbook.yaml，禁止修改 {path}")
            if content is None:
                if not path.startswith("skills/"):
                    reasons.append(f"CONTRACT_BREAK: 不能删除核心策略文件 {path}")
                continue
            if not content.strip() or "\x00" in content or len(content.encode("utf-8")) > MAX_SINGLE_FILE_BYTES:
                reasons.append(f"CONTRACT_BREAK: 文件 {path} 内容为空、含非法字符或超过大小预算")
                continue

            # 1. 禁止按评测 case_id 写特判分支
            folded = re.sub(r"[\W_]", "", (path + "\n" + content).casefold())
            for cid in case_ids:
                norm_cid = re.sub(r"[\W_]", "", cid.casefold())
                if len(norm_cid) >= 5 and norm_cid in folded:
                    reasons.append(f"CASE_MEMORIZATION: 文件 {path} 直接引用了评测用例标识 {cid}")

            # 2. 禁止评测/裁判作弊关键字
            for pat in _GAMING_PATTERNS:
                if pat.search(content):
                    reasons.append(f"GRADER_GAMING: 文件 {path} 命中禁止的评测作弊模式 {pat.pattern}")

        return CriticVerdict(len(reasons) == 0, tuple(reasons), tuple(risk_notes))

    async def screen(
        self,
        edits: tuple[Edit, ...] | list[Edit],
        *,
        hypothesis: str,
        case_ids: set[str],
        edit_budget: int,
        mode: str = "hybrid",
    ) -> CriticVerdict:
        static_res = self.screen_static(
            edits, case_ids=case_ids, edit_budget=edit_budget, mode=mode
        )
        if not static_res.accepted or self.critic_provider is None:
            return static_res

        payload = {
            "hypothesis": hypothesis,
            "edits": [{"path": e.path, "content": e.content} for e in edits],
        }
        conv = ConversationManager()
        conv.add_user_message(json.dumps(payload, ensure_ascii=False))
        chunks: list[str] = []
        try:
            async with asyncio.timeout(45):
                async for ev in create_client(self.critic_provider).stream(
                    conv, system=SEMANTIC_CRITIC_SYSTEM, tools=[]
                ):
                    if isinstance(ev, TextDelta):
                        chunks.append(ev.text)
            raw = "".join(chunks).strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```[a-zA-Z]*\n|\n```$", "", raw).strip()
            data = json.loads(raw)
            accepted = data.get("verdict") == "accept"
            return CriticVerdict(
                accepted=accepted,
                reasons=tuple(str(r) for r in (data.get("reasons") or [])),
                risk_notes=tuple(str(r) for r in (data.get("risk_notes") or [])),
            )
        except Exception as exc:
            # 语义 Critic 不可用时降级保留静态审查结论并记录 risk note
            return CriticVerdict(
                True,
                (),
                (f"semantic_critic_skipped: {exc}",),
            )


class PlaybookPruner:
    """借鉴 RRSI Pruner：定期清理净效用为负或冗余超限的 Playbook 规则，防上下文膨胀。"""

    def __init__(self, max_rules: int = 25) -> None:
        self.max_rules = max_rules

    def compute_prune_ops(self, playbook_yaml: str | None) -> list[dict[str, Any]]:
        if not playbook_yaml:
            return []
        doc = yaml.safe_load(playbook_yaml)
        if not isinstance(doc, dict) or not isinstance(doc.get("rules"), list):
            return []
        rules = doc["rules"]
        ops: list[dict[str, Any]] = []
        surviving: list[dict[str, Any]] = []
        for r in rules:
            helpful = int(r.get("helpful", 0))
            harmful = int(r.get("harmful", 0))
            if harmful >= 2 and harmful > helpful:
                ops.append({"op": "prune", "id": r["id"]})
            else:
                surviving.append(r)
        if len(surviving) > self.max_rules:
            # 按净效用升序淘汰尾部低价值规则
            ranked = sorted(
                surviving,
                key=lambda r: (int(r.get("helpful", 0)) - int(r.get("harmful", 0)), int(r.get("helpful", 0))),
            )
            overflow = len(surviving) - self.max_rules
            for r in ranked[:overflow]:
                ops.append({"op": "prune", "id": r["id"]})
        return ops


HYBRID_OPTIMIZER_SYSTEM = """你是仓库级自进化引擎的融合优化器（整合 ACE + SkillAdam + RRSI + AWM）。
你的目标是在「冻结模型不变」的前提下，仅通过改进本仓库的 Harness（playbook.yaml、tool_guidance.yaml、workflow.yaml、skills/<name>/SKILL.md、prompt.md），
系统性修复本仓库评测回归集与 EIT 问题台账中暴露的失败、前端/业务偏差与工具调用摩擦，同时**严禁破坏历史已通过的用例**。

工作纪律：
1. 优先对 `playbook.yaml` 做**结构化增量修改**（通过 `playbook_ops`），避免整篇重写丢失既有有效经验。
2. 当证据显示工具调用报错、未先读后改或未自测交付时，通过 `edits` 更新 `tool_guidance.yaml`（添加工具描述叠加 `description_overlays` 或声明式 `tool_hooks` 护栏）或 `workflow.yaml`（AWM 工作流阶段）。
3. 当某个领域（如前端股票行情布局/图表规范）需要成体系 SOP 时，在 `skills/<name>/SKILL.md` 新增或更新 inline Skill，并在 `workflow.yaml` 中引用。
4. 严格遵守 `edit_budget` 上限，禁止写入具体评测 case_id 或测试断言硬编码。
5. 若此前某方向被拒（见 history 与 critic_feedback），不要原样重复。

返回单一严格 JSON 对象（禁止 Markdown 围栏），若确实没有可改进项返回 null：
{
  "parent": <当前整数版本号>,
  "hypothesis": "可检验的根因诊断与预期收益（<=1500字）",
  "target_issues": ["eit-001"],
  "evidence": ["<关联的 case_id>"],
  "playbook_ops": [
    {"op": "add", "id": "ui-001", "category": "design", "trigger": "...", "rule": "...", "origin_issue": "eit-001"},
    {"op": "mark_helpful", "id": "..."},
    {"op": "mark_harmful", "id": "..."},
    {"op": "prune", "id": "..."}
  ],
  "edits": [
    {"path": "tool_guidance.yaml", "content": "<完整 YAML 正文>"}
  ]
}"""


def materialize_proposal_edits(
    current_files: dict[str, str], proposal: dict[str, Any]
) -> tuple[Edit, ...]:
    """将提案中的 playbook_ops 与 edits 确定性合并为标准 Edit 列表。"""
    if not isinstance(proposal, dict):
        raise InvalidProposal("提案必须为 JSON 对象")
    if not isinstance(proposal.get("hypothesis"), str) or not proposal["hypothesis"].strip():
        raise InvalidProposal("提案缺少有效的 hypothesis")

    edits_by_path: dict[str, str | None] = {}
    raw_ops = proposal.get("playbook_ops") or []
    if raw_ops:
        if not isinstance(raw_ops, list):
            raise InvalidProposal("playbook_ops 必须为列表")
        merged_yaml = apply_playbook_ops(current_files.get("playbook.yaml"), raw_ops)
        edits_by_path["playbook.yaml"] = merged_yaml

    for item in proposal.get("edits") or []:
        if not isinstance(item, dict) or set(item) != {"path", "content"}:
            raise InvalidProposal("edits 条目必须仅包含 path 与 content")
        path = item["path"]
        content = item["content"]
        if not isinstance(path, str) or not STRATEGY_PATH_RE.fullmatch(path):
            raise InvalidProposal(f"策略路径非法：{path!r}")
        if content is not None and not isinstance(content, str):
            raise InvalidProposal(f"文件 {path} 的 content 必须为字符串或 null")
        edits_by_path[path] = content

    if not edits_by_path:
        raise InvalidProposal("提案未生成任何策略编辑")

    return tuple(Edit(path=p, content=c) for p, c in edits_by_path.items())


class RepoHybridOptimizer:
    """实现 StrategyOptimizer 协议的仓库级融合优化器（支持 hybrid / ace / skilladam 模式）。"""

    def __init__(self, provider: Any, *, mode: str = "hybrid") -> None:
        if mode not in ("hybrid", "ace", "skilladam"):
            raise ValueError(f"不支持的优化模式：{mode!r}")
        self.provider = provider
        self.mode = mode

    async def propose(self, public: dict[str, Any]) -> dict[str, Any] | None:
        mode_note = {
            "hybrid": "当前模式：hybrid（可综合使用 playbook_ops、tool_guidance.yaml、workflow.yaml、skills、prompt.md）。",
            "ace": "当前模式：ace（仅允许使用 playbook_ops 增量维护 playbook.yaml，edits 必须为空列表 []）。",
            "skilladam": "当前模式：skilladam（基于 EIT 问题台账与自适应预算，重点演进 skills/<name>/SKILL.md 与 prompt.md）。",
        }[self.mode]
        system = f"{HYBRID_OPTIMIZER_SYSTEM}\n\n{mode_note}"
        conv = ConversationManager()
        conv.add_user_message(json.dumps(public, ensure_ascii=False))
        chunks: list[str] = []
        size = 0
        async with asyncio.timeout(90):
            async for ev in create_client(self.provider).stream(
                conv, system=system, tools=[]
            ):
                if isinstance(ev, (ToolCallStart, ToolCallComplete)):
                    raise InvalidProposal("提案模型不能调用工具")
                if isinstance(ev, TextDelta):
                    size += len(ev.text.encode("utf-8"))
                    if size > 96 * 1024:
                        raise InvalidProposal("提案响应超出大小预算")
                    chunks.append(ev.text)
        raw = "".join(chunks).strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z]*\n|\n```$", "", raw).strip()
        if raw == "null" or not raw:
            return None
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            raise InvalidProposal(f"提案模型未返回合法 JSON：{exc}") from exc
        if parsed is None:
            return None
        if not isinstance(parsed, dict):
            raise InvalidProposal("提案必须为 JSON 对象或 null")
        return parsed
