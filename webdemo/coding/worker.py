"""一个活跃 Coding 轮次使用独立进程。私有管道由服务端绑定实例。"""

import asyncio
import json
import os
import select
import sys

from .worktrees import PROJECT

MAX_FRAME = 24 * 1024 * 1024


def search_main():
    import fnmatch
    import re
    from pathlib import Path

    from .worktrees import READ_LIMIT, Worktree

    data = json.load(sys.stdin)
    tree = Worktree(**data["tree"])
    args, tool = data["args"], data["tool"]
    base = tree.resolve(args.get("path", "."), data["cwd"])
    if not base.exists():
        raise ValueError("搜索目录不存在")
    matches, size = [], 0
    regex = re.compile(args["pattern"]) if tool == "Grep" else None
    for name in tree.files():
        path = Path(tree.root) / name
        if not path.is_relative_to(base):
            continue
        if regex is None:
            if fnmatch.fnmatch(
                str(path.relative_to(base)), args["pattern"]
            ) or path.match(args["pattern"]):
                matches.append(name)
                size += len(name)
        elif not args.get("include") or path.match(args["include"]):
            try:
                lines = tree.read(name).decode().splitlines()
            except (ValueError, OSError):
                continue
            for n, line in enumerate(lines, 1):
                if regex.search(line):
                    match = f"{name}:{n}:{line}"
                    matches.append(match)
                    size += len(match)
                    if size >= READ_LIMIT or len(matches) >= 1000:
                        break
        if size >= READ_LIMIT or len(matches) >= 1000:
            break
    print(
        json.dumps(
            {
                "output": "\\n".join(matches)[:READ_LIMIT] or "No matches found.",
                "is_error": False,
                "truncated": size >= READ_LIMIT or len(matches) >= 1000,
            }
        )
    )


async def search(tree, cwd, tool, args):
    from dataclasses import asdict

    entry = "import sys;sys.path.insert(0,sys.argv[1]);from webdemo.coding.worker import search_main;search_main()"
    paths = {
        k: os.environ[k]
        for k in (
            "CODING_WEB_HOME",
            "RSI_WEB_HOME",
            "RSI_ARCHIVE",
            "CODING_PROVIDER_CONFIG",
            "RSI_PROVIDER_CONFIG",
        )
        if k in os.environ
    }
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-B",
        "-c",
        entry,
        str(PROJECT),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env={"PATH": os.defpath, **paths},
    )
    try:
        output, _ = await asyncio.wait_for(
            proc.communicate(
                json.dumps(
                    dict(tree=asdict(tree), cwd=cwd, tool=tool, args=args)
                ).encode()
            ),
            20,
        )
        if proc.returncode:
            raise ValueError("搜索路径或表达式无效")
        return json.loads(output)
    except TimeoutError as exc:
        raise ValueError("搜索超过运行时间") from exc
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


async def start_process():
    entry = (
        "import sys;sys.path.insert(0,sys.argv[1]);"
        "from webdemo.coding.runtime_adapter import main;main()"
    )
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-B",
        "-c",
        entry,
        str(PROJECT),
        cwd=PROJECT,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        limit=MAX_FRAME,
        env={
            "PATH": os.defpath,
            "LANG": "en_US.UTF-8",
            "PYTHONIOENCODING": "utf-8",
            "NO_PROXY": "localhost,127.0.0.1,::1",
        },
        start_new_session=True,
    )


async def send(stream, message):
    data = json.dumps(message, ensure_ascii=False, allow_nan=False).encode() + b"\n"
    if len(data) > MAX_FRAME:
        raise ValueError("会话消息超出管道容量")
    stream.write(data)
    await stream.drain()


class Channel:
    def __init__(self, instance, reader, writer):
        self.instance, self.reader, self.writer = instance, reader, writer
        self.serial = asyncio.Lock()
        self.seq = 0

    async def request(self, kind, **payload):
        async with self.serial:
            self.seq += 1
            message = dict(
                instance=self.instance, seq=self.seq, kind=kind, payload=payload
            )
            data = json.dumps(message, ensure_ascii=False).encode() + b"\n"
            if len(data) > MAX_FRAME:
                raise ValueError("会话消息过大")
            self.writer.write(data)
            self.writer.flush()
            if kind == "ready":
                ready, _, _ = await asyncio.to_thread(
                    select.select, [self.reader], [], [], 2
                )
                if not ready:
                    self.writer.write(data)
                    self.writer.flush()
            while True:
                reply = await asyncio.to_thread(self.reader.readline, MAX_FRAME)
                if not reply:
                    raise ConnectionError("会话管理器已关闭")
                response = json.loads(reply)
                if (
                    response.get("seq") == self.seq
                    or response.get("seq") != 1
                    or self.seq == 1
                ):
                    break
            if response.get("seq") != self.seq:
                raise ConnectionError("无效的执行应答")
            if "error" in response:
                raise ValueError(response["error"])
            return response["result"]
