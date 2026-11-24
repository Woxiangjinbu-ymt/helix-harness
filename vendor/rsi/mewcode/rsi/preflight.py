"""第 1 天的环境预检与标准样例校准。只装入受信的出厂样例。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

from .contracts import digest
from .fixtures import BENCHMARK, copy_files, freeze_inputs, materialize_repo, verify_lock


def docker(*args: str, timeout: int = 30, stdin: str = "") -> str:
    result = subprocess.run(["docker", *args], input=stdin, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"Docker 预检失败 ({result.returncode})：{result.stderr.strip()}")
    return result.stdout


def run_in_task(policy: dict, task: Path, argv: list[str], stdin: str = "") -> str:
    name = "mewcode-rsi-preflight-" + uuid.uuid4().hex
    # 只传递公开仓库。私测期望值、策略版本库、Docker socket 和宿主环境均留在外部。
    if "," in str(task):
        raise ValueError("Docker 预检的临时目录路径含有逗号")
    options = [
        "run", "--rm", "--pull=never", "--name", name, "--platform", policy["platform"],
        "--network=none", "--user=65534:65534", "--read-only", "--cap-drop=ALL",
        "--security-opt=no-new-privileges", "--pids-limit", str(policy["pids_limit"]),
        "--memory", f"{policy['memory_mb']}m", "--cpus=1",
        "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m",
        "--mount", f"type=bind,source={task},target=/task,readonly",
        "--workdir", "/task", "--interactive", "--entrypoint", "python",
        policy["image"], "-B", *argv,
    ]
    try:
        return docker(*options, timeout=policy["timeout_seconds"], stdin=stdin)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15)
        raise


def isolation_probe(policy: dict, scratch: Path) -> None:
    task = scratch / "probe"
    task.mkdir(mode=0o755)
    public_token = uuid.uuid4().hex
    (task / "public-probe").write_text(public_token)
    (task / "public-probe").chmod(0o644)
    private_file = scratch / ("private-" + uuid.uuid4().hex)
    private_file.write_text(uuid.uuid4().hex)
    # 哨兵特意可读，以验证隔离来自挂载边界。公开文件是正对照。
    private_file.chmod(0o644)
    probe = """
import os, sys
from pathlib import Path
if Path('/task/public-probe').read_text() != sys.argv[2]:
    sys.exit('public mount unavailable')
for name in (sys.argv[1], '/task/' + Path(sys.argv[1]).name):
    try:
        Path(name).read_bytes()
    except (FileNotFoundError, PermissionError):
        continue
    sys.exit('private host file readable')
if Path('/var/run/docker.sock').exists():
    sys.exit('docker socket visible')
interfaces = Path('/sys/class/net').iterdir()
active = {p.name for p in interfaces if p.is_dir() and int((p / 'flags').read_text(), 16) & 1}
if os.getuid() != 65534 or active != {'lo'} or len(Path('/proc/net/route').read_text().splitlines()) > 1:
    sys.exit('unexpected identity or network')
mounts = {line.split()[4]: line.split()[5].split(',') for line in Path('/proc/self/mountinfo').read_text().splitlines()}
if any('ro' not in mounts[mount] for mount in ('/', '/task')):
    sys.exit('mount is writable')
print('isolated')
"""
    result = run_in_task(policy, task, ["-I", "-c", probe, str(private_file), public_token])
    if result.strip() != "isolated":
        raise RuntimeError("私有文件隔离验证失败")


def fixture_score(policy: dict, task: Path, checks: list[dict]) -> float:
    if not checks or any(set(row) != {"input", "expected"} for row in checks):
        raise ValueError("评分材料需要非空的输入和预期输出")
    run_in_task(policy, task, ["-m", "unittest", "public_checks"])
    payload = "".join(json.dumps(row["input"]) + "\n" for row in checks)
    lines = run_in_task(policy, task, ["app.py"], payload).splitlines()
    if len(lines) != len(checks):
        raise ValueError("校准样例的响应数与输入数不一致")
    passed = sum(digest(json.loads(line)) == digest(row["expected"]) for line, row in zip(lines, checks))
    return round(100 * passed / len(checks), 2)


def check_environment(root: Path) -> dict:
    lock = verify_lock(root)
    policy = json.loads((root / "evaluation/policy/docker.json").read_text())
    if not re.fullmatch(r"[a-z0-9./_-]+@sha256:[0-9a-f]{64}", policy["image"]):
        raise ValueError("预检必须使用固定摘要的镜像")
    info = json.loads(docker("image", "inspect", policy["image"]))[0]
    platform = info["Os"] + "/" + info["Architecture"]
    if platform != policy["platform"]:
        raise ValueError("本地镜像平台与冻结策略不一致")
    bench = root / "evaluation" / BENCHMARK
    scores = {}
    with tempfile.TemporaryDirectory(prefix="mewcode-rsi-") as tmp:
        scratch = Path(tmp).resolve()
        isolation_probe(policy, scratch)
        for case in lock["benchmark"]["cases"]:
            case_id = case["case_id"]
            public = bench / "public" / case_id
            private = bench / "private" / case_id
            task = scratch / case_id
            materialize_repo(public / "repo", task, case["repo_commit"])
            shutil.rmtree(task / ".git")
            rubric = json.loads((private / "checks.json").read_text())
            starter = fixture_score(policy, task, rubric["checks"])
            copy_files(private / "reference", task)
            reference = fixture_score(policy, task, rubric["checks"])
            if not (starter == rubric["starter_score"] < reference == 100):
                raise ValueError(f"{case_id} 校准未通过：starter={starter}, reference={reference}")
            scores[case_id] = {
                "repo_commit": case["repo_commit"], "starter": starter,
                "reference": reference, "checks": len(rubric["checks"]),
            }
    if verify_lock(root) != lock:
        raise ValueError("预检期间冻结条件发生变化")
    return {"status": "ok", "benchmark": BENCHMARK, "benchmark_digest": lock["benchmark_digest"],
            "input_lock_digest": digest(lock),
            "image": policy["image"], "platform": platform, "image_id": info["Id"],
            "isolation_probe": "passed", "calibration": scores}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(required=True, dest="command")
    commands.add_parser("describe", help="打印当前输入清单，供发布者核对")
    commands.add_parser("verify", help="离线核对已冻结的输入")
    commands.add_parser("check", help="核对输入并在 Docker 中执行隔离预检与样例校准")
    checkout = commands.add_parser("checkout", help="重建指定 case 的公开 Git 仓库")
    checkout.add_argument("case_id")
    checkout.add_argument("destination", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    try:
        if args.command == "describe":
            result = freeze_inputs(root)
        elif args.command == "check":
            result = check_environment(root)
        else:
            lock = verify_lock(root)
            result = {"status": "ok", "benchmark_digest": lock["benchmark_digest"]}
            if args.command == "checkout":
                cases = {c["case_id"]: c for c in lock["benchmark"]["cases"]}
                if args.case_id not in cases:
                    raise ValueError(f"未知 case：{args.case_id}")
                case = cases[args.case_id]
                if any(args.destination.resolve().is_relative_to(root / name) for name in ("evaluation", "mewcode")):
                    raise ValueError("目标目录必须位于评测材料和运行时代码之外")
                public = root / "evaluation" / BENCHMARK / "public" / args.case_id
                result["repo_commit"] = materialize_repo(public / "repo", args.destination, case["repo_commit"])
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, KeyError) as exc:
        print(f"RSI 预检失败：{exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
