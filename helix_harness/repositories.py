"""Import an explicitly operator-published, committed public Python project."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid

from mewcode.rsi.fixtures import materialize_repo, tree_digest
from .paths import PROJECT
from .store import HOME, ROOT, db


def register(name):
    root = Path(os.getenv("RSI_PUBLIC_REPOS", HOME / "public-repos")).resolve()
    project = (root / name).resolve()
    if (
        not re.fullmatch("[a-zA-Z0-9_-]{1,64}", name)
        or project.parent != root
        or not (project / ".git").exists()
    ):
        raise ValueError("Choose a Git project directly under RSI_PUBLIC_REPOS")
    if any(project.is_relative_to(p) or p.is_relative_to(project) for p in (PROJECT, ROOT)):
        raise ValueError("Engine and evaluation sources are protected")

    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(project), *args], timeout=10, stderr=subprocess.DEVNULL
        )

    commit = git("rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    entries = git("ls-tree", "-rz", "-l", commit).split(b"\0")
    id = "repo-" + uuid.uuid4().hex[:12]
    fixture = HOME / "repositories" / id
    folder = fixture / "repo"
    folder.mkdir(parents=True)
    total = 0
    try:
        for row in filter(None, entries):
            meta, raw = row.split(b"\t", 1)
            mode, kind, key, size = meta.split()
            path = Path(raw.decode())
            if path.is_absolute() or any(
                p.startswith(".") or p == ".." for p in path.parts
            ):
                continue
            if mode not in (b"100644", b"100755") or kind != b"blob":
                raise ValueError("Only regular files are supported")
            if path.suffix.lower() not in {
                ".py",
                ".txt",
                ".md",
                ".toml",
                ".json",
                ".yaml",
                ".yml",
            }:
                continue
            if any(
                re.search(r"(secret|credential|api[_-]?key|password)", p, re.I)
                for p in path.parts
            ):
                continue
            total += int(size)
            if int(size) > 65536 or total > 1024 * 1024:
                raise ValueError("Public project exceeds import limit")
            dest = folder / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(git("cat-file", "blob", key.decode()))
        if not (folder / "public_checks.py").is_file():
            raise ValueError("Published project needs public_checks.py")
        with tempfile.TemporaryDirectory() as tmp:
            pin = materialize_repo(folder, Path(tmp) / "repo")
        (fixture / "pin.json").write_text(
            json.dumps({"commit": pin, "digest": tree_digest(folder)})
        )
        with db() as con:
            con.execute(
                "INSERT OR IGNORE INTO repos VALUES(?,?,?,?)",
                (id, name, commit, str(fixture)),
            )
            stored = con.execute(
                "SELECT id FROM repos WHERE name=? AND commit_id=?", (name, commit)
            ).fetchone()[0]
        if stored != id:
            shutil.rmtree(fixture)
            id = stored
        return {"id": id, "name": name, "commit": commit}
    except BaseException:
        shutil.rmtree(fixture)
        raise
