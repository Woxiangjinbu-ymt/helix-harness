"""宿主保留判分规则，每项私测只向隔离进程发送该项输入。"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import uuid

from .contracts import digest

__test__ = False
OUTPUT_LIMIT = 64 * 1024


class JudgeUnavailable(RuntimeError):
    """评分基础设施未能生成有效观测。"""


async def docker_control(*args: str) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        output, _ = await asyncio.wait_for(proc.communicate(), 15)
    except BaseException:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        raise
    if proc.returncode:
        raise JudgeUnavailable(f"Docker 控制操作失败：{args[0]}")
    return output


@dataclass(frozen=True)
class CheckResult:
    reason: str
    exit_code: int | None

    @property
    def passed(self):
        return self.reason == "passed"


@dataclass(frozen=True)
class Grade:
    checks: tuple[CheckResult, ...]

    @property
    def score(self):
        return round(100 * sum(c.passed for c in self.checks) / len(self.checks), 2)

    def audit(self):
        return [asdict(c) for c in self.checks]


async def execute_check(policy: dict, task: Path, inputs: bytes, expected) -> CheckResult:
    name = "mewcode-judge-" + uuid.uuid4().hex
    if "," in str(task):
        raise JudgeUnavailable("临时挂载路径无效")
    try:
        await docker_control(
            "create", "--pull=never", "--name", name, "--interactive",
            "--platform", policy["platform"], "--network=none", "--read-only", "--user=65534:65534",
            "--cap-drop=ALL", "--security-opt=no-new-privileges", "--log-driver=none",
            "--pids-limit", str(policy["pids_limit"]), "--memory", f"{policy['memory_mb']}m", "--cpus=1",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m",
            "--mount", f"type=bind,source={task},target=/task,readonly", "--workdir", "/task",
            "--entrypoint", "python", policy["image"], "-B", "/task/app.py",
        )
        proc = await asyncio.create_subprocess_exec(
            "docker", "start", "--attach", "--interactive", name, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        output = bytearray()
        timed_out = False
        try:
            async with asyncio.timeout(policy["timeout_seconds"]):
                proc.stdin.write(inputs)
                proc.stdin.close()
                while chunk := await proc.stdout.read(8192):
                    output.extend(chunk[:max(0, OUTPUT_LIMIT + 1 - len(output))])
                await proc.wait()
        except TimeoutError:
            timed_out = True
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.communicate()
        state = json.loads(await docker_control("inspect", "--format", "{{json .State}}", name))
        if state["Error"] or state["StartedAt"].startswith("0001-"):
            raise JudgeUnavailable("无法证明被测程序已启动")
        if timed_out:
            return CheckResult("timeout", None)
        if state["Status"] != "exited" or proc.returncode != state["ExitCode"]:
            raise JudgeUnavailable("无法核对被测进程退出状态")
        code = state["ExitCode"]
        if code != 0:
            return CheckResult("nonzero_exit", code)
        if len(output) > OUTPUT_LIMIT:
            return CheckResult("output_limit", code)
        try:
            actual = json.loads(output.decode("utf-8"))
            match = digest(actual) == digest(expected)
        except (ValueError, RecursionError):
            return CheckResult("malformed_output", code)
        return CheckResult("passed" if match else "wrong_answer", code)
    finally:
        await docker_control("rm", "--force", name)


class TestRunner:
    def __init__(self, policy: dict):
        self.policy = policy

    async def grade(self, task: Path, checks: list[dict]) -> Grade:
        if not checks or any(not isinstance(c, dict) or set(c) != {"input", "expected"} for c in checks):
            raise ValueError("私测清单无效")
        if not (task / "app.py").is_file():
            return Grade(tuple(CheckResult("missing_entry", None) for _ in checks))
        results = []
        for check in checks:
            payload = (json.dumps(check["input"], allow_nan=False) + "\n").encode()
            results.append(await execute_check(self.policy, task, payload, check["expected"]))
        return Grade(tuple(results))
