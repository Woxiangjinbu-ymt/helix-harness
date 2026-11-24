"""受信端从公开文件独立构造仓库，并核对冻结的输入。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path

from .contracts import BenchmarkPin, CasePin, digest


BENCHMARK = "repo-python-v1"


def file_digest(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"需要常规文件：{path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def regular_files(folder: Path) -> Iterator[Path]:
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError(f"需要常规目录：{folder}")
    for entry in sorted(folder.iterdir()):
        if entry.is_symlink() or entry.name.casefold() == ".git":
            raise ValueError(f"禁止作为评测输入：{entry}")
        if entry.is_dir():
            yield from regular_files(entry)
        elif entry.is_file():
            yield entry
        else:
            raise ValueError(f"非常规评测文件：{entry}")


def tree_digest(folder: Path) -> str:
    return digest({p.relative_to(folder).as_posix(): file_digest(p) for p in regular_files(folder)})


def copy_files(source: Path, dest: Path) -> None:
    for file in regular_files(source):
        target = dest / file.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(file.read_bytes())
        target.chmod(0o644)


def materialize_repo(source: Path, dest: Path, expected: str | None = None) -> str:
    if dest.resolve().is_relative_to(source.resolve()):
        raise ValueError("目标仓库位于输入目录内")
    # 独立 Git 配置、固定提交时间和文件模式让不同机器得到同一提交。
    env = {
        "PATH": os.environ["PATH"], "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_ATTR_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "MewCode Fixtures",
        "GIT_AUTHOR_EMAIL": "fixtures@example.invalid",
        "GIT_COMMITTER_NAME": "MewCode Fixtures", "GIT_COMMITTER_EMAIL": "fixtures@example.invalid",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
    }
    dest.mkdir()
    try:
        def git(*args: str) -> str:
            proc = subprocess.run(
                ["git", "-c", f"core.hooksPath={os.devnull}", *args],
                cwd=dest, env=env, capture_output=True, text=True, timeout=15,
            )
            if proc.returncode:
                raise ValueError(f"构建公开仓库失败：{proc.stderr.strip()}")
            return proc.stdout.strip()

        copy_files(source, dest)
        git("init", "--quiet", "--template=", "--object-format=sha1", "-b", "main")
        git("add", "--force", "--", ".")
        git("commit", "--quiet", "-m", "Initial public task")
        head = git("rev-parse", "HEAD")
        if expected is not None and head != expected:
            raise ValueError(f"起始提交漂移：{head} != {expected}")
        return head
    except BaseException:
        shutil.rmtree(dest)
        raise


def freeze_inputs(root: Path) -> dict:
    bench = root / "evaluation" / BENCHMARK
    for protected in (root / "evaluation", bench, root / "evaluation/policy", root / "evaluation/strategies"):
        if protected.is_symlink():
            raise ValueError(f"禁止使用链接作为受保护目录：{protected}")
    manifest_path = bench / "benchmark.json"
    manifest = json.loads(manifest_path.read_text())
    ids = manifest["cases"]
    if (manifest["id"] != BENCHMARK or manifest["schema_version"] != 1 or not ids
            or len(ids) != len(set(ids)) or not all(re.fullmatch(r"[a-z0-9-]+", i) for i in ids)):
        raise ValueError("Benchmark 清单无效")
    # 拒绝目录链接及未声明的文件，避免发布清单之外的材料混入。
    def check_entries(folder: Path, expected: set[str]) -> None:
        entries = list(folder.iterdir())
        if folder.is_symlink() or any(p.is_symlink() for p in entries):
            raise ValueError(f"禁止使用链接：{folder}")
        if {p.name for p in entries} != expected:
            raise ValueError(f"{folder.name} 目录和清单不一致")

    for area in ("public", "private"):
        check_entries(bench / area, set(ids))
    cases = []
    references = {}
    with tempfile.TemporaryDirectory() as tmp:
        for case_id in ids:
            public = bench / "public" / case_id
            private = bench / "private" / case_id
            check_entries(public, {"statement.md", "repo"})
            check_entries(private, {"checks.json", "reference"})
            head = materialize_repo(public / "repo", Path(tmp) / case_id)
            cases.append(CasePin(case_id, head, file_digest(public / "statement.md"),
                                 file_digest(private / "checks.json")))
            references[case_id] = tree_digest(private / "reference")
    benchmark = BenchmarkPin(BENCHMARK, file_digest(manifest_path), tuple(cases))
    package = root / "mewcode"
    # 运行包内容和依赖锁均影响模型请求与工具行为。排除生成的字节码。
    runtime_files = {
        p.relative_to(root).as_posix(): file_digest(p) for p in sorted(package.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc" and p.name != ".DS_Store"
        and not p.is_relative_to(package / "rsi")
    }
    for name in ("pyproject.toml", "uv.lock"):
        runtime_files[name] = file_digest(root / name)
    scoring_code = {name: file_digest(package / "rsi" / name)
                    for name in ("__init__.py", "contracts.py", "fixtures.py", "preflight.py")}
    return {
        "schema_version": 1, "benchmark": asdict(benchmark), "benchmark_digest": benchmark.fingerprint,
        "runtime_digest": digest(runtime_files), "evaluator_digest": digest(scoring_code),
        "policy_digest": file_digest(root / "evaluation/policy/docker.json"),
        "initial_strategy_digest": tree_digest(root / "evaluation/strategies/default-v1"),
        "calibration_reference_digests": references,
    }


def verify_lock(root: Path) -> dict:
    actual = freeze_inputs(root)
    expected = json.loads((root / "evaluation" / BENCHMARK / "freeze.lock.json").read_text())
    if digest(actual) != digest(expected):
        keys = sorted(k for k in actual.keys() | expected.keys() if digest(actual.get(k)) != digest(expected.get(k)))
        raise ValueError("冻结清单不匹配：" + ", ".join(keys))
    return expected
