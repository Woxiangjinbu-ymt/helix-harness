"""仓库级 Harness 策略包校验、装配与 Tools Call 引导层。

在既有 strategy（prompt.md / workflow.yaml / skills/<name>/SKILL.md）基础上，
增量扩展两个可选文件，完全向后兼容既有 default-v1 快照：
1. playbook.yaml       —— 借鉴 Penguin ACE 的条目化增量规则手册（带 helpful/harmful 计数）
2. tool_guidance.yaml  —— 面向 Tools Call 进化的工具描述叠加层与声明式安全护栏 Hook
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any

import yaml

from mewcode.hooks.conditions import Condition, ConditionGroup
from mewcode.hooks.models import Action, Hook
from mewcode.skills.parser import parse_frontmatter, parse_skill_file
from mewcode.tools.base import Tool, ToolResult
from ..fixtures import regular_files, tree_digest

# 允许演化的策略路径正则
STRATEGY_PATH_RE = re.compile(
    r"^(?:prompt\.md|workflow\.yaml|playbook\.yaml|tool_guidance\.yaml"
    r"|skills/[a-z][a-z0-9-]{0,63}/SKILL\.md)$"
)

ALLOWED_TOOLS = frozenset(
    {"ReadFile", "EditFile", "WriteFile", "Glob", "Grep", "Bash", "LoadSkill"}
)
ALLOWED_PLAYBOOK_CATEGORIES = frozenset(
    {"design", "tooling", "architecture", "verification", "pitfall"}
)
ALLOWED_HOOK_EVENTS = frozenset({"pre_tool_use", "post_tool_use"})

MAX_SINGLE_FILE_BYTES = 16 * 1024
MAX_TOTAL_SNAPSHOT_BYTES = 128 * 1024
MAX_PLAYBOOK_RULES = 40
MAX_TOOL_HOOKS = 16


@dataclass(frozen=True)
class PlaybookRule:
    """ACE 风格的条目化仓库规则。"""

    id: str
    category: str
    trigger: str
    rule: str
    helpful: int = 0
    harmful: int = 0
    origin_issue: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "category": self.category,
            "trigger": self.trigger,
            "rule": self.rule,
            "helpful": self.helpful,
            "harmful": self.harmful,
        }
        if self.origin_issue:
            data["origin_issue"] = self.origin_issue
        return data


@dataclass(frozen=True)
class RepoHarnessInputs:
    """装配完成的仓库级 Harness 输入，可直接注入 Coding 或隔离评测运行时。"""

    instructions: str
    skills: dict[str, dict[str, str]]
    playbook_rules: tuple[PlaybookRule, ...] = ()
    description_overlays: dict[str, str] = field(default_factory=dict)
    tool_hooks: tuple[dict[str, Any], ...] = ()
    digest: str = ""
    files: dict[str, str] = field(default_factory=dict)


def _validate_playbook(raw: Any) -> tuple[PlaybookRule, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise ValueError("playbook.yaml 必须为映射对象")
    allowed_top = {"version", "rules"}
    if set(raw) - allowed_top or raw.get("version", 1) != 1:
        raise ValueError("playbook.yaml 仅支持 version: 1 与 rules 字段")
    entries = raw.get("rules", [])
    if not isinstance(entries, list) or len(entries) > MAX_PLAYBOOK_RULES:
        raise ValueError(f"playbook.yaml 规则数无效或超过上限 {MAX_PLAYBOOK_RULES}")
    seen_ids: set[str] = set()
    rules: list[PlaybookRule] = []
    for item in entries:
        if not isinstance(item, dict):
            raise ValueError("playbook 规则条目必须为映射")
        extra = set(item) - {
            "id",
            "category",
            "trigger",
            "rule",
            "helpful",
            "harmful",
            "origin_issue",
        }
        if extra:
            raise ValueError(f"playbook 规则包含未知字段：{sorted(extra)}")
        rid = item.get("id")
        cat = item.get("category")
        trigger = item.get("trigger")
        rule = item.get("rule")
        helpful = item.get("helpful", 0)
        harmful = item.get("harmful", 0)
        origin = item.get("origin_issue", "")
        if not isinstance(rid, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{1,47}", rid):
            raise ValueError(f"playbook 规则 ID 无效：{rid!r}")
        if rid in seen_ids:
            raise ValueError(f"playbook 规则 ID 重复：{rid}")
        if cat not in ALLOWED_PLAYBOOK_CATEGORIES:
            raise ValueError(f"playbook 规则分类无效：{cat!r}")
        if not isinstance(trigger, str) or not 0 < len(trigger.strip()) <= 300:
            raise ValueError(f"playbook 规则 {rid} 的 trigger 无效")
        if not isinstance(rule, str) or not 0 < len(rule.strip()) <= 800:
            raise ValueError(f"playbook 规则 {rid} 的 rule 无效")
        if (
            type(helpful) is not int
            or helpful < 0
            or type(harmful) is not int
            or harmful < 0
        ):
            raise ValueError(f"playbook 规则 {rid} 的计数器须为非负整数")
        if not isinstance(origin, str) or len(origin) > 64:
            raise ValueError(f"playbook 规则 {rid} 的 origin_issue 无效")
        seen_ids.add(rid)
        rules.append(
            PlaybookRule(
                id=rid,
                category=cat,
                trigger=trigger.strip(),
                rule=rule.strip(),
                helpful=helpful,
                harmful=harmful,
                origin_issue=origin.strip(),
            )
        )
    return tuple(rules)


def _validate_tool_guidance(
    raw: Any,
) -> tuple[dict[str, str], tuple[dict[str, Any], ...]]:
    if raw is None:
        return {}, ()
    if not isinstance(raw, dict):
        raise ValueError("tool_guidance.yaml 必须为映射对象")
    allowed_top = {"version", "description_overlays", "tool_hooks"}
    if set(raw) - allowed_top or raw.get("version", 1) != 1:
        raise ValueError(
            "tool_guidance.yaml 仅支持 version: 1、description_overlays、tool_hooks"
        )
    raw_overlays = raw.get("description_overlays", {}) or {}
    if not isinstance(raw_overlays, dict):
        raise ValueError("description_overlays 必须为映射")
    overlays: dict[str, str] = {}
    for tool_name, text in raw_overlays.items():
        if tool_name not in ALLOWED_TOOLS:
            raise ValueError(f"description_overlays 不支持的工具名：{tool_name!r}")
        if not isinstance(text, str) or not 0 < len(text.strip()) <= 1000:
            raise ValueError(f"工具 {tool_name} 的描述叠加内容无效")
        overlays[tool_name] = text.strip()

    raw_hooks = raw.get("tool_hooks", []) or []
    if not isinstance(raw_hooks, list) or len(raw_hooks) > MAX_TOOL_HOOKS:
        raise ValueError(f"tool_hooks 无效或超过上限 {MAX_TOOL_HOOKS}")
    seen_ids: set[str] = set()
    hooks: list[dict[str, Any]] = []
    for item in raw_hooks:
        if not isinstance(item, dict):
            raise ValueError("tool_hooks 条目必须为映射")
        extra = set(item) - {
            "id",
            "event",
            "tool",
            "file_glob",
            "command_regex",
            "reject",
            "once",
            "message",
            "prompt",
        }
        if extra:
            raise ValueError(f"tool_hooks 包含未知字段：{sorted(extra)}")
        hid = item.get("id")
        event = item.get("event")
        tool_name = item.get("tool")
        if not isinstance(hid, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{1,47}", hid):
            raise ValueError(f"tool_hooks ID 无效：{hid!r}")
        if hid in seen_ids:
            raise ValueError(f"tool_hooks ID 重复：{hid}")
        if event not in ALLOWED_HOOK_EVENTS:
            raise ValueError(f"tool_hooks {hid} 的 event 无效：{event!r}")
        if tool_name not in ALLOWED_TOOLS:
            raise ValueError(f"tool_hooks {hid} 的 tool 无效：{tool_name!r}")
        reject = bool(item.get("reject", False))
        if reject and event != "pre_tool_use":
            raise ValueError(f"tool_hooks {hid}: reject 仅允许用于 pre_tool_use")
        msg = (item.get("message") or item.get("prompt") or "").strip()
        if not msg or len(msg) > 600:
            raise ValueError(f"tool_hooks {hid} 需要有效的 message/prompt 文本")
        file_glob = item.get("file_glob")
        if file_glob is not None and (
            not isinstance(file_glob, str) or not 0 < len(file_glob) <= 200
        ):
            raise ValueError(f"tool_hooks {hid} 的 file_glob 无效")
        cmd_regex = item.get("command_regex")
        if cmd_regex is not None:
            if not isinstance(cmd_regex, str) or not 0 < len(cmd_regex) <= 200:
                raise ValueError(f"tool_hooks {hid} 的 command_regex 无效")
            try:
                re.compile(cmd_regex)
            except re.error as exc:
                raise ValueError(f"tool_hooks {hid} 的正则无效：{exc}") from exc
        seen_ids.add(hid)
        entry: dict[str, Any] = {
            "id": hid,
            "event": event,
            "tool": tool_name,
            "reject": reject,
            "once": bool(item.get("once", False)),
            "message": msg,
        }
        if file_glob is not None:
            entry["file_glob"] = file_glob
        if cmd_regex is not None:
            entry["command_regex"] = cmd_regex
        hooks.append(entry)
    return overlays, tuple(hooks)


def render_playbook_section(rules: tuple[PlaybookRule, ...]) -> str:
    """将条目化规则手册渲染为结构化上下文片段，按净效用降序排布。"""
    if not rules:
        return ""
    ordered = sorted(
        rules,
        key=lambda r: (r.harmful - r.helpful, r.category, r.id),
    )
    lines = [
        "## Repository Playbook",
        "以下规则来自本仓库历史交互与客观评测验证。触发条件匹配时务必遵守：",
    ]
    for r in ordered:
        score_tag = f"+{r.helpful}/-{r.harmful}" if (r.helpful or r.harmful) else "new"
        lines.append(
            f"- `[{r.id} | {r.category} | {score_tag}]` **触发条件**：{r.trigger} —— **规范**：{r.rule}"
        )
    return "\n".join(lines)


def validate_and_assemble_harness(
    folder: Path, expected: str | None = None
) -> RepoHarnessInputs:
    """校验并装配仓库级 Harness 快照，完全兼容既有 default-v1 格式。"""
    actual_digest = tree_digest(folder)
    if expected is not None and actual_digest != expected:
        raise ValueError("策略快照摘要不匹配")

    files_list = list(regular_files(folder))
    total_bytes = sum(p.stat().st_size for p in files_list)
    if total_bytes > MAX_TOTAL_SNAPSHOT_BYTES:
        raise ValueError(f"策略快照总大小超过上限 {MAX_TOTAL_SNAPSHOT_BYTES} 字节")

    skills: dict[str, dict[str, str]] = {}
    files_map: dict[str, str] = {}
    for path in files_list:
        rel_str = path.relative_to(folder).as_posix()
        if not STRATEGY_PATH_RE.fullmatch(rel_str):
            raise ValueError(f"策略包含未授权文件：{rel_str}")
        raw_bytes = path.read_bytes()
        if len(raw_bytes) > MAX_SINGLE_FILE_BYTES or b"\x00" in raw_bytes:
            raise ValueError(f"策略文件过大或含空字节：{rel_str}")
        text = raw_bytes.decode("utf-8")
        files_map[rel_str] = text
        if rel_str.startswith("skills/"):
            rel = path.relative_to(folder)
            meta, _ = parse_frontmatter(text)
            skill = parse_skill_file(path)
            if (
                set(meta) - {"name", "description", "mode", "version"}
                or skill.mode != "inline"
                or skill.name != rel.parent.name
                or not skill.prompt_body.strip()
                or not skill.description.strip()
            ):
                raise ValueError(f"策略只接受带描述的同名 inline Skill：{rel_str}")
            skills[skill.name] = {
                "name": skill.name,
                "description": skill.description.strip(),
                "prompt_body": skill.prompt_body.strip(),
            }

    if "prompt.md" not in files_map or "workflow.yaml" not in files_map:
        raise ValueError("策略缺少必需文件 prompt.md 或 workflow.yaml")

    prompt = files_map["prompt.md"].strip()
    workflow = yaml.safe_load(files_map["workflow.yaml"])
    if not prompt or not isinstance(workflow, dict) or set(workflow) != {"stages"}:
        raise ValueError("行为 Prompt 或 Workflow 无效")
    stages = workflow["stages"]
    if not isinstance(stages, list) or not stages:
        raise ValueError("Workflow 至少需要一个阶段")

    sections = [prompt, "## Workflow"]
    for index, stage in enumerate(stages, 1):
        if isinstance(stage, str):
            stage = {"name": stage}
        if not isinstance(stage, dict) or set(stage) - {"name", "skills"}:
            raise ValueError("Workflow 阶段只接受 name 和 skills")
        name = stage.get("name")
        names = stage.get("skills", [])
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", name):
            raise ValueError(f"Workflow 阶段名称无效：{name!r}")
        if not isinstance(names, list) or not all(
            isinstance(n, str) and n in skills for n in names
        ):
            raise ValueError("Workflow 引用了未知 Skill")
        sections.append(f"{index}. {name}")
        for skill_name in names:
            sections.append(f"### {skill_name}\n{skills[skill_name]['prompt_body']}")

    playbook_rules: tuple[PlaybookRule, ...] = ()
    if "playbook.yaml" in files_map:
        playbook_rules = _validate_playbook(yaml.safe_load(files_map["playbook.yaml"]))
        rendered = render_playbook_section(playbook_rules)
        if rendered:
            sections.append(rendered)

    overlays: dict[str, str] = {}
    tool_hooks: tuple[dict[str, Any], ...] = ()
    if "tool_guidance.yaml" in files_map:
        overlays, tool_hooks = _validate_tool_guidance(
            yaml.safe_load(files_map["tool_guidance.yaml"])
        )

    if tree_digest(folder) != actual_digest:
        raise ValueError("策略在装配期间发生变化")

    return RepoHarnessInputs(
        instructions="\n\n".join(sections),
        skills=skills,
        playbook_rules=playbook_rules,
        description_overlays=overlays,
        tool_hooks=tool_hooks,
        digest=actual_digest,
        files=files_map,
    )


def apply_playbook_ops(
    existing_yaml: str | None, ops: list[dict[str, Any]]
) -> str:
    """确定性合并 ACE 增量 Playbook 操作，避免模型手写整份 YAML 造成规则丢失。

    支持操作 op：
    - add: 新增规则（id/category/trigger/rule/origin_issue）
    - update: 按 id 更新 trigger/rule/category
    - mark_helpful / mark_harmful: 递增对应规则计数
    - prune: 按 id 删除低效/过时规则
    """
    current_rules = list(
        _validate_playbook(yaml.safe_load(existing_yaml) if existing_yaml else None)
    )
    by_id: dict[str, dict[str, Any]] = {r.id: r.to_dict() for r in current_rules}
    order: list[str] = [r.id for r in current_rules]

    for item in ops:
        if not isinstance(item, dict) or "op" not in item:
            raise ValueError("playbook_ops 每项必须包含 op 字段")
        op = item["op"]
        if op == "add":
            rid = item.get("id", "")
            if rid in by_id:
                # 幂等转为更新而非覆盖计数
                for field_name in ("category", "trigger", "rule", "origin_issue"):
                    if item.get(field_name):
                        by_id[rid][field_name] = item[field_name]
            else:
                entry = {
                    "id": rid,
                    "category": item.get("category", "pitfall"),
                    "trigger": item.get("trigger", ""),
                    "rule": item.get("rule", ""),
                    "helpful": int(item.get("helpful", 0)),
                    "harmful": int(item.get("harmful", 0)),
                }
                if item.get("origin_issue"):
                    entry["origin_issue"] = item["origin_issue"]
                by_id[rid] = entry
                order.append(rid)
        elif op == "update":
            rid = item.get("id", "")
            if rid not in by_id:
                raise ValueError(f"无法更新不存在的 playbook 规则：{rid}")
            for field_name in ("category", "trigger", "rule", "origin_issue"):
                if item.get(field_name):
                    by_id[rid][field_name] = item[field_name]
        elif op == "mark_helpful":
            rid = item.get("id", "")
            if rid in by_id:
                by_id[rid]["helpful"] = int(by_id[rid].get("helpful", 0)) + 1
        elif op == "mark_harmful":
            rid = item.get("id", "")
            if rid in by_id:
                by_id[rid]["harmful"] = int(by_id[rid].get("harmful", 0)) + 1
        elif op == "prune":
            rid = item.get("id", "")
            if rid in by_id:
                del by_id[rid]
                order = [x for x in order if x != rid]
        else:
            raise ValueError(f"未知 playbook_ops 操作：{op!r}")

    doc = {"version": 1, "rules": [by_id[rid] for rid in order if rid in by_id]}
    # 立即复用校验器保证产出合法
    _validate_playbook(doc)
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)


def build_guidance_hooks(tool_hooks: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> list[Hook]:
    """将 tool_guidance.yaml 的声明式护栏编译为原生 MewCode Hook（仅 prompt 类型，无 Shell 风险）。"""
    compiled: list[Hook] = []
    for entry in tool_hooks:
        conditions = [Condition(field="tool", operator="==", value=entry["tool"])]
        if entry.get("file_glob"):
            conditions.append(
                Condition(
                    field="args.file_path",
                    operator="~=",
                    value=entry["file_glob"],
                )
            )
        if entry.get("command_regex"):
            conditions.append(
                Condition(
                    field="args.command",
                    operator="=~",
                    value=entry["command_regex"],
                )
            )
        compiled.append(
            Hook(
                id=f"rsi:{entry['id']}",
                event=entry["event"],
                action=Action(type="prompt", message=entry["message"]),
                condition=ConditionGroup(conditions=conditions, logic="and"),
                reject=bool(entry.get("reject", False)),
                once=bool(entry.get("once", False)),
                async_exec=False,
            )
        )
    return compiled


class GuidedTool(Tool):
    """不修改原生 Tool 代码，为指定工具动态叠加仓库级工具描述指引。"""

    def __init__(self, inner: Tool, overlay: str | None = None) -> None:
        self._inner = inner
        self.name = inner.name
        self.category = inner.category
        self.params_model = inner.params_model
        self.should_defer = inner.should_defer
        self._overlay = (overlay or "").strip()
        self.description = (
            f"{inner.description}\n\n[Repository Tool Guidance]\n{self._overlay}"
            if self._overlay
            else inner.description
        )

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return self._inner.is_concurrency_safe(args)

    def get_schema(self) -> dict[str, Any]:
        schema = dict(self._inner.get_schema())
        if self._overlay:
            schema["description"] = (
                f"{schema['description']}\n\n[Repository Tool Guidance]\n{self._overlay}"
            )
        return schema

    async def execute(self, params: Any) -> ToolResult:
        return await self._inner.execute(params)


def init_default_harness(folder: Path, repo_summary: str = "") -> str:
    """在指定目录初始化一个干净的仓库级 v1 Harness 并返回内容摘要。"""
    folder.mkdir(parents=True, exist_ok=True)
    header = (
        f"你正在为仓库「{repo_summary}」开发与维护代码。"
        if repo_summary.strip()
        else "你正在为当前代码仓库执行开发与修改任务。"
    )
    (folder / "prompt.md").write_text(
        f"{header}\n"
        "修改文件前先读取相关上下文与设计约定，优先做精准最小改动；"
        "完成后运行仓库可用的构建或测试命令验证再交付结果。\n",
        encoding="utf-8",
    )
    (folder / "workflow.yaml").write_text(
        yaml.safe_dump(
            {"stages": ["inspect", "implement", "verify"]},
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (folder / "playbook.yaml").write_text(
        yaml.safe_dump({"version": 1, "rules": []}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (folder / "tool_guidance.yaml").write_text(
        yaml.safe_dump(
            {"version": 1, "description_overlays": {}, "tool_hooks": []},
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    assembled = validate_and_assemble_harness(folder)
    return assembled.digest
