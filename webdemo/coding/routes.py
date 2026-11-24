"""普通工作树、会话及其只读视图。"""

import asyncio
import json
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from .manager import Manager
from .models import Conflict, PermissionAnswer, SessionRequest, TurnRequest
from .store import Store
from .worktrees import Worktree

router = APIRouter(prefix="/api/coding")


@lru_cache(maxsize=1)
def store():
    return Store()


@lru_cache(maxsize=1)
def manager():
    return Manager(store())


@router.post("/sessions/{sid}/turns", status_code=202)
async def submit(sid: str, form: TurnRequest):
    try:
        return manager().submit(sid, form.submit_request_id, form.text)
    except Conflict as exc:
        raise HTTPException(409, {"message": str(exc), **exc.details}) from exc
    except OSError as exc:
        raise HTTPException(409, "会话工作目录不可用") from exc


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
    with store().connection() as db:
        store().session(sid, db)
        last = db.execute(
            "SELECT coalesce(max(seq),0) FROM events WHERE session=?", (sid,)
        ).fetchone()[0]

    async def stream():
        if (
            owner != sid
            or not number.isdecimal()
            or len(number) > 18
            or int(number) > last
        ):
            yield "event: reset_required\ndata: {}\n\n"
            return
        seq = int(number)
        while not await request.is_disconnected():
            with store().connection() as db:
                rows = store().events(db, sid, seq, 100)
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
    from webdemo import providers

    try:
        _, provider = providers.select()
    except ValueError as exc:
        return {
            "directory": str(Path.cwd()),
            "provider": "",
            "model": "",
            "error": str(exc),
        }
    return {
        "directory": str(Path.cwd()),
        "provider": provider.name,
        "model": provider.model,
    }


@router.post("/sessions", status_code=201)
def create(form: SessionRequest):
    try:
        session = store().create_session(form.directory, form.profile_id)
    except OSError as exc:
        raise HTTPException(422, "请选择可用的 Git 工作目录") from exc
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
        return {"session": s, "turns": turns, "events": events, "through_seq": seq}


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
