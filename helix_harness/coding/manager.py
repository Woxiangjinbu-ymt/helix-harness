"""受理轮次、监督独立 Agent 进程并串行提交工具与写入决定。"""

from __future__ import annotations

import asyncio
import fcntl
import json
import re
import shutil
import sqlite3
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from mtcode.permissions.rules import Rule, RuleEngine, extract_content
from mtcode.tools.bash import Bash
from mtcode.tools.edit_file import EditFile
from mtcode.tools.glob import Glob
from mtcode.tools.grep import Grep
from mtcode.tools.read_file import ReadFile
from mtcode.tools.write_file import WriteFile
from helix_harness import providers

from . import actions, profiles, worker
from .mcp_bridge import BoundMCPTool
from .model_input import user_record
from .models import Conflict
from .runtime_adapter import checker
from .store import digest, encode, now, uid
from .worktrees import Worktree

TOOLS = {cls.name: cls() for cls in (ReadFile, WriteFile, EditFile, Bash, Glob, Grep)}


@dataclass
class Running:
    sid: str
    tid: str
    instance: str
    tree_id: str
    tree: Worktree
    relative: str
    snapshot: dict
    rules: Path
    scratch: Path
    calls: dict = field(default_factory=dict)
    reads: dict = field(default_factory=dict)
    waiting: dict = field(default_factory=dict)
    secret: str = field(default="", repr=False)
    hooks: list = field(default_factory=list)
    mcp: dict = field(default_factory=dict)
    mcp_tools: dict = field(default_factory=dict)
    policy: list | None = None
    process: object = None
    unsafe: bool = False
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    last_seq: int = 0
    last_request: str = ""
    last_reply: dict = field(default_factory=dict)


class Manager:
    def __init__(self, store):
        self.store = store
        self.running = {}
        self.tasks = set()
        self.owner = None
        self.closing = False
        self.faults = {}
        self.reaper = None

    def activate(self):
        if self.owner:
            return
        owner = (self.store.root / "service.lock").open("a")
        try:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.store.connection(True) as db:
                for row in db.execute(
                    "SELECT session,id FROM turns WHERE ended IS NULL"
                ).fetchall():
                    sid, tid = row
                    db.execute(
                        "UPDATE turns SET state='interrupted',ended=?,reason=? WHERE id=?",
                        (now(), "服务中断，执行实例已失联", tid),
                    )
                    db.execute(
                        "UPDATE permissions SET state='expired' WHERE turn=? AND state IN ('waiting','answered')",
                        (tid,),
                    )
                    db.execute(
                        "UPDATE leases SET state='draining' WHERE turn=?", (tid,)
                    )
                    self.store.event(
                        db,
                        sid,
                        tid,
                        "terminal",
                        "state",
                        {"state": "interrupted", "reason": "服务中断，执行实例已失联"},
                    )
            self.reconcile()
        except BaseException:
            owner.close()
            raise
        self.owner = owner
        self.closing = False
        self.reaper = asyncio.create_task(self.reap())

    def reconcile(self):
        with self.store.connection() as db:
            pending = db.execute("""SELECT a.*,t.session FROM actions a JOIN turns t ON t.id=a.turn
                                  WHERE a.state='running' AND t.ended IS NOT NULL""").fetchall()
        if not pending:
            return
        cleaned = []
        with self.store.connection(True) as db:
            for action in pending:
                sid, tid, aid = action["session"], action["turn"], action["id"]
                process = None
                for row in db.execute(
                    "SELECT kind,payload FROM events WHERE turn=? AND kind IN ('process_started','process_exited') ORDER BY seq",
                    (tid,),
                ):
                    p = json.loads(row["payload"])
                    if p.get("action_id") == aid:
                        process = p["job"] if row["kind"] == "process_started" else None
                self.store.event(
                    db,
                    sid,
                    tid,
                    "unknown/" + aid,
                    "unknown_result",
                    {
                        "call_id": action["call"],
                        "action_id": aid,
                        "tool": action["target"],
                    },
                )
                if process:
                    if not re.fullmatch(
                        r"local\.(?:helix-harness|mewcode)\.coding\.[a-f0-9]{32}", process
                    ):
                        continue
                    proof = self.store.root / "commands" / process / "clean"
                    if not proof.is_file() or proof.read_text() != process:
                        continue
                    try:
                        actions.clear_command_files(proof.parent)
                    except OSError:
                        continue
                    cleaned.append(proof.parent)
                db.execute(
                    "UPDATE actions SET state='unknown' WHERE turn=? AND id=? AND state='running'",
                    (tid, aid),
                )
                db.execute(
                    """UPDATE leases SET state='free',turn=NULL,worker=NULL WHERE turn=? AND worker=?
                           AND generation=? AND NOT EXISTS(SELECT 1 FROM actions WHERE turn=? AND state='running' AND generation=?)""",
                    (
                        tid,
                        action["worker"],
                        action["generation"],
                        tid,
                        action["generation"],
                    ),
                )

        for folder in cleaned:
            shutil.rmtree(folder, ignore_errors=True)

    async def reap(self):
        while True:
            await asyncio.sleep(1)
            try:
                self.reconcile()
            except sqlite3.DatabaseError:
                continue

    async def shutdown(self):
        self.closing = True
        if self.reaper:
            self.reaper.cancel()
            await asyncio.gather(self.reaper, return_exceptions=True)
        pending = list(self.tasks)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if self.owner:
            self.owner.close()
            self.owner = None

    async def report_delayed_cleanup(self, job):
        await asyncio.sleep(2)
        with self.store.connection(True) as db:
            row = db.execute(
                "SELECT state FROM turns WHERE id=? AND worker=?",
                (job.tid, job.instance),
            ).fetchone()
            if row and row[0] == "cancelling":
                self.append(
                    db,
                    job,
                    "cleaning",
                    {"reason": "清理耗时较长，等待本轮子进程退出和临时资源核验"},
                    "delayed-cleanup",
                )

    def cancel(self, sid, tid, request):
        result = self.store.cancel(sid, tid, request)
        job = self.running.get(tid)
        if (
            result["state"] == "cancelling"
            and job is not None
            and not job.stop.is_set()
        ):
            job.stop.set()
            report = asyncio.create_task(self.report_delayed_cleanup(job))
            self.tasks.add(report)
            report.add_done_callback(self.tasks.discard)
            for future in job.waiting.values():
                if not future.done():
                    future.set_exception(Conflict("轮次已取消"))
            if job.process and job.process.returncode is None:
                stop_task = asyncio.create_task(worker.stop_turn(job.process))
                self.tasks.add(stop_task)
                stop_task.add_done_callback(self.tasks.discard)
        return result

    def submit(self, sid, request, text, extras=None):
        payload = {"text": text, **extras} if extras else text
        if sid in self.faults:
            with self.store.connection() as db:
                receipt = self.store.existing(db, sid + "/submit", request, payload)
                if receipt is not None:
                    return receipt
            raise sqlite3.OperationalError("进度存储不可用")
        result, created = self.store.submit(
            sid,
            request,
            text,
            lambda profile: (
                profiles.for_session(self.store, sid, profile, extras)
                if extras
                else profiles.for_session(self.store, sid, profile)
            ),
            extras,
        )
        if created:
            task = asyncio.create_task(self.execute(sid, result["turn_id"]))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
        return result

    def start_data(self, sid, tid):
        with self.store.connection(True) as db:
            session = self.store.session(sid, db)
            row = db.execute(
                """SELECT t.state,s.document FROM turns t JOIN snapshots s ON s.id=t.snapshot
                                WHERE t.session=? AND t.id=?""",
                (sid, tid),
            ).fetchone()
            if row is None or row["state"] != "accepted":
                raise Conflict("轮次已失效")
            tree = Worktree(**session["identity"])
            directory = tree.verify(session["cwd_relative"])
            snap = json.loads(row["document"])
            secret = profiles.secret_for(snap, self.store)
            rules = (
                self.store.root
                / "permissions"
                / (
                    session["worktree_id"]
                    + "-"
                    + digest(session["cwd_relative"])[:16]
                    + ".yaml"
                )
            )
            scratch = self.store.root / "sessions" / sid
            scratch.mkdir(mode=0o700, parents=True, exist_ok=True)
            job = Running(
                sid,
                tid,
                uid(),
                session["worktree_id"],
                tree,
                session["cwd_relative"],
                snap,
                rules,
                scratch,
                secret=secret,
            )
            if "bindings" in snap:
                material = json.loads(
                    (self.store.root / "bindings" / snap["bindings"]).read_text()
                )
                if (
                    profiles.credential_version(self.store.root, encode(material))
                    != snap["bindings"]
                ):
                    raise ValueError("配置材料校验失败")
                definitions = list(material["hooks"].values())
            else:
                config, _ = providers.select(snap["provider"])
                definitions, refs = profiles.command_hooks(config)
                if refs != snap["extensions"]["hooks"]:
                    raise ValueError("Hook 配置已变化")
                material = {"skills": {}, "mcp": {}}
            job.mcp = material["mcp"]
            job.policy = material.get("policy")
            from mtcode.hooks.loader import load_hooks

            job.hooks = load_hooks(definitions)
            db.execute("UPDATE turns SET worker=? WHERE id=?", (job.instance, tid))
            records = []
            for r in db.execute(
                "SELECT kind,payload,recorded FROM events WHERE session=? AND kind IN ('user','native_record') ORDER BY seq",
                (sid,),
            ):
                data = json.loads(r["payload"])
                if r["kind"] == "user":
                    records.append(
                        user_record(
                            data["text"],
                            data.get("attachments", []),
                            datetime.fromisoformat(r["recorded"]),
                        ).to_jsonl()
                    )
                else:
                    records.append(data["record"])
            instructions = []
            for name in ("AGENTS.md", "MEWCODE.md"):
                try:
                    instructions.append(tree.read(name).decode())
                except (OSError, ValueError):
                    continue
            return job, {
                "instance": job.instance,
                "session_id": sid,
                "root": tree.root,
                "directory": str(directory),
                "private_dir": str(scratch),
                "attachment_db": str(self.store.path),
                "rules": str(rules),
                "snapshot": snap,
                "credential": secret,
                "history": records,
                "hooks": definitions,
                "skills": material["skills"],
                "harness": material.get("harness"),
                "policy": job.policy,
                "instructions": "\n".join(instructions),
            }

    def append(self, db, job, kind, data, source=None):
        if job.secret and kind != "native_record":
            data = {
                k: profiles.redact(v, job.secret)
                if k
                in (
                    "text",
                    "args",
                    "output",
                    "error",
                    "reason",
                    "feedback",
                    "description",
                    "scope",
                    "command",
                    "rule_error",
                )
                else v
                for k, v in data.items()
            }
        return self.store.event(db, job.sid, job.tid, source or uid(), kind, data)

    async def extension(self, job, p):
        from mtcode.hooks.models import HookContext

        parent = job.calls[p["parent_call"]]
        hook = next(
            (
                item
                for item in job.hooks
                if item.id == p["hook_id"] and item.event == p["event"]
            ),
            None,
        )
        if hook is None:
            raise ValueError("当前快照未启用此 Hook")
        ctx = HookContext(
            event_name=hook.event,
            tool_name=parent["tool"],
            tool_args=parent["args"],
            file_path=parent["args"].get("file_path", parent["args"].get("path", "")),
        )
        args = {
            "command": ctx.expand(hook.action.command),
            "timeout": hook.action.timeout,
        }
        call, aid = uid(), uid()
        item = {
            "call_id": call,
            "parent_call_id": p["parent_call"],
            "hook_id": hook.id,
            "tool": "Bash",
            "args": args,
        }
        job.calls[call] = item
        with self.store.connection(True) as db:
            self.append(db, job, "tool_call", item)
        try:
            result = await self.action(
                job, {"tool": "Bash", "args": args, "call_id": call, "action_id": aid}
            )
        except (Conflict, ValueError, OSError) as exc:
            result = {"is_error": True, "output": str(exc)}
        with self.store.connection(True) as db:
            self.append(
                db, job, "tool_result", dict(call_id=call, tool="Bash", **result)
            )
        return result

    def finalize(self, sid, tid, state, reason):
        with self.store.connection(True) as db:
            row = db.execute(
                "SELECT state,ended FROM turns WHERE session=? AND id=?", (sid, tid)
            ).fetchone()
            if not row or row["ended"] is not None:
                return
            if row["state"] == "cancelling":
                state, reason = "cancelled", None
            if db.execute(
                "SELECT 1 FROM actions WHERE turn=? AND state='running'", (tid,)
            ).fetchone():
                db.execute("UPDATE leases SET state='draining' WHERE turn=?", (tid,))
                state, reason = "interrupted", "执行已丢失，等待核实工作树清理"
            db.execute(
                "UPDATE turns SET state=?,ended=?,reason=? WHERE id=?",
                (state, now(), reason, tid),
            )
            db.execute(
                "UPDATE permissions SET state='expired' WHERE turn=? AND state IN ('waiting','answered')",
                (tid,),
            )
            error_type = (
                "storage"
                if sid in self.faults
                else "model"
                if state == "failed"
                else None
            )
            self.store.event(
                db,
                sid,
                tid,
                "terminal",
                "state",
                {"state": state, "reason": reason, "error_type": error_type},
            )

    async def execute(self, sid, tid):
        job = None
        final, reason = "interrupted", "执行进程已中断"
        try:
            job, initial = self.start_data(sid, tid)
            self.running[tid] = job
            schemas = []
            for name, config in job.mcp.items():
                try:
                    found = await self.probe(
                        job.tree, job.tree.verify(job.relative), config, job.stop
                    )
                    for schema in found:
                        tool = BoundMCPTool(schema)
                        if tool.name in TOOLS or tool.name in job.mcp_tools:
                            raise ValueError("重复的 MCP 工具名")
                        job.mcp_tools[tool.name] = (config, tool)
                        schemas.append(schema)
                    binding = {
                        "extension": "MCP",
                        "name": name,
                        "status": "connected",
                        "tools": [x["name"] for x in found],
                    }
                except ValueError as exc:
                    binding = {
                        "extension": "MCP",
                        "name": name,
                        "status": "failed",
                        "error": str(exc),
                    }
                with self.store.connection(True) as db:
                    self.append(db, job, "extension", binding)
            initial["mcp_schemas"] = schemas
            if job.stop.is_set():
                return
            proc = job.process = await worker.start_process()
            if job.stop.is_set():
                return
            await worker.send(proc.stdin, initial)
            reads = []

            async def deliver(message, signature):
                reply = await self.respond(job, message, signature)
                await worker.send(proc.stdin, reply)
                return reply

            async with asyncio.TaskGroup() as group:
                while line := await proc.stdout.readline():
                    message = json.loads(line)
                    kind = message["kind"]
                    # 原生调度器只把无副作用的文件查询编为并行批次。其余操作等待前批结束。
                    parallel = kind == "action" and message["payload"].get("tool") in (
                        "ReadFile",
                        "Glob",
                        "Grep",
                    )
                    if not parallel and reads:
                        await asyncio.gather(*reads)
                        reads.clear()
                    signature = self.accept_frame(job, message)
                    if parallel:
                        reads.append(group.create_task(deliver(message, signature)))
                        continue
                    reply = await deliver(message, signature)
                    if kind == "finish" and "error" not in reply:
                        final, reason = (
                            reply["result"]["state"],
                            reply["result"]["reason"],
                        )
                        break
                if not proc.stdout.at_eof():
                    await asyncio.gather(*reads)
                else:
                    for read in reads:
                        read.cancel()
            if proc.stdin:
                proc.stdin.close()
            if await proc.wait() != 0 and final == "completed":
                final, reason = "failed", "执行进程异常退出"
        except Exception as exc:  # noqa: BLE001 — 进程故障边界需提交轮次终态
            storage = isinstance(exc, sqlite3.DatabaseError) or (
                isinstance(exc, BaseExceptionGroup)
                and exc.subgroup(sqlite3.DatabaseError) is not None
            )
            if storage:
                self.faults[sid] = "进度存储不可用，已停止派发工具"
                if job:
                    job.stop.set()
            final, reason = "failed", self.faults.get(sid, str(exc))
        finally:
            if job and job.process and job.process.returncode is None:
                with suppress(ProcessLookupError):
                    job.process.terminate()
                await job.process.wait()
            if job and job.secret and reason:
                reason = reason.replace(job.secret, "[redacted]")
            self.running.pop(tid, None)
            while True:
                try:
                    self.finalize(sid, tid, final, reason)
                    self.faults.pop(sid, None)
                    break
                except sqlite3.DatabaseError:
                    self.faults[sid] = "进度存储不可用，已停止派发工具"
                    if self.closing:
                        break
                    await asyncio.sleep(0.5)

    async def probe(self, tree, cwd, config, stop=None):
        if "binding_error" in config:
            raise ValueError(config["name"] + " · " + config["binding_error"])

        async def quiet(kind, value):
            pass

        result = await actions.run_shell(
            tree,
            cwd,
            "MCP discovery",
            25,
            self.store.root,
            quiet,
            bridge={"config": config, "operation": "discover"},
            stop=stop,
        )
        if result["is_error"]:
            raise ValueError("MCP 服务无法连接")
        try:
            data = json.loads(result["output"])
            tools = data.get("tools")
        except (KeyError, ValueError) as exc:
            raise ValueError("MCP 服务无法列出工具") from exc

        if tools is None:
            raise ValueError(
                config["name"] + " · " + data.get("error", "MCP 工具列表无效")
            )
        return tools

    async def dispatch(self, job, message):
        return await self.respond(job, message, self.accept_frame(job, message))

    def accept_frame(self, job, message):
        seq = message["seq"]
        signature = digest(message)
        if self.running.get(job.tid) is not job or message["instance"] != job.instance:
            raise ValueError("未知执行实例")
        if seq == job.last_seq and signature == job.last_request:
            return None
        if seq != job.last_seq + 1:
            raise ValueError("执行事件序号无效")
        job.last_seq = seq
        return signature

    async def respond(self, job, message, signature):
        if signature is None:
            return job.last_reply
        seq = message["seq"]
        try:
            result = await self.handle(job, message["kind"], message["payload"])
            reply = {"seq": seq, "result": result}
        except (Conflict, ValueError, OSError) as exc:
            reply = {"seq": seq, "error": str(exc)}
            if isinstance(exc, Conflict) and "holder_session_id" in exc.details:
                with self.store.connection(True) as db:
                    self.append(
                        db,
                        job,
                        "write_conflict",
                        {"call_id": message["payload"].get("call_id"), **exc.details},
                    )
        if seq == job.last_seq:
            job.last_request, job.last_reply = signature, reply
        return reply

    async def handle(self, job, kind, p):
        if job.stop.is_set():
            raise Conflict("轮次已取消")
        if kind == "model_permission":
            profiles.secret_for(job.snapshot, self.store)
            return True
        if kind == "ready":
            with self.store.connection(True) as db:
                changed = db.execute(
                    "UPDATE turns SET state='running',started=? WHERE id=? AND worker=? AND state='accepted'",
                    (now(), job.tid, job.instance),
                ).rowcount
                if not changed:
                    raise Conflict("无法接手轮次")
                self.append(db, job, "state", {"state": "running"}, "start")
            return True
        if kind == "event":
            if p["event"] == "tool_call":
                job.calls[p["value"]["call_id"]] = p["value"]
            with self.store.connection(True) as db:
                return self.append(
                    db, job, p["event"], p["value"], f"{job.instance}/{job.last_seq}"
                )
        if kind == "hook":
            return await self.extension(job, p)
        if kind == "permission":
            return await self.permission(job, p["call_id"], p["description"])
        if kind == "delivered":
            with self.store.connection(True) as db:
                updated = db.execute(
                    """UPDATE permissions SET state='delivered' WHERE id=? AND turn=? AND call=? AND
                                    worker=? AND state='answered'""",
                    (p["permission_id"], job.tid, p["call_id"], job.instance),
                ).rowcount
                if updated != 1:
                    raise Conflict("授权已失效")
                changed = db.execute(
                    """UPDATE turns SET state='running' WHERE id=? AND state='waiting_permission'
                       AND NOT EXISTS (SELECT 1 FROM permissions WHERE turn=? AND state IN ('waiting','answered'))""",
                    (job.tid, job.tid),
                ).rowcount
                if changed:
                    self.append(db, job, "state", {"state": "running"})
            return True
        if kind in ("action", "save_rule"):
            return await self.action(job, p, kind == "save_rule")
        if kind == "finish" and p["state"] in ("failed", "completed"):
            if job.unsafe:
                raise Conflict("无法确认执行清理")
            with self.store.connection(True) as db:
                changes = {}
                for event in db.execute(
                    "SELECT payload FROM events WHERE turn=? AND kind='action_ended' ORDER BY seq",
                    (job.tid,),
                ):
                    item = json.loads(event[0])
                    for name in item.get("touched", []):
                        changes[name] = {
                            "path": name,
                            "call_id": item["call_id"],
                            "after": None,
                            "uncertain": True,
                        }
                    name = item.get("path")
                    if name and ("after" in item or item.get("uncertain")):
                        had_mixed = name in changes and changes[name]["uncertain"]
                        changes[name] = {
                            "path": name,
                            "call_id": item["call_id"],
                            "after": item.get("after"),
                            "uncertain": bool(had_mixed or item.get("uncertain")),
                        }
                for change in changes.values():
                    last = change.pop("after")
                    try:
                        change["uncertain"] |= (
                            actions.content_id(job.tree.read(change["path"])) != last
                        )
                    except (ValueError, OSError):
                        change["uncertain"] = True
                self.append(
                    db, job, "summary", {"changes": list(changes.values())}, "summary"
                )
            return {"state": p["state"], "reason": p.get("reason")}
        raise ValueError("未知执行请求")

    async def permission(self, job, call, description):
        info = job.calls[call]
        permission = uid()
        future = asyncio.get_running_loop().create_future()
        with self.store.connection(True) as db:
            db.execute(
                "INSERT INTO permissions VALUES(?,?,?,?,'waiting',?,NULL)",
                (permission, job.tid, call, job.instance, description),
            )
            db.execute(
                "UPDATE turns SET state='waiting_permission' WHERE id=?", (job.tid,)
            )
            self.append(db, job, "state", {"state": "waiting_permission"})
            self.append(
                db,
                job,
                "permission",
                {
                    "permission_id": permission,
                    "call_id": call,
                    "description": description,
                    "scope": self.scope(info),
                    "directory": str(Path(job.tree.root) / job.relative),
                    "state": "waiting_permission",
                },
            )
        job.waiting[permission] = future
        try:
            return await future
        finally:
            job.waiting.pop(permission, None)

    @staticmethod
    def scope(info):
        value = extract_content(info["tool"], info["args"])
        return "".join({"[": "[[]", "*": "[*]", "?": "[?]"}.get(c, c) for c in value)

    def answer(self, sid, tid, pid, request, decision, feedback):
        scope = f"{sid}/{tid}/{pid}"
        payload = {"decision": decision, "feedback": feedback}
        with self.store.connection(True) as db:
            row = db.execute(
                """SELECT p.* FROM permissions p JOIN turns t ON t.id=p.turn WHERE p.id=? AND
                              t.id=? AND t.session=?""",
                (pid, tid, sid),
            ).fetchone()
            if row is None:
                raise KeyError(pid)
            replay = self.store.existing(db, scope, request, payload)
            if replay:
                return replay
            job = self.running.get(tid)
            if (
                not job
                or row["worker"] != job.instance
                or row["state"] != "waiting"
                or pid not in job.waiting
            ):
                raise Conflict("授权请求已失效")
            result = dict(permission_id=pid, call_id=row["call"], **payload)
            db.execute(
                "UPDATE permissions SET state='answered',answer=? WHERE id=?",
                (encode(result), pid),
            )
            self.append(db, job, "decision", result)
            self.store.remember(db, scope, request, payload, result)
        job.waiting[pid].set_result(result)
        return result

    async def action(self, job, p, rule=False):
        call, aid = p["call_id"], p["action_id"]
        info = job.calls[call]
        tool, args = info["tool"], info["args"]
        if job.secret and profiles.redact(args, job.secret) != args:
            raise ValueError("工具参数包含模型凭据")
        binding = job.mcp_tools.get(tool)
        bound_tool = binding[1] if binding else TOOLS.get(tool)
        if bound_tool is None or (
            not rule
            and (
                tool != p["tool"]
                or bound_tool.params_model.model_validate(args).model_dump()
                != p["args"]
            )
        ):
            raise ValueError("调用参数与已登记工具不符")
        receipt_scope = f"action/{job.tid}/{job.instance}"
        receipt_payload = {"call": call, "rule": rule, "args": args, "tool": tool}
        with self.store.connection() as db:
            receipt = self.store.existing(db, receipt_scope, aid, receipt_payload)
            if receipt is not None:
                return receipt
        cwd = job.tree.verify(job.relative)
        profiles.secret_for(job.snapshot, self.store)
        decision = checker(
            job.tree.root,
            job.snapshot["permission_mode"],
            job.rules,
            job.snapshot["sandbox"],
            job.policy,
        ).check(bound_tool, args)
        if decision.effect == "deny":
            raise ValueError(decision.reason)
        if decision.effect == "ask" and not rule:
            with self.store.connection() as db:
                approved = db.execute(
                    "SELECT 1 FROM permissions WHERE turn=? AND call=? AND state='delivered'",
                    (job.tid, call),
                ).fetchone()
            if not approved:
                # 判定可能在原生检查后收紧。等待期间尚未取得许可或写入权。
                reply = await self.permission(
                    job, call, decision.reason + " · " + self.scope(info)
                )
                await self.handle(job, "delivered", reply)
                if reply["decision"] == "deny":
                    return {
                        "is_error": True,
                        "output": "授权被拒绝 " + reply["feedback"],
                    }
                if reply["decision"] == "allow_always":
                    await self.action(job, {"call_id": call, "action_id": uid()}, True)
                return await self.action(job, p)
        writable = rule or bool(binding) or tool in ("Bash", "WriteFile", "EditFile")
        with self.store.connection(True) as db:
            if db.execute(
                "SELECT state FROM turns WHERE id=? AND worker=?",
                (job.tid, job.instance),
            ).fetchone()[0] not in (
                ("running",) if writable else ("running", "waiting_permission")
            ):
                raise Conflict("轮次不能派发工具")
            permission = db.execute(
                "SELECT answer FROM permissions WHERE turn=? AND call=? AND state='delivered'",
                (job.tid, call),
            ).fetchone()
            granted = json.loads(permission[0])["decision"] if permission else None
            if decision.effect == "deny" or (
                decision.effect == "ask" and granted not in ("allow", "allow_always")
            ):
                raise ValueError("工具权限已改变，请重新获取授权")
            if rule and granted != "allow_always":
                raise Conflict("没有持续授权决定")
            previous = db.execute(
                "SELECT state FROM actions WHERE turn=? AND id=?", (job.tid, aid)
            ).fetchone()
            if previous:
                raise Conflict("动作许可已消费")
            generation = None
            if writable:
                lease = db.execute(
                    "SELECT * FROM leases WHERE tree=?", (job.tree_id,)
                ).fetchone()
                if lease["state"] == "free":
                    root = Path(job.tree.root)
                    occupied = db.execute(
                        """SELECT t.identity, u.session FROM leases l JOIN trees t ON t.id=l.tree
                           JOIN turns u ON u.id=l.turn WHERE l.state!='free'"""
                    )
                    for row in occupied:
                        other = Path(json.loads(row["identity"])["root"])
                        if root.is_relative_to(other) or other.is_relative_to(root):
                            raise Conflict(
                                "工作目录正由其他会话写入：" + row["session"],
                                holder_session_id=row["session"],
                            )
                    db.execute(
                        """UPDATE leases SET state='held',generation=generation+1,worker=?,turn=?,acquired=? WHERE tree=?""",
                        (job.instance, job.tid, now(), job.tree_id),
                    )
                elif (
                    lease["turn"] != job.tid
                    or lease["worker"] != job.instance
                    or lease["state"] != "held"
                ):
                    holder = db.execute(
                        "SELECT session FROM turns WHERE id=?", (lease["turn"],)
                    ).fetchone()
                    raise Conflict(
                        "工作树正由其他会话写入：" + holder[0],
                        holder_session_id=holder[0],
                    )
                generation = db.execute(
                    "SELECT generation FROM leases WHERE tree=?", (job.tree_id,)
                ).fetchone()[0]
            db.execute(
                "INSERT INTO actions VALUES(?,?,?,?,?,?,'running')",
                (aid, job.tid, job.instance, call, generation, tool),
            )
            self.append(
                db,
                job,
                "action_started",
                {
                    "call_id": call,
                    "action_id": aid,
                    "tool": tool,
                    "type": "save_rule" if rule else "tool",
                    "generation": generation,
                },
            )

        def guard():
            if job.stop.is_set():
                raise Conflict("轮次已取消")
            job.tree.verify(job.relative)
            profiles.secret_for(job.snapshot, self.store)
            if (
                checker(
                    job.tree.root,
                    job.snapshot["permission_mode"],
                    job.rules,
                    job.snapshot["sandbox"],
                    job.policy,
                )
                .check(bound_tool, args)
                .effect
                == "deny"
            ):
                raise ValueError("策略禁止执行")

        async def note(kind, value):
            with self.store.connection(True) as db:
                self.append(db, job, kind, dict(call_id=call, action_id=aid, **value))
            if kind == "process_started":
                guard()
                job.unsafe = True
            if kind == "process_exited":
                job.unsafe = False

        try:
            if rule:
                from tempfile import NamedTemporaryFile

                job.rules.parent.mkdir(parents=True, exist_ok=True)
                with NamedTemporaryFile(dir=job.rules.parent, delete=False) as pending:
                    temp = Path(pending.name)
                    if job.rules.exists():
                        pending.write(job.rules.read_bytes())
                try:
                    RuleEngine(local_rules_path=temp).append_local_rule(
                        Rule(tool, self.scope(info), "allow")
                    )
                    temp.replace(job.rules)
                finally:
                    temp.unlink(missing_ok=True)
                result = {
                    "output": "持续授权已保存",
                    "is_error": False,
                    "scope": self.scope(info),
                }
            elif binding:
                config, remote = binding
                bridge = {
                    "operation": "call",
                    "tool": tool,
                    "schema": remote.schema,
                    "config": config,
                    "args": args,
                }
                response = await actions.run_shell(
                    job.tree,
                    cwd,
                    tool,
                    min(120, job.snapshot["budget"]["command_timeout"]),
                    self.store.root,
                    note,
                    stop=job.stop,
                    bridge=bridge,
                )
                if response["is_error"]:
                    raise ValueError("MCP 调用中断")
                result = json.loads(response["output"])
                if "output" not in result:
                    raise ValueError("MCP 工具调用失败")
            elif tool == "Bash":
                for name, last in job.reads.items():
                    try:
                        current = actions.content_id(job.tree.read(name))
                    except FileNotFoundError:
                        current = None
                    if current != last:
                        raise ValueError("文件已更改，请重新读取 " + name)
                before = await asyncio.to_thread(job.tree.file_stamps)
                result = await actions.run_shell(
                    job.tree,
                    cwd,
                    args["command"],
                    max(
                        1,
                        min(
                            job.snapshot["budget"].get("command_timeout", 600),
                            args.get("timeout", 120),
                        ),
                    ),
                    self.store.root,
                    note,
                    stop=job.stop,
                )
                after = await asyncio.to_thread(job.tree.file_stamps)
                result["touched"] = sorted(
                    k
                    for k in before.keys() | after.keys()
                    if before.get(k) != after.get(k)
                )
            elif tool in ("Glob", "Grep"):
                result = await worker.search(job.tree, job.relative, tool, args)
            elif (
                tool == "ReadFile"
                and Path(args["file_path"]).parent == job.scratch / "tool-results"
            ):
                path = Path(args["file_path"])
                if (
                    not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}\.txt", path.name)
                    or path.is_symlink()
                ):
                    raise ValueError("无效的会话输出路径")
                read = await ReadFile().execute(
                    ReadFile.params_model.model_validate(args)
                )
                result = {"output": read.output, "is_error": read.is_error}
            else:
                result = await actions.file_action(
                    job.tree,
                    job.relative,
                    tool,
                    args,
                    job.reads,
                    job.scratch,
                    **({"guard": guard} if writable else {}),
                )
        except (ValueError, OSError, RuntimeError, re.error) as exc:
            result = {"output": str(exc), "is_error": True}
        if job.unsafe:
            raise RuntimeError("无法核实写进程已退出")
        with self.store.connection(True) as db:
            db.execute(
                "UPDATE actions SET state='ended' WHERE id=? AND turn=? AND worker=?",
                (aid, job.tid, job.instance),
            )
            if job.secret and result.get("output"):
                result["output"] = result["output"].replace(job.secret, "[redacted]")
            self.store.remember(db, receipt_scope, aid, receipt_payload, result)
            facts = {k: v for k, v in result.items() if k != "output"}
            if rule and result["is_error"]:
                facts["rule_error"] = result["output"]
            self.append(
                db, job, "action_ended", dict(call_id=call, action_id=aid, **facts)
            )
            db.execute(
                """UPDATE leases SET state='free',turn=NULL,worker=NULL WHERE tree=? AND turn=? AND worker=?
                        AND generation=? AND NOT EXISTS(SELECT 1 FROM actions WHERE turn=? AND generation=? AND state='running')""",
                (job.tree_id, job.tid, job.instance, generation, job.tid, generation),
            )
        return result
