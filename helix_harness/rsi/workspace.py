"""每个 cell 独享工作树。仅在只读容器内执行仓库代码。"""

import asyncio
import os
from pathlib import Path
import shutil
import tempfile

from mtcode.agent import Agent
from mtcode.permissions import Decision, PermissionChecker
from mtcode.tools.base import ToolResult
from mtcode.tools.bash import Bash

from .fixtures import copy_files, materialize_repo, tree_digest


PUBLIC_TEST = "python -m unittest public_checks"


class CellAgent(Agent):
    # MewCode 的溢写文件放在控制端，避免进入公开仓库和最终产物。
    @property
    def session_dir(self):
        folder = Path(self.work_dir).parent / "spill"
        folder.mkdir(exist_ok=True)
        return folder


class WorkspacePermissions(PermissionChecker):
    def check(self, tool, arguments):
        if tool.name == "Bash":
            return Decision("ask", "公开测试命令需要由宿主授权")
        raw = arguments.get("file_path", arguments.get("path", "."))
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            return Decision("deny", "文件路径无效")
        target = Path(raw).resolve()
        root = self.sandbox.project_root
        if tool.name == "ReadFile" and target.is_relative_to(root.parent / "spill"):
            return Decision("allow", "本次会话的溢写文件")
        if not target.is_relative_to(root) or any(p.casefold() in {".git", ".mewcode"} for p in target.relative_to(root).parts):
            return Decision("deny", "文件路径超出任务范围")
        if tool.name in ("Glob", "Grep"):
            pattern = arguments.get("pattern" if tool.name == "Glob" else "include", "")
            if not isinstance(pattern, str) or Path(pattern).is_absolute() or ".." in Path(pattern).parts:
                return Decision("deny", "搜索范围超出工作树")
            return Decision("allow", "工作树内搜索")
        return super().check(tool, arguments)


async def remove_container(name: str):
    proc = await asyncio.create_subprocess_exec(
        "docker", "rm", "-f", name, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    await asyncio.wait_for(proc.wait(), timeout=10)


class PublicTestTool(Bash):
    description = "Run the repository's public tests using: " + PUBLIC_TEST

    def __init__(self, task: Path, policy: dict, name: str):
        self.task = task
        self.policy = policy
        self.container = name
        self.infrastructure_error = False

    async def execute(self, params):
        if params.command != PUBLIC_TEST:
            return ToolResult("此运行仅允许公开测试命令。", is_error=True)
        p = self.policy
        args = [
            "docker", "run", "--rm", "--pull=never", "--name", self.container,
            "--platform", p["platform"], "--network=none", "--read-only", "--user=65534:65534",
            "--cap-drop=ALL", "--security-opt=no-new-privileges", "--log-driver=none",
            "--memory", f"{p['memory_mb']}m", "--cpus=1", "--pids-limit", str(p["pids_limit"]),
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m",
            "--mount", f"type=bind,source={self.task},target=/task,readonly",
            "--workdir", "/task", "--entrypoint", "python", p["image"], "-IB", "-c",
            "import subprocess,sys; sys.exit(int(subprocess.call([sys.executable,'-B','-m','unittest','public_checks']) != 0))",
        ]
        try:
            proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        except OSError:
            self.infrastructure_error = True
            return ToolResult("公开测试执行后端不可用。", is_error=True)
        output = bytearray()
        try:
            async with asyncio.timeout(min(max(1, params.timeout), p["timeout_seconds"])):
                while chunk := await proc.stdout.read(8192):
                    output.extend(chunk[:max(0, 32000 - len(output))])
                code = await proc.wait()
        except (TimeoutError, asyncio.CancelledError) as exc:
            if proc.returncode is None:
                proc.kill()
            await remove_container(self.container)
            await proc.communicate()
            if isinstance(exc, asyncio.CancelledError):
                raise
            return ToolResult("公开测试超时。", is_error=True)
        if code in (125, 126, 127):
            self.infrastructure_error = True
            return ToolResult("公开测试容器未正常启动。", is_error=True)
        text = output.decode(errors="replace")
        return ToolResult(f"Public test exit code: {code}\n{text}", is_error=code != 0)


def prepare_task(public: Path, task: Path, expected: str):
    materialize_repo(public / "repo", task, expected)
    shutil.rmtree(task / ".git")
    if "," in str(task):
        raise ValueError("容器挂载路径不支持逗号")


def seal_artifact(task: Path, run: Path) -> str:
    with tempfile.TemporaryDirectory(dir=run) as tmp:
        snapshot = Path(tmp) / "snapshot"
        snapshot.mkdir()
        copy_files(task, snapshot)
        fingerprint = tree_digest(snapshot)
        os.rename(snapshot, run / "artifact")
    return fingerprint
