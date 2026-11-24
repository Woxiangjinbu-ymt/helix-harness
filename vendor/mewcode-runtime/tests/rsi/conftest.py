import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def release_copy(tmp_path):
    for name in ("evaluation", "mewcode"):
        shutil.copytree(ROOT / name, tmp_path / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copyfile(ROOT / name, tmp_path / name)
    return tmp_path


@pytest.fixture
def docker_policy():
    policy = json.loads((ROOT / "evaluation/policy/docker.json").read_text())
    if not shutil.which("docker") or subprocess.run(["docker", "image", "inspect", policy["image"]],
                                                    capture_output=True, timeout=15).returncode:
        pytest.skip("Docker 集成测试需要第 1 天冻结的本地镜像")
    return policy
