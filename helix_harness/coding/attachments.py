"""Immutable session-scoped input materials; only metadata enters public events."""

import hashlib
import io
import time

from PIL import Image, UnidentifiedImageError

from .models import Conflict

MAX_FILE = 4 * 1024 * 1024
MAX_TOTAL = 8 * 1024 * 1024
MAX_TEXT = 64 * 1024
MAX_COUNT = 8


def classify(data):
    try:
        with Image.open(io.BytesIO(data)) as image:
            mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}.get(
                image.format
            )
            if not mime or image.width * image.height > 16_000_000:
                raise ValueError("请选择 PNG、JPEG 或 WebP 图片（最多 1600 万像素）")
            image.verify()
            return mime
    except UnidentifiedImageError:
        pass
    except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError("图片损坏或过大") from exc
    if len(data) > MAX_TEXT:
        raise ValueError("文本附件不能超过 64 KiB")
    try:
        text = data.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError("请选择 UTF-8 文本或支持的图片格式") from exc
    if any(ord(c) < 32 and c not in "\t\n\r" for c in text):
        raise ValueError("不支持该二进制附件")
    return "text/plain"


def metadata(row):
    return {key: row[key] for key in ("id", "name", "mime", "size")}


def stage(store, sid, aid, name, data):
    if not name or len(name) > 512 or any(ord(c) < 32 for c in name):
        raise ValueError("附件名称无效")
    if not data or len(data) > MAX_FILE:
        raise ValueError("附件为空或超过 4 MiB")
    mime = classify(data)
    checksum = hashlib.sha256(data).hexdigest()
    with store.connection(True) as db:
        store.session(sid, db)
        db.execute(
            "DELETE FROM attachments WHERE used=0 AND created<?",
            (time.time() - 7 * 86400,),
        )
        row = db.execute(
            "SELECT * FROM attachments WHERE session=? AND id=?", (sid, aid)
        ).fetchone()
        if row:
            if row["digest"] != checksum or row["name"] != name:
                raise Conflict("此附件编号已对应其他内容")
            return metadata(row)
        size = db.execute(
            "SELECT coalesce(sum(size),0) FROM attachments WHERE session=? AND used=0",
            (sid,),
        ).fetchone()[0]
        if size + len(data) > 4 * MAX_TOTAL:
            raise ValueError("未发送的附件已达会话暂存上限")
        db.execute(
            "INSERT INTO attachments VALUES(?,?,?,?,?,?,?,?,0)",
            (sid, aid, name, mime, len(data), checksum, data, time.time()),
        )
    return {"id": aid, "name": name, "mime": mime, "size": len(data)}


def bind(db, sid, ids, vision):
    if len(ids) > MAX_COUNT or len(set(ids)) != len(ids):
        raise ValueError("附件数量超限或重复")
    result = []
    for aid in ids:
        row = db.execute(
            "SELECT id,name,mime,size FROM attachments WHERE session=? AND id=?",
            (sid, aid),
        ).fetchone()
        if not row:
            raise ValueError("此会话的附件不可用，请重新选择")
        if row["mime"].startswith("image/") and not vision:
            raise ValueError("所选模型未启用图片输入，请切换模型或移除图片")
        result.append(metadata(row))
    if (
        sum(r["size"] for r in result) > MAX_TOTAL
        or sum(r["size"] for r in result if r["mime"] == "text/plain") > 2 * MAX_TEXT
    ):
        raise ValueError("每轮附件最多 8 MiB，其中文本合计最多 128 KiB")
    db.executemany(
        "UPDATE attachments SET used=1 WHERE session=? AND id=?",
        ((sid, aid) for aid in ids),
    )
    return result


def hydrate(message, source, data, snapshot):
    """Materialize immutable session inputs only for the retained native history."""
    import base64
    import sqlite3
    from contextlib import closing
    from pathlib import Path

    message.web_input = source
    parts = []
    text = [source["text"]] if source["text"] else []
    if text:
        parts.append(text[0])
    with closing(
        sqlite3.connect(Path(data["attachment_db"]).as_uri() + "?mode=ro", uri=True)
    ) as db:
        for aid in source["ids"]:
            row = db.execute(
                "SELECT name,mime,data FROM attachments WHERE session=? AND id=? AND used=1",
                (data["session_id"], aid),
            ).fetchone()
            if row is None:
                raise ValueError("历史附件不可用")
            name, mime, contents = row
            if mime == "text/plain":
                text.append(f"File: {name}\n{contents.decode('utf-8')}")
            else:
                suffix = (
                    ""
                    if snapshot.get("vision")
                    else " (image omitted for this text model)"
                )
                text.append(f"Image: {name}{suffix}")
            parts.append(text[-1])
            if mime.startswith("image/") and snapshot.get("vision"):
                parts.append((mime, base64.b64encode(contents).decode("ascii")))
    message.content = "\n\n".join(text)
    if any(isinstance(p, tuple) for p in parts):
        message.web_parts = parts
