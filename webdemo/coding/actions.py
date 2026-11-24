"""工作树动作和本机 Shell 的进程清理边界。"""

from __future__ import annotations

import array
import asyncio
import ctypes
import hashlib
import json
import os
import plistlib
import select
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from mewcode.tools.bash import _interpret_exit_code
from mewcode.tools.edit_file import EditFile
from mewcode.tools.read_file import ReadFile
from mewcode.tools.write_file import WriteFile

from .worktrees import APP_DATA, PROJECT, READ_LIMIT


def content_id(data):
    return hashlib.sha256(data).hexdigest()


@contextmanager
def parent_fd(tree, target, create=False):
    parts = target.relative_to(tree.root).parts
    if not parts:
        raise ValueError("请选择文件")
    fd = os.open(tree.root, os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        ident = os.fstat(fd)
        if (ident.st_dev, ident.st_ino) != (tree.root_dev, tree.root_ino):
            raise ValueError("工作树已更换")
        for part in parts[:-1]:
            if create:
                try:
                    os.mkdir(part, mode=0o755, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd, parts[-1]
    finally:
        os.close(fd)


async def file_action(tree, cwd, tool, args, seen, scratch, guard=None):
    path = tree.resolve(args["file_path"], cwd)
    name = path.relative_to(tree.root).as_posix()
    try:
        before = await asyncio.to_thread(tree.read, name)
    except FileNotFoundError:
        before = None
    signature = content_id(before) if before is not None else None
    if tool != "ReadFile" and (
        (name in seen and seen[name] != signature)
        or (before is not None and name not in seen)
    ):
        return {
            "output": "目标已更改或尚未读取，请重新读取文件",
            "is_error": True,
            "path": name,
            "uncertain": True,
        }
    with tempfile.TemporaryDirectory(dir=scratch) as temp:
        private = Path(temp) / path.name
        if before is not None:
            private.write_bytes(before)
        native = {"ReadFile": ReadFile, "WriteFile": WriteFile, "EditFile": EditFile}[
            tool
        ]()
        params = native.params_model.model_validate(dict(args, file_path=str(private)))
        result = await native.execute(params)
        output = result.output.replace(str(private), name)
        if result.is_error:
            return {"output": output, "is_error": True}
        if tool == "ReadFile":
            seen[name] = signature
            return {
                "output": output,
                "is_error": False,
                "path": name,
                "read_digest": signature,
            }
        if guard:
            guard()
        after = private.read_bytes()
        with parent_fd(tree, path, create=True) as (directory, leaf):
            info = None
            try:
                info = os.stat(leaf, dir_fd=directory, follow_symlinks=False)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or before is None
                    or tree.read(name) != before
                ):
                    raise ValueError("目标已更改，请重新读取")
            except FileNotFoundError:
                if before is not None:
                    raise ValueError("目标已删除，请重新读取")
            tmp = ".mewcode-write-" + uuid4().hex
            fd = os.open(
                tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=directory
            )
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(after)
                    os.fchmod(
                        stream.fileno(), stat.S_IMODE(info.st_mode) if info else 0o644
                    )
                if info is None:
                    os.link(tmp, leaf, src_dir_fd=directory, dst_dir_fd=directory)
                else:
                    current = os.stat(leaf, dir_fd=directory, follow_symlinks=False)
                    if (current.st_ino, current.st_mtime_ns, current.st_size) != (
                        info.st_ino,
                        info.st_mtime_ns,
                        info.st_size,
                    ):
                        raise ValueError("文件已被外部修改，请重新读取")
                    os.replace(tmp, leaf, src_dir_fd=directory, dst_dir_fd=directory)
            finally:
                try:
                    os.unlink(tmp, dir_fd=directory)
                except FileNotFoundError:
                    pass
    seen[name] = content_id(after)
    return {
        "output": output,
        "is_error": False,
        "path": name,
        "before": signature,
        "after": seen[name],
    }


def shell_profile(tree, temp, state):
    quote = lambda p: json.dumps(str(p), ensure_ascii=False)
    roots = [
        "/System",
        "/Library/Apple",
        "/Library/Frameworks",
        "/Library/Developer",
        "/Applications/Xcode.app/Contents/Developer",
        sys.base_prefix,
        "/usr",
        "/bin",
        "/sbin",
        "/opt/homebrew",
        "/private/etc",
        "/dev",
        tree.root,
        temp,
    ]
    rules = [
        "(version 1)",
        "(deny default)",
        "(allow process-exec process-fork sysctl-read)",
        "(allow file-read-metadata)",
        '(allow file-read-data (literal "/"))',
        '(allow file-write* (literal "/dev/null"))',
    ]
    rules.extend(f"(allow file-read* (subpath {quote(p)}))" for p in roots)
    rules.extend(f"(allow file-write* (subpath {quote(p)}))" for p in (tree.root, temp))
    for path in (
        APP_DATA,
        state,
        *([tree.admin] if tree.admin else []),
        PROJECT / "evaluation/repo-python-v1/private",
        *[
            Path(os.environ[k]).expanduser().resolve()
            for k in (
                "RSI_ARCHIVE",
                "RSI_WEB_HOME",
                "RSI_PROVIDER_CONFIG",
                "CODING_PROVIDER_CONFIG",
            )
            if os.getenv(k)
        ],
    ):
        rules.append(f"(deny file-read* file-write* (subpath {quote(path)}))")
    rules.append(
        r'(deny file-read* file-write* (regex #"/([.]git|[.]mewcode|evaluator-private)(/|$)") (regex #"/[.]env([.]|/|$)"))'
    )
    return "\n".join(rules)


def shell_child(directory):
    """launchd 独立审计会话。先交付内核结束通知 FD，收到许可后才执行命令。"""
    import struct

    spec = json.loads((directory / "request.json").read_text())
    libc = ctypes.CDLL(None, use_errno=True)
    audit = ctypes.create_string_buffer(48)
    if libc.getaudit_addr(audit, len(audit)) != 0:
        raise OSError(ctypes.get_errno(), "无法读取审计会话")
    sid = struct.unpack_from("=i", audit.raw, 36)[0]
    bsm = ctypes.CDLL("/usr/lib/libbsm.dylib", use_errno=True)
    bsm.au_sdev_open.restype = ctypes.c_void_p
    handle = bsm.au_sdev_open(1)
    if not handle:
        raise OSError(ctypes.get_errno(), "无法监督审计会话")
    try:
        monitor = bsm.au_sdev_fd(ctypes.c_void_p(handle))
        with socket.socket(socket.AF_UNIX) as conn:
            conn.connect(spec["socket"])
            identity = {
                "audit_session": sid,
                "launcher_pid": os.getpid(),
                "process_group": os.getpgrp(),
            }
            conn.sendmsg(
                [json.dumps(identity).encode()],
                [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [monitor]))],
            )
            if conn.recv(1) != b"G":
                return
    finally:
        bsm.au_sdev_close(ctypes.c_void_p(handle))
    with (directory / "output").open("wb", buffering=0) as output:
        command = [
            "/usr/bin/sandbox-exec",
            "-p",
            spec["profile"],
            "/bin/bash",
            "--noprofile",
            "--norc",
            "-c",
            spec["command"],
        ]
        data = None
        if "bridge" in spec:
            command = [
                sys.executable,
                "-I",
                "-B",
                "-c",
                "import sys;sys.path.insert(0,sys.argv[1]);from webdemo.coding.mcp_bridge import main;main()",
                str(PROJECT),
            ]
            data = json.dumps(
                dict(spec["bridge"], cwd=spec["cwd"], profile=spec["profile"])
            ).encode()
        result = subprocess.Popen(
            command,
            env=spec["env"],
            cwd=spec["cwd"],
            stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL if data is not None else subprocess.STDOUT,
        )
        if data is not None:
            result.stdin.write(data)
            result.stdin.close()
        retained = 0
        with result.stdout as pipe:
            while chunk := os.read(pipe.fileno(), 65536):
                if retained <= READ_LIMIT:
                    part = chunk[: READ_LIMIT + 1 - retained]
                    output.write(part)
                    retained += len(part)
        result.wait()
    tmp = directory / "exit.tmp"
    tmp.write_text(str(result.returncode))
    tmp.replace(directory / "exit")


def ended(fd, pending):
    # BSM 记录以令牌、网络序长度、版本、事件号开头。设备限定在工具自己的审计会话。
    while select.select([fd], [], [], 0)[0]:
        block = os.read(fd, 65536)
        if not block:
            raise RuntimeError("内核进程监督通道失联")
        pending.extend(block)
        while len(pending) >= 8:
            size = int.from_bytes(pending[1:5], "big")
            if size < 8 or size > 4 * 1024 * 1024:
                raise RuntimeError("审计事件格式无效")
            if len(pending) < size:
                break
            kind = int.from_bytes(pending[6:8], "big")
            del pending[:size]
            if kind == 44903:  # AUE_SESSION_END：此会话最后一个进程已退出
                return True
    return False


def witness(fd, owner, directory):
    """独立于 Web 服务持有内核通知。写命令只能在监督者启动后收到 G。"""
    label = directory.name
    watched = [fd, owner]
    pending = bytearray()
    while not ended(fd, pending):
        ready, _, _ = select.select(watched, [], [], 1)
        if owner in ready and not os.read(owner, 1):
            watched.remove(owner)
            subprocess.run(
                ["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{label}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
    tmp = directory / "clean.tmp"
    tmp.write_text(label)
    tmp.replace(directory / "clean")


async def launchctl(*args, check=True):
    proc = await asyncio.create_subprocess_exec(
        "/bin/launchctl",
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if check and proc.returncode:
        raise RuntimeError("无法启动受管命令：" + err.decode(errors="replace")[:300])


def clear_command_files(directory):
    """保留内核清理回执，直至对应退出事件提交。"""
    for entry in directory.iterdir():
        if entry.name == "clean":
            continue
        if entry.is_dir() and not entry.is_symlink():
            # 工作进程可能在自己的临时目录中移除用户写权限。
            for root, dirs, _files in os.walk(entry, topdown=True, followlinks=False):
                Path(root).chmod(0o700)
                for subdir in dirs:
                    child = Path(root) / subdir
                    if not child.is_symlink():
                        child.chmod(0o700)
            shutil.rmtree(entry)
        else:
            entry.unlink(missing_ok=True)


async def run_shell(
    tree, cwd, command, timeout, state, notify, *, stop=None, bridge=None
):
    if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").is_file():
        raise ValueError("当前系统缺少已验证的 Coding Shell 沙箱")
    domain = "gui/" + str(os.getuid())
    label = "local.mewcode.coding." + uuid4().hex
    directory = state / "commands" / label
    directory.mkdir(parents=True, mode=0o700)
    scratch = directory / "tmp"
    scratch.mkdir(mode=0o700)
    # Darwin 的 Unix socket 路径较短，握手目录与命令可读写目录分开。
    with (
        tempfile.TemporaryDirectory(prefix="mc-", dir="/tmp") as handshake,
        socket.socket(socket.AF_UNIX) as listener,
    ):
        address = str(Path(handshake) / "s")
        listener.bind(address)
        listener.listen(1)
        listener.settimeout(15)
        spec = {
            "command": command,
            "cwd": str(cwd),
            "profile": shell_profile(tree, scratch, state),
            "socket": address,
            "env": {
                "PATH": f"{sys.base_prefix}/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin",
                "HOME": str(scratch),
                "TMPDIR": str(scratch),
                "LANG": "en_US.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        }
        # scratch 在私有状态目录下。只向命令重新授予这一小块临时空间。
        escaped = json.dumps(str(scratch), ensure_ascii=False)
        spec["profile"] += f"\n(allow file-read* file-write* (subpath {escaped}))"
        if bridge:
            spec["bridge"] = bridge
            if bridge["operation"] == "discover":
                spec["profile"] += (
                    "\n(deny file-write* (subpath " + json.dumps(str(tree.root)) + "))"
                )
        (directory / "request.json").write_text(json.dumps(spec))
        entry = "import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from webdemo.coding.actions import shell_child;shell_child(Path(sys.argv[2]))"
        job = {
            "Label": label,
            "ProgramArguments": [
                sys.executable,
                "-I",
                "-B",
                "-c",
                entry,
                str(PROJECT),
                str(directory),
            ],
            "RunAtLoad": True,
            "SessionCreate": True,
            "AbandonProcessGroup": False,
        }
        plist = directory / "job.plist"
        plist.write_bytes(plistlib.dumps(job))
        monitor = None
        loaded = False
        observer = None
        owner_write = None
        try:
            await launchctl("bootstrap", domain, str(plist))
            loaded = True
            conn, _ = await asyncio.to_thread(listener.accept)
            with conn:
                conn.settimeout(10)
                data, ancillary, _, _ = await asyncio.to_thread(
                    conn.recvmsg, 256, socket.CMSG_SPACE(4)
                )
                if not ancillary or ancillary[0][:2] != (
                    socket.SOL_SOCKET,
                    socket.SCM_RIGHTS,
                ):
                    raise RuntimeError("缺少子进程监督凭据")
                monitor = array.array("i", ancillary[0][2][:4])[0]
                owner_read, owner_write = os.pipe()
                try:
                    observer = await asyncio.create_subprocess_exec(
                        sys.executable,
                        "-I",
                        "-B",
                        "-c",
                        "import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from webdemo.coding.actions import witness;witness(int(sys.argv[2]),int(sys.argv[3]),Path(sys.argv[4]))",
                        str(PROJECT),
                        str(monitor),
                        str(owner_read),
                        str(directory),
                        pass_fds=(monitor, owner_read),
                        start_new_session=True,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        env={"PATH": os.defpath},
                    )
                finally:
                    os.close(owner_read)
                await notify(
                    "process_started",
                    {
                        "job": label,
                        **json.loads(data),
                        "cwd": str(cwd),
                        "command": command,
                    },
                )
                conn.sendall(b"G")
            deadline = time.monotonic() + timeout
            pos, truncated = 0, False
            output_path = directory / "output"

            async def drain():
                nonlocal pos, truncated
                if output_path.exists() and pos < READ_LIMIT:
                    with output_path.open("rb") as stream:
                        stream.seek(pos)
                        data = stream.read(min(65536, READ_LIMIT - pos))
                    if data:
                        pos += len(data)
                        if not bridge:
                            await notify(
                                "output", {"text": data.decode(errors="replace")}
                            )
                if (
                    output_path.exists()
                    and output_path.stat().st_size > READ_LIMIT
                    and not truncated
                ):
                    truncated = True
                    await notify("output", {"truncated": True})

            timed_out = False
            while not (directory / "clean").exists():
                if (
                    observer.returncode is not None
                    and not (directory / "clean").exists()
                ):
                    raise RuntimeError("内核清理监督通道失联")
                await drain()
                if (
                    time.monotonic() > deadline or (stop and stop.is_set())
                ) and not timed_out:
                    await launchctl("bootout", domain + "/" + label, check=False)
                    loaded = False
                    timed_out = True
                    await notify("cleaning", {"reason": "等待本轮所有写进程退出"})
                await asyncio.sleep(0.08)
            while output_path.exists() and pos < min(
                READ_LIMIT, output_path.stat().st_size
            ):
                await drain()
            exitfile = directory / "exit"
            code = int(exitfile.read_text()) if exitfile.exists() else None

            def read_output():
                if not output_path.is_file():
                    return ""
                with output_path.open("rb") as stream:
                    return stream.read(READ_LIMIT).decode(errors="replace")

            message = await asyncio.to_thread(read_output)
            await asyncio.to_thread(clear_command_files, directory)
            await notify(
                "process_exited",
                {"job": label, "exit_code": code, "timed_out": timed_out},
            )
            shutil.rmtree(directory, ignore_errors=True)
            return {
                "output": message or ("(no output)" if code == 0 else "命令未正常结束"),
                "is_error": code is None
                or _interpret_exit_code(command, code)
                or timed_out,
                "exit_code": code,
                "truncated": truncated,
            }
        finally:
            if loaded:
                await launchctl("bootout", domain + "/" + label, check=False)
            if monitor is not None:
                os.close(monitor)
            if owner_write is not None:
                os.close(owner_write)
            if (directory / "clean").is_file():
                if observer:
                    await observer.wait()
            elif observer is None:
                shutil.rmtree(directory, ignore_errors=True)
            if (directory / "clean").is_file():
                clear_command_files(directory)
