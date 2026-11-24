"""普通会话的 Git 工作树身份和只读文件视图。"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from difflib import unified_diff
from pathlib import Path

READ_LIMIT = 2 * 1024 * 1024
APP_DATA = Path.home() / "Library/Application Support/MewCode"
PROJECT = Path(__file__).resolve().parents[2]


def git(directory, *args, check=True):
    result = subprocess.run(
        [
            "/usr/bin/git",
            "--no-optional-locks",
            "--literal-pathspecs",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.untrackedCache=false",
            "-C",
            str(directory),
            *args,
        ],
        env={
            "PATH": os.defpath,
            "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        },
        check=False,
        capture_output=True,
        timeout=20,
    )
    if check and result.returncode:
        raise ValueError("Git 工作树或对象不可用")
    return result


@dataclass(frozen=True)
class Worktree:
    root: str
    admin: str
    root_dev: int
    root_ino: int
    admin_dev: int
    admin_ino: int

    @classmethod
    def identify(cls, cwd):
        directory = Path(cwd).expanduser().resolve(strict=True)
        if not directory.is_dir():
            raise ValueError("请选择本地 Git 工作目录")
        values = git(
            directory, "rev-parse", "--show-toplevel", "--absolute-git-dir"
        ).stdout.splitlines()
        if len(values) != 2:
            raise ValueError("无法识别 Git 工作树")
        root, admin = (Path(os.fsdecode(x)).resolve(strict=True) for x in values)
        root_stat, admin_stat = root.stat(), admin.stat()
        result = cls(
            str(root),
            str(admin),
            root_stat.st_dev,
            root_stat.st_ino,
            admin_stat.st_dev,
            admin_stat.st_ino,
        )
        result.allowed(directory)
        return result, directory.relative_to(root).as_posix()

    def verify(self, relative="."):
        tree, actual = self.identify(Path(self.root) / relative)
        if tree != self or actual != relative:
            raise ValueError("工作目录已移动或被替换")
        return Path(self.root) / relative

    def allowed(self, path):
        root = Path(self.root)
        target = Path(path).resolve(strict=False)
        if not target.is_relative_to(root):
            raise ValueError("路径超出工作树范围")
        protected = [
            APP_DATA,
            Path(self.admin),
            Path.home() / ".mewcode",
            PROJECT / "evaluation" / "repo-python-v1" / "private",
        ]
        for key in (
            "CODING_WEB_HOME",
            "RSI_WEB_HOME",
            "RSI_ARCHIVE",
            "CODING_PROVIDER_CONFIG",
            "RSI_PROVIDER_CONFIG",
        ):
            if os.getenv(key):
                protected.append(Path(os.environ[key]).expanduser().resolve())
        if any(target.is_relative_to(p) for p in protected):
            raise ValueError("受保护的应用数据")
        if any(
            p in (".git", ".mewcode", "evaluator-private")
            or p == ".env"
            or p.startswith(".env.")
            for p in target.relative_to(root).parts
        ):
            raise ValueError("受保护的配置或材料")
        return target

    def resolve(self, name, cwd="."):
        return self.allowed(Path(self.root) / cwd / name)

    @contextmanager
    def open_file(self, name, cwd="."):
        target = self.resolve(name, cwd)
        parts = target.relative_to(self.root).parts
        if not parts:
            raise ValueError("请选择普通文件")
        fd = os.open(self.root, os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) != (self.root_dev, self.root_ino):
                raise ValueError("工作树已被替换")
            for part in parts[:-1]:
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                )
                os.close(fd)
                fd = child
            file_fd = os.open(
                parts[-1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=fd
            )
            with os.fdopen(file_fd, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("需要常规文件")
                yield stream
        finally:
            os.close(fd)

    def read(self, name, cwd="."):
        with self.open_file(name, cwd) as stream:
            data = stream.read(READ_LIMIT + 1)
        if len(data) > READ_LIMIT:
            raise ValueError("文件过大，无法预览")
        return data

    def paths(self):
        result = git(
            self.root, "ls-files", "--cached", "--others", "--exclude-standard", "-z"
        )
        for entry in sorted(set(result.stdout.split(b"\0")) - {b""}):
            name = os.fsdecode(entry)
            try:
                self.resolve(name)
            except (ValueError, OSError, RuntimeError):
                continue
            yield name

    def files(self, query="", content=False):
        found = []
        for name in self.paths():
            if not self.resolve(name).is_file():
                continue
            if content and query:
                try:
                    if query not in self.read(name).decode("utf-8"):
                        continue
                except (ValueError, OSError):
                    continue
            elif query.casefold() not in name.casefold():
                continue
            found.append(name)
        return found

    def file_stamps(self):
        stamps = {}
        for name in self.paths():
            try:
                with self.open_file(name) as f:
                    row = os.fstat(f.fileno())
                stamps[name] = (
                    row.st_dev,
                    row.st_ino,
                    row.st_size,
                    row.st_mtime_ns,
                    row.st_mode,
                )
            except (ValueError, OSError):
                continue
        return stamps

    def changes(self, selected=None):
        index = {}
        for record in git(self.root, "ls-files", "--stage", "-z").stdout.split(b"\0"):
            if record:
                meta, path = record.split(b"\t", 1)
                mode, oid, stage = meta.decode().split()
                index[os.fsdecode(path)] = (mode, oid, stage)
        head = {}
        if git(self.root, "rev-parse", "--verify", "HEAD", check=False).returncode == 0:
            for record in git(self.root, "ls-tree", "-rz", "HEAD").stdout.split(b"\0"):
                if record:
                    meta, path = record.split(b"\t", 1)
                    mode, _kind, oid = meta.decode().split()
                    head[os.fsdecode(path)] = (mode, oid, "0")
        names = set(self.paths()) | set(head)
        result = []
        for name in sorted(names if selected is None else names & {selected}):
            try:
                target = self.resolve(name)
            except (OSError, ValueError, RuntimeError):
                continue
            original, staged = head.get(name), index.get(name)
            if target.is_symlink() or (Path(self.root) / name).is_symlink():
                working = os.fsencode(os.readlink(Path(self.root) / name))
                mode = "120000"
            elif target.is_file():
                with self.open_file(name) as stream:
                    info = os.fstat(stream.fileno())
                    hashfn = (
                        hashlib.sha256
                        if len((staged or original or ("", "", ""))[1]) == 64
                        else hashlib.sha1
                    )
                    h = hashfn(f"blob {info.st_size}\0".encode())
                    while block := stream.read(65536):
                        h.update(block)
                oid = h.hexdigest()
                mode = "100755" if info.st_mode & 0o111 else "100644"
                working = None
            elif target.is_dir():
                continue
            else:
                working, mode = None, None
            if mode == "120000":
                hashfn = (
                    hashlib.sha256
                    if len((staged or original or ("", "", ""))[1]) == 64
                    else hashlib.sha1
                )
                oid = hashfn(f"blob {len(working)}\0".encode() + working).hexdigest()
            disk = (mode, oid, "0") if mode else None

            def status(old, new):
                if old == new:
                    return " "
                return "A" if old is None else "D" if new is None else "M"

            x, y = status(original, staged), status(staged, disk)
            if (x, y) == (" ", " "):
                continue
            row = {
                "path": name,
                "status": "??" if original is None and staged is None else x + y,
            }
            if selected is not None:

                def blob(meta):
                    if not meta:
                        return b""
                    if (
                        int(git(self.root, "cat-file", "-s", meta[1]).stdout)
                        > READ_LIMIT
                    ):
                        raise ValueError("文件过大，无法预览差异")
                    return git(self.root, "cat-file", "blob", meta[1]).stdout

                def diff(a, b, name=name):
                    lines = lambda data: data.decode("utf-8", "replace").splitlines(
                        keepends=True
                    )
                    if a == b:
                        return ""
                    lines_with_eol = []
                    for line in unified_diff(
                        lines(a), lines(b), "a/" + name, "b/" + name
                    ):
                        lines_with_eol.append(
                            line
                            if line.endswith("\n")
                            else line + "\n\\ No newline at end of file\n"
                        )
                    return f"diff --git a/{name} b/{name}\n" + "".join(lines_with_eol)

                raw = self.read(name) if mode and working is None else working or b""
                indexed = blob(staged)
                row.update(
                    staged=diff(blob(original), indexed), working=diff(indexed, raw)
                )
            result.append(row)
        return result
