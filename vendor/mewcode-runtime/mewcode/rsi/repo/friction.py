"""在线交互偏差与失败捕获器（Friction Detector）。

借鉴 PenguinHarness `continual-learning/hooks/stop.mjs` 的会话窗口浓缩机制与
SkillAdam 的 Evolving Issue Tracker (EIT) 追踪理念：
- 对 Coding 会话做只读分析（绝不直接改写用户技能），输出结构化 FrictionEvent 并归并进仓库 EIT；
- 同时支持零成本确定性规则检测（工具报错循环、盲改、未验证交付、多轮用户纠偏关键词）
  与可选的语义 LLM 偏差提炼（精准识别前端布局/审美/需求偏差并给出客观检查建议）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from mewcode.client import create_client
from mewcode.conversation import ConversationManager
from mewcode.tools.base import TextDelta
from .store import RepoRSIStore

# 用户在后续轮次指出偏差/要求修改的常见表达（中英文）
_CORRECTION_PATTERNS = (
    re.compile(r"不对|有偏差|不符合|不是我要的|改一下|重新设计|重做|太挤|太丑|错位|遮挡|别放|不要把|不要用|应该放在|缺少|漏了|忘了|还是报错|没生效"),
    re.compile(
        r"\b(wrong|incorrect|mismatch|doesn'?t match|not what i|redo|fix the layout|don'?t put|instead of|move .+ to|missing|forgot to|still failing)\b",
        re.IGNORECASE,
    ),
)

# 视作验证/自测的 Bash 命令特征
_VERIFY_CMD_RE = re.compile(
    r"\b(pytest|unittest|npm\s+run\s+(build|test|lint|typecheck|check)|pnpm\s+(build|test|lint|typecheck)|yarn\s+(build|test)|tsc|vite\s+build|eslint|ruff|mypy|playwright|vitest|jest|cargo\s+test|go\s+test|make\s+(test|check|build)|python3?\s+\S*(test|verify|check|smoke)\S*|node\s+\S*(test|verify|check)\S*)\b",
    re.IGNORECASE,
)

_MAX_WINDOW_CHARS = 12_000


@dataclass
class TurnTrace:
    """单个 Coding 轮次的标准化视图，可从 coding.sqlite 或内存转录构造。"""

    session_id: str
    turn_id: str
    user_text: str
    assistant_text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    base_commit: str = ""
    recorded_at: str = ""


def _clip(text: str, limit: int = 800) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def load_turns_from_coding_db(
    coding_db_path: Path,
    *,
    repo_root: Path | str | None = None,
    session_id: str | None = None,
    limit_sessions: int = 10,
) -> list[TurnTrace]:
    """只读读取 webdemo/coding/store.py 的 coding.sqlite，还原指定仓库或会话的轮次轨迹。"""
    if not coding_db_path.is_file():
        return []
    conn = sqlite3.connect(f"file:{coding_db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if session_id:
            sessions = conn.execute(
                "SELECT id, cwd FROM sessions WHERE id=?", (session_id,)
            ).fetchall()
        elif repo_root is not None:
            target = str(Path(repo_root).expanduser().resolve())
            all_sessions = conn.execute(
                "SELECT id, cwd FROM sessions ORDER BY created DESC LIMIT ?",
                (limit_sessions * 4,),
            ).fetchall()
            sessions = [
                s
                for s in all_sessions
                if Path(s["cwd"]).expanduser().resolve() == Path(target)
                or Path(s["cwd"]).expanduser().resolve().is_relative_to(Path(target))
            ][:limit_sessions]
        else:
            sessions = conn.execute(
                "SELECT id, cwd FROM sessions ORDER BY created DESC LIMIT ?",
                (limit_sessions,),
            ).fetchall()

        traces: list[TurnTrace] = []
        for sess in sessions:
            sid = sess["id"]
            turns = conn.execute(
                "SELECT id, accepted FROM turns WHERE session=? ORDER BY accepted ASC",
                (sid,),
            ).fetchall()
            for t in turns:
                tid = t["id"]
                events = conn.execute(
                    "SELECT kind, payload, recorded FROM events WHERE session=? AND turn=? ORDER BY seq ASC",
                    (sid, tid),
                ).fetchall()
                user_text = ""
                assistant_chunks: list[str] = []
                calls_by_id: dict[str, dict[str, Any]] = {}
                call_order: list[str] = []
                for ev in events:
                    kind = ev["kind"]
                    payload = json.loads(ev["payload"])
                    if kind == "user":
                        user_text = payload.get("text", "")
                    elif kind == "native_record":
                        rec = json.loads(payload.get("record", "{}"))
                        if rec.get("role") == "assistant" and rec.get("content"):
                            content = rec["content"]
                            if isinstance(content, str):
                                assistant_chunks.append(content)
                    elif kind == "tool_call":
                        cid = payload.get("call_id", str(len(call_order) + 1))
                        entry = {
                            "call_id": cid,
                            "tool": payload.get("tool", ""),
                            "args": payload.get("args", {}),
                            "is_error": False,
                            "output": "",
                        }
                        calls_by_id[cid] = entry
                        call_order.append(cid)
                    elif kind == "tool_result":
                        cid = payload.get("call_id", "")
                        if cid in calls_by_id:
                            calls_by_id[cid]["is_error"] = bool(payload.get("is_error", False))
                            calls_by_id[cid]["output"] = str(payload.get("output", ""))
                traces.append(
                    TurnTrace(
                        session_id=sid,
                        turn_id=tid,
                        user_text=user_text,
                        assistant_text="\n".join(assistant_chunks).strip(),
                        tool_calls=[calls_by_id[c] for c in call_order],
                        recorded_at=t["accepted"] or "",
                    )
                )
        return traces
    finally:
        conn.close()


def condense_turns(turns: list[TurnTrace], max_chars: int = _MAX_WINDOW_CHARS) -> str:
    """借鉴 Penguin `continual-learning/hooks/stop.mjs` 的窗口浓缩器，截断过长历史。"""
    lines: list[str] = []
    for idx, turn in enumerate(turns, 1):
        lines.append(f"=== Turn {idx} ({turn.turn_id}) ===")
        if turn.user_text:
            lines.append(f"[user] {_clip(turn.user_text, 1000)}")
        for tc in turn.tool_calls:
            tool = tc.get("tool", "unknown")
            args = _clip(json.dumps(tc.get("args", {}), ensure_ascii=False), 320)
            status = "ERROR" if tc.get("is_error") else "ok"
            out = _clip(str(tc.get("output", "")), 400)
            lines.append(f"[tool {tool} ({status})] args={args} -> {out}")
        if turn.assistant_text:
            lines.append(f"[assistant] {_clip(turn.assistant_text, 900)}")

    dropped = 0
    total = sum(len(l) + 1 for l in lines)
    while total > max_chars and len(lines) > 1:
        total -= len(lines.pop(0)) + 1
        dropped += 1
    if dropped > 0:
        lines.insert(0, f"[… {dropped} earlier lines omitted]")
    return "\n".join(lines)


def _infer_category(text: str, modified_files: list[str]) -> str:
    lower = text.lower()
    if any(
        kw in lower
        for kw in ("布局", "样式", "前端", "图表", "k线", "颜色", "主题", "侧边栏", "按钮", "ui", "css", "layout", "chart", "tailwind", "responsive")
    ) or any(f.endswith((".tsx", ".jsx", ".css", ".scss", ".vue", ".html")) for f in modified_files):
        return "design"
    if any(kw in lower for kw in ("测试", "构建", "typecheck", "lint", "verify", "build", "playwright")):
        return "verification"
    if any(kw in lower for kw in ("架构", "目录", "接口", "schema", "api", "组件拆分", "state")):
        return "architecture"
    return "tooling"


def detect_deterministic_frictions(turns: list[TurnTrace]) -> list[dict[str, Any]]:
    """确定性扫描多轮轨迹，零 LLM 成本提取四类摩擦信号：

    1. tool_error_loop      —— 工具反复调用报错（EditFile 不匹配、Bash 连续失败等）
    2. blind_edit           —— 修改已有文件前未读取该文件
    3. unverified_delivery  —— 改写代码后未执行任何构建/测试校验命令就结束
    4. user_correction      —— 下一轮用户明确提出纠偏/修改意见，配对前后轮上下文
    """
    findings: list[dict[str, Any]] = []
    if not turns:
        return findings

    for idx, turn in enumerate(turns):
        read_files: set[str] = set()
        modified_files: list[str] = []
        blind_edits: list[str] = []
        error_calls: list[dict[str, Any]] = []
        last_write_idx = -1
        verified_after_write = False

        for c_idx, tc in enumerate(turn.tool_calls):
            tool = tc.get("tool", "")
            args = tc.get("args", {}) or {}
            fpath = str(args.get("file_path") or args.get("path") or "")
            if tc.get("is_error"):
                error_calls.append(
                    {
                        "tool": tool,
                        "args": args,
                        "output": _clip(str(tc.get("output", "")), 300),
                    }
                )
            if tool == "ReadFile" and fpath and not tc.get("is_error"):
                read_files.add(fpath)
            elif tool in ("EditFile", "WriteFile"):
                if tool == "EditFile" and fpath and fpath not in read_files:
                    blind_edits.append(fpath)
                if fpath and not tc.get("is_error"):
                    if fpath not in modified_files:
                        modified_files.append(fpath)
                    last_write_idx = c_idx
            elif tool == "Bash":
                cmd = str(args.get("command", ""))
                if last_write_idx >= 0 and c_idx > last_write_idx and _VERIFY_CMD_RE.search(cmd):
                    if not tc.get("is_error"):
                        verified_after_write = True

        # 1. 工具报错循环
        if len(error_calls) >= 2:
            tools_hit = sorted({e["tool"] for e in error_calls})
            findings.append(
                {
                    "session_id": turn.session_id,
                    "turn_id": turn.turn_id,
                    "kind": "tool_error_loop",
                    "category": "tooling",
                    "severity": "high" if len(error_calls) >= 3 else "medium",
                    "title": f"工具调用反复报错（{', '.join(tools_hit)}）",
                    "summary": f"轮次 {turn.turn_id} 中工具调用失败 {len(error_calls)} 次（{', '.join(tools_hit)}），存在明显调用摩擦。",
                    "base_commit": turn.base_commit,
                    "evidence": {
                        "error_count": len(error_calls),
                        "errors": error_calls[:6],
                        "modified_files": modified_files,
                    },
                }
            )

        # 2. 未先读再改（盲改）
        if blind_edits:
            uniq = sorted(set(blind_edits))
            findings.append(
                {
                    "session_id": turn.session_id,
                    "turn_id": turn.turn_id,
                    "kind": "blind_edit",
                    "category": "tooling",
                    "severity": "medium",
                    "title": "未读取上下文即直接 EditFile 盲改",
                    "summary": f"轮次 {turn.turn_id} 在未 ReadFile 的情况下直接 EditFile 修改了 {', '.join(uniq[:4])}。",
                    "base_commit": turn.base_commit,
                    "evidence": {
                        "blind_files": uniq,
                        "modified_files": modified_files,
                    },
                }
            )

        # 3. 改完代码未做验证即交付
        code_files = [
            f
            for f in modified_files
            if f.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".vue", ".css", ".go", ".rs"))
        ]
        if code_files and not verified_after_write:
            findings.append(
                {
                    "session_id": turn.session_id,
                    "turn_id": turn.turn_id,
                    "kind": "unverified_delivery",
                    "category": "verification",
                    "severity": "medium",
                    "title": "修改代码后未执行构建或测试校验即交付",
                    "summary": f"轮次 {turn.turn_id} 修改了 {len(code_files)} 个代码文件，但最后一次编辑后未执行构建/类型检查/测试命令。",
                    "base_commit": turn.base_commit,
                    "evidence": {
                        "modified_files": code_files,
                        "total_tool_calls": len(turn.tool_calls),
                    },
                }
            )

        # 4. 配对识别用户纠偏（当前轮为第 2 轮及以上，且包含纠偏意图）
        if idx > 0 and turn.user_text:
            if any(p.search(turn.user_text) for p in _CORRECTION_PATTERNS):
                prev = turns[idx - 1]
                prev_files = sorted(
                    {
                        str((tc.get("args") or {}).get("file_path") or "")
                        for tc in prev.tool_calls
                        if tc.get("tool") in ("EditFile", "WriteFile")
                        and not tc.get("is_error")
                        and (tc.get("args") or {}).get("file_path")
                    }
                )
                category = _infer_category(
                    f"{prev.user_text}\n{turn.user_text}", prev_files
                )
                findings.append(
                    {
                        "session_id": turn.session_id,
                        "turn_id": turn.turn_id,
                        "kind": "user_correction",
                        "category": category,
                        "severity": "high",
                        "title": f"用户纠偏：{_clip(turn.user_text, 48)}",
                        "summary": f"针对前序任务「{_clip(prev.user_text, 120)}」，用户在下一轮提出修改意见：「{_clip(turn.user_text, 240)}」。",
                        "base_commit": prev.base_commit or turn.base_commit,
                        "evidence": {
                            "original_turn_id": prev.turn_id,
                            "original_prompt": prev.user_text,
                            "previous_assistant_summary": _clip(prev.assistant_text, 800),
                            "previous_modified_files": prev_files,
                            "user_feedback": turn.user_text,
                        },
                    }
                )

    return findings


SEMANTIC_EXTRACTOR_SYSTEM = """你是仓库级自进化系统的在线偏差分析器（只读）。
请分析一段真实编程会话的浓缩轨迹，判断模型是否存在值得沉淀到仓库级 Harness 的偏差、失败或可复用的纠偏规范。
严禁输出 Markdown 围栏；返回严格 JSON 对象：
{
  "findings": [
    {
      "kind": "user_correction | tool_error_loop | verification_failure | positive_workflow",
      "category": "design | tooling | architecture | verification | pitfall",
      "severity": "high | medium | low",
      "title": "简短可归并的问题标题（<=40字）",
      "summary": "问题根因与用户期望行为的清晰总结",
      "original_prompt": "若为用户纠偏，还原纠偏前的原始任务需求，否则为空字符串",
      "user_feedback": "用户给出的具体修改意见或期望标准",
      "suggested_checks": ["可客观执行的命令或 DOM/文件断言建议"],
      "suggested_rubric": ["可用 0/1 二元判定的核查条文"]
    }
  ]
}
若本段会话没有值得沉淀的仓库级教训，返回 {"findings": []}。"""


async def extract_semantic_frictions(
    turns: list[TurnTrace],
    provider: Any,
    *,
    timeout_seconds: int = 60,
) -> list[dict[str, Any]]:
    """可选 LLM 语义偏差提取：精准捕获隐晦的审美、前端布局、交互需求偏差。"""
    if not turns or provider is None:
        return []
    excerpt = condense_turns(turns)
    conv = ConversationManager()
    conv.add_user_message(excerpt)
    chunks: list[str] = []
    async with asyncio.timeout(timeout_seconds):
        async for ev in create_client(provider).stream(
            conv, system=SEMANTIC_EXTRACTOR_SYSTEM, tools=[]
        ):
            if isinstance(ev, TextDelta):
                chunks.append(ev.text)
    raw = "".join(chunks).strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n|\n```$", "", raw).strip()
    data = json.loads(raw)
    items = data.get("findings", []) if isinstance(data, dict) else []
    anchor = turns[-1]
    base_commit = next((t.base_commit for t in turns if t.base_commit), "")
    results: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or not item.get("summary"):
            continue
        cat = item.get("category", "pitfall")
        if cat not in ("design", "tooling", "architecture", "verification", "pitfall"):
            cat = "pitfall"
        results.append(
            {
                "session_id": anchor.session_id,
                "turn_id": anchor.turn_id,
                "kind": item.get("kind", "user_correction"),
                "category": cat,
                "severity": item.get("severity", "medium"),
                "title": str(item.get("title") or item["summary"])[:80],
                "summary": str(item["summary"]),
                "base_commit": base_commit,
                "evidence": {
                    "original_prompt": item.get("original_prompt", ""),
                    "user_feedback": item.get("user_feedback", ""),
                    "suggested_checks": item.get("suggested_checks", []),
                    "suggested_rubric": item.get("suggested_rubric", []),
                    "source": "llm_semantic",
                },
            }
        )
    return results


async def scan_and_record_frictions(
    store: RepoRSIStore,
    turns: list[TurnTrace],
    *,
    provider: Any | None = None,
) -> dict[str, Any]:
    """执行确定性检测（+ 可选语义提取），并原子写入 friction_events 与 EIT 台账。"""
    detected = detect_deterministic_frictions(turns)
    if provider is not None:
        try:
            semantic = await extract_semantic_frictions(turns, provider)
            detected.extend(semantic)
        except Exception:
            # 语义分析失败不影响确定性检测结果
            pass

    saved_frictions: list[dict[str, Any]] = []
    touched_issues: dict[str, dict[str, Any]] = {}
    for item in detected:
        issue = store.upsert_eit_issue(
            category=item["category"],
            title=item["title"],
            description=item["summary"],
        )
        touched_issues[issue["issue_id"]] = issue
        fric = store.add_friction(
            session_id=item["session_id"],
            turn_id=item["turn_id"],
            kind=item["kind"],
            category=item["category"],
            severity=item["severity"],
            summary=item["summary"],
            evidence=item["evidence"],
            base_commit=item.get("base_commit", ""),
            issue_id=issue["issue_id"],
        )
        saved_frictions.append(fric)

    return {
        "scanned_turns": len(turns),
        "frictions_added": len(saved_frictions),
        "frictions": saved_frictions,
        "eit_issues": list(touched_issues.values()),
    }
