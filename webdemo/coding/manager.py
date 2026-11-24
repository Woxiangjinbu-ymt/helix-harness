"""受理轮次、监督独立 Agent 进程并串行提交工具与写入决定。"""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from mewcode.memory.session import SessionRecord
from mewcode.permissions.rules import Rule, RuleEngine, extract_content
from mewcode.tools.bash import Bash
from mewcode.tools.edit_file import EditFile
from mewcode.tools.glob import Glob
from mewcode.tools.grep import Grep
from mewcode.tools.read_file import ReadFile
from mewcode.tools.write_file import WriteFile
from webdemo import providers

from . import actions, profiles, worker
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
    process: object = None
    unsafe: bool = False
    last_seq: int = 0
    last_request: str = ""
    last_reply: dict = field(default_factory=dict)


class Manager:
    def __init__(self, store):
        self.store = store
        self.running = {}
        self.tasks = set()

    async def shutdown(self):
        pending = list(self.tasks)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    def submit(self, sid, request, text):
        result, created = self.store.submit(
            sid,
            request,
            text,
            lambda profile: profiles.snapshot(profile, self.store.root),
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
            config, provider = providers.select(snap["provider"])
            secret = provider.resolve_api_key()
            if (
                profiles.credential_version(self.store.root, secret)
                != snap["credential_ref"]
            ):
                raise ValueError("模型凭据已变更")
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
            definitions, refs = profiles.command_hooks(config)
            if refs != snap["extensions"]["hooks"]:
                raise ValueError("Hook 配置已在轮次接手前改变")
            from mewcode.hooks.loader import load_hooks

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
                        SessionRecord(
                            "user", data["text"], datetime.fromisoformat(r["recorded"])
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
                "rules": str(rules),
                "snapshot": snap,
                "credential": secret,
                "history": records,
                "hooks": definitions,
                "instructions": "\n".join(instructions),
            }

    def append(self, db, job, kind, data, source=None):
        if job.secret:

            def redact(item):
                if isinstance(item, str):
                    return item.replace(job.secret, "[redacted]")
                if isinstance(item, list):
                    return [redact(x) for x in item]
                if isinstance(item, dict):
                    return {k: redact(v) for k, v in item.items()}
                return item

            data = redact(data)
        return self.store.event(db, job.sid, job.tid, source or uid(), kind, data)

    async def extension(self, job, p):
        from mewcode.hooks.models import HookContext

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
            self.store.event(
                db, sid, tid, "terminal", "state", {"state": state, "reason": reason}
            )

    async def execute(self, sid, tid):
        job = None
        final, reason = "interrupted", "执行进程已中断"
        try:
            job, initial = self.start_data(sid, tid)
            self.running[tid] = job
            proc = job.process = await worker.start_process()
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
            final, reason = "failed", str(exc)
        finally:
            if job and job.process and job.process.returncode is None:
                with suppress(ProcessLookupError):
                    job.process.terminate()
                await job.process.wait()
            if job and job.secret and reason:
                reason = reason.replace(job.secret, "[redacted]")
            self.running.pop(tid, None)
            self.finalize(sid, tid, final, reason)

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
        if tool not in TOOLS or (
            not rule
            and (
                tool != p["tool"]
                or TOOLS[tool].params_model.model_validate(args).model_dump()
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
        _, provider = providers.select(job.snapshot["provider"])
        if (
            profiles.credential_version(self.store.root, provider.resolve_api_key())
            != job.snapshot["credential_ref"]
        ):
            raise ValueError("模型凭据已撤销")
        decision = checker(
            job.tree.root,
            job.snapshot["permission_mode"],
            job.rules,
            job.snapshot["sandbox"],
        ).check(TOOLS[tool], args)
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
        writable = rule or tool in ("Bash", "WriteFile", "EditFile")
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

        async def note(kind, value):
            with self.store.connection(True) as db:
                self.append(db, job, kind, dict(call_id=call, action_id=aid, **value))
            if kind == "process_started":
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
                    max(1, min(600, args.get("timeout", 120))),
                    self.store.root,
                    note,
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
                    job.tree, job.relative, tool, args, job.reads, job.scratch
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
