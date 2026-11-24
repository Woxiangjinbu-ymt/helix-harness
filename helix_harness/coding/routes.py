"""普通工作树、会话及其只读视图。"""

import asyncio
import json
import sqlite3
from functools import lru_cache
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from . import attachments, profiles
from .manager import Manager
from .models import (
    CancelRequest,
    Conflict,
    DirectoryRequest,
    FileReference,
    PermissionAnswer,
    ProfileRequest,
    SessionOptions,
    SessionRequest,
    TurnRequest,
)
from .store import Store
from .worktrees import Worktree, browse_directories


async def activate():
    try:
        manager().activate()
    except BlockingIOError as exc:
        raise HTTPException(503, "Coding 数据目录已由另一服务使用") from exc


router = APIRouter(prefix="/api/coding", dependencies=[Depends(activate)])


@lru_cache(maxsize=1)
def store():
    return Store()


@lru_cache(maxsize=1)
def manager():
    return Manager(store())


@router.post("/sessions/{sid}/attachments/{aid}")
async def upload(sid: str, aid: UUID, request: Request, name: str):
    with store().connection() as db:
        store().session(sid, db)
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > attachments.MAX_FILE:
            raise HTTPException(413, "每个附件最多 4 MiB")
        data.extend(chunk)
    try:
        return attachments.stage(store(), sid, str(aid), name, bytes(data))
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/sessions/{sid}/references/{aid}")
def reference(sid: str, aid: UUID, form: FileReference):
    path = form.path
    try:
        data = location(sid).read(path)
        return attachments.stage(store(), sid, str(aid), path, data)
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(404, "文件不可用") from exc


@router.get("/sessions/{sid}/attachments/{aid}")
def retrieve(sid: str, aid: UUID):
    with store().connection() as db:
        row = db.execute(
            "SELECT mime,data FROM attachments WHERE session=? AND id=?",
            (sid, str(aid)),
        ).fetchone()
    if not row:
        raise HTTPException(404, "附件不可用")
    return Response(
        bytes(row["data"]),
        media_type=row["mime"],
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "private, no-store",
        },
    )


@router.post("/sessions/{sid}/turns", status_code=202)
async def submit(sid: str, form: TurnRequest):
    try:
        extras = form.model_dump(
            mode="json",
            exclude={"text", "submit_request_id"},
            exclude_none=True,
            exclude_defaults=True,
        )
        return manager().submit(sid, form.submit_request_id, form.text, extras)
    except Conflict as exc:
        raise HTTPException(409, {"message": str(exc), **exc.details}) from exc
    except OSError as exc:
        raise HTTPException(409, "会话工作目录不可用") from exc


@router.post("/sessions/{sid}/turns/{tid}/cancel")
async def cancel(sid: str, tid: str, form: CancelRequest):
    return manager().cancel(sid, tid, form.request_id)


@router.post("/sessions/{sid}/turns/{tid}/permissions/{pid}/reply")
async def answer(sid: str, tid: str, pid: str, form: PermissionAnswer):
    try:
        return manager().answer(
            sid, tid, pid, form.request_id, form.decision, form.feedback
        )
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/sessions/{sid}/events")
async def events(sid: str, request: Request, after: str = ""):
    cursor = request.headers.get("last-event-id") or after or sid + "/0"
    owner, _, number = cursor.rpartition("/")
    try:
        with store().connection() as db:
            store().session(sid, db)
            first, last = db.execute(
                "SELECT coalesce(min(seq),1),coalesce(max(seq),0) FROM events WHERE session=?",
                (sid,),
            ).fetchone()
    except sqlite3.DatabaseError:
        return StreamingResponse(
            iter(["event: storage_error\ndata: {}\n\n"]), media_type="text/event-stream"
        )

    async def stream():
        if (
            owner != sid
            or not number.isdecimal()
            or len(number) > 18
            or int(number) < first - 1
            or int(number) > last
        ):
            yield "event: reset_required\ndata: {}\n\n"
            return
        seq = int(number)
        while not await request.is_disconnected():
            try:
                if sid in manager().faults:
                    raise sqlite3.OperationalError()
                with store().connection() as db:
                    rows = store().events(db, sid, seq, 100)
            except sqlite3.DatabaseError:
                yield "event: storage_error\ndata: {}\n\n"
                return
            for row in rows:
                seq = row["seq"]
                yield f"id: {sid}/{seq}\ndata: {json.dumps(row, ensure_ascii=False)}\n\n"
            if not rows:
                yield ": heartbeat\n\n"
                await asyncio.sleep(0.35)

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@router.get("/defaults")
def defaults():
    from helix_harness import providers

    try:
        _, provider = providers.select()
    except ValueError as exc:
        return {
            "directory": "",
            "provider": "",
            "model": "",
            "error": str(exc),
        }
    return {
        "directory": "",
        "provider": provider.name,
        "model": provider.model,
    }


@router.post("/directories")
def directories(form: DirectoryRequest):
    return browse_directories(form.directory)


@router.post("/sessions", status_code=201)
def create(form: SessionRequest):
    try:
        session = store().create_session(
            form.directory, form.profile_id, form.request_id
        )
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(422, "无法打开目录，请检查路径和访问权限") from exc
    session.pop("identity")
    return session


@router.get("/sessions")
def sessions(worktree_id: str | None = None):
    with store().connection() as db:
        rows = []
        for row in db.execute(
            "SELECT id FROM sessions WHERE (? IS NULL OR tree=?) ORDER BY active DESC",
            (worktree_id, worktree_id),
        ):
            data = store().session(row["id"], db)
            data.pop("identity")
            rows.append(data)
        return rows


def location(sid):
    with store().connection() as db:
        s = store().session(sid, db)
    tree = Worktree(**s["identity"])
    try:
        tree.verify(s["cwd_relative"])
    except (OSError, ValueError) as exc:
        raise HTTPException(409, "会话工作目录不可用") from exc
    return tree


@router.get("/sessions/{sid}/view")
def view(sid: str):
    with store().connection() as db:
        s = store().session(sid, db)
        try:
            Worktree(**s.pop("identity")).verify(s["cwd_relative"])
            s["available"] = True
        except (OSError, ValueError):
            s["available"] = False
        turns = [
            dict(r)
            for r in db.execute(
                "SELECT id turn_id,snapshot snapshot_id,state,accepted,started,ended,reason FROM turns WHERE session=? ORDER BY accepted",
                (sid,),
            )
        ]
        events = store().events(db, sid)
        seq = db.execute(
            "SELECT coalesce(max(seq),0) FROM events WHERE session=?", (sid,)
        ).fetchone()[0]
        permissions = [
            dict(row)
            for row in db.execute(
                """SELECT p.id permission_id,p.turn turn_id,p.call call_id,p.summary description
                    FROM permissions p JOIN turns t ON p.turn=t.id WHERE t.session=? AND p.state='waiting'""",
                (sid,),
            )
        ]
        return {
            "session": s,
            "turns": turns,
            "events": events,
            "permissions": permissions,
            "through_seq": seq,
        }


@router.get("/sessions/{sid}/files")
def files(sid: str, query: str = "", content: bool = False):
    return location(sid).files(query, content)


@router.get("/sessions/{sid}/file")
def file(sid: str, path: str):
    try:
        return {"path": path, "content": location(sid).read(path).decode("utf-8")}
    except (FileNotFoundError, IsADirectoryError) as exc:
        raise HTTPException(404, "文件不可用") from exc


@router.get("/sessions/{sid}/diff")
def changes(sid: str, path: str | None = None):
    return location(sid).changes(path)


@router.get("/profiles")
def all_profiles():
    from mewcode.context.manager import compute_compact_threshold

    return [
        dict(p, compact_threshold=compute_compact_threshold(p["context_window"]))
        for p in profiles.list_profiles(store())
    ]


@router.post("/profiles")
def update_profile(form: ProfileRequest):
    try:
        return profiles.save_profile(store(), form)
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/profiles/{key}/revoke")
def revoke_profile(key: str):
    return profiles.revoke(store(), key)


@router.get("/sessions/{sid}/settings")
def options(sid: str):
    with store().connection() as db:
        session = store().session(sid, db)
        row = db.execute(
            "SELECT document FROM session_options WHERE session=?", (sid,)
        ).fetchone()
    with store().connection() as db:
        connections = {
            r["name"]: json.loads(r["document"])
            for r in db.execute(
                "SELECT name,document FROM mcp_connections WHERE session=?", (sid,)
            )
        }
    catalog = profiles.extension_catalog(session["directory"], store())
    kinds = ("skills", "mcp", "hooks")
    selected = (
        json.loads(row[0])
        if row
        else {"skills": [], "mcp": [], "hooks": list(catalog["hooks"])}
    )
    return {
        "profile_id": session["composer"].get(
            "model_profile_id", session["profile_id"]
        ),
        "catalog": {k: list(catalog[k]) for k in kinds},
        "errors": catalog["errors"],
        "selected": {
            **selected,
            **(
                {"skills": session["composer"]["skills"]}
                if "skills" in session["composer"]
                else {}
            ),
        },
        "connections": connections,
    }


@router.post("/sessions/{sid}/settings")
def set_options(sid: str, form: SessionOptions):
    current = options(sid)
    if form.profile_id and not any(
        p["profile_id"] == form.profile_id for p in all_profiles()
    ):
        raise ValueError("Coding 配置不存在")
    values = form.model_dump(exclude={"profile_id"})
    for kind, names in values.items():
        if len(set(names)) != len(names) or not set(names) <= set(
            current["catalog"][kind]
        ):
            raise ValueError(f"{kind} 包含不可用的扩展")
    with store().connection(True) as db:
        prior = db.execute(
            "SELECT document FROM session_options WHERE session=?", (sid,)
        ).fetchone()
        previous_mcp = json.loads(prior[0]).get("mcp", []) if prior else []
        for name in set(previous_mcp) - set(form.mcp):
            db.execute(
                "INSERT OR REPLACE INTO mcp_connections VALUES(?,?,?)",
                (
                    sid,
                    name,
                    json.dumps({"name": name, "tools": [], "status": "disconnected"}),
                ),
            )
        db.execute(
            "INSERT OR REPLACE INTO session_options VALUES(?,?)",
            (sid, json.dumps(values)),
        )
        session = store().session(sid, db)
        prefs = session["composer"]
        prefs.pop("skills", None)
        if form.profile_id != prefs.get("model_profile_id", session["profile_id"]):
            prefs["model_profile_id"] = (
                form.profile_id or profiles.snapshot("", store().root)["provider"]
            )
            prefs.pop("thinking_level", None)
        db.execute(
            "INSERT OR REPLACE INTO composer_options VALUES(?,?)",
            (sid, json.dumps(prefs)),
        )

    return {"profile_id": form.profile_id, "selected": values}


@router.post("/sessions/{sid}/mcp/{name}/connect")
async def connect(sid: str, name: str):
    tree = location(sid)
    with store().connection() as db:
        session = store().session(sid, db)
    catalog = profiles.extension_catalog(session["directory"], store())
    if name not in catalog["mcp"]:
        raise KeyError(name)
    try:
        tools = await manager().probe(
            tree, tree.verify(session["cwd_relative"]), catalog["mcp"][name]
        )
        result = {
            "name": name,
            "tools": [t["name"] for t in tools],
            "status": "connected",
        }
    except (ValueError, OSError) as exc:
        result = {"name": name, "tools": [], "status": "failed", "error": str(exc)}
    with store().connection(True) as db:
        db.execute(
            "INSERT OR REPLACE INTO mcp_connections VALUES(?,?,?)",
            (sid, name, json.dumps(result)),
        )
    if result["status"] == "failed":
        raise ValueError(result["error"])
    return result
