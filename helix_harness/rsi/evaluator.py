"""独立评分进程。公开回执与私有逐项判分证据分别输出。"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import uuid

from .contracts import EvalCell, FrozenContext, digest, require_digest
from .fixtures import BENCHMARK, copy_files, file_digest, tree_digest, verify_lock
from .v1_identity import published_id
from .runtime import ROOT, read_outcome
from .test_runner import TestRunner, docker_control


class CellFailure(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def judge_build(root: Path, lock: dict) -> str:
    sources = {name: file_digest(root / "helix_harness/rsi" / name)
               for name in ("test_runner.py", "evaluator.py", "baseline.py", "runtime.py")}
    physical = digest({"revision": 1, "calibration": lock["evaluator_digest"],
                       "policy": lock["policy_digest"], "sources": sources})
    return published_id("judge", physical)


def verified_release(root: Path = ROOT):
    try:
        lock = verify_lock(root)
        judge = judge_build(root, lock)
        pin = root / "evaluation/policy/judge-v1.json"
        if pin.is_symlink():
            raise ValueError("评分发布记录必须为常规文件")
        published = json.loads(pin.read_text())
        if published != {"protocol_version": 1, "status": "published",
                         "benchmark_digest": lock["benchmark_digest"], "judge_digest": judge}:
            raise ValueError("评分实现与发布记录不符")
        return lock, judge
    except (ValueError, KeyError, OSError) as exc:
        raise CellFailure("benchmark_invalid") from exc


@dataclass(frozen=True)
class ScoreRequest:
    cell: EvalCell
    strategy_digest: str
    judge_digest: str
    provider: str
    model_id: str
    thinking_mode: str
    agent_id: str = "mewcode"
    benchmark_id: str = BENCHMARK
    protocol_version: int = 1

    def __post_init__(self):
        if (type(self.protocol_version) is not int or self.protocol_version != 1 or self.agent_id != "mewcode"
                or self.benchmark_id != BENCHMARK or not self.provider or not self.model_id
                or self.thinking_mode not in {"provider_default", "on", "off"}):
            raise ValueError("评分请求身份无效")
        require_digest(self.strategy_digest)
        require_digest(self.judge_digest)

    @classmethod
    def parse(cls, data):
        fields = dict(data)
        fields["cell"] = EvalCell(**fields["cell"])
        return cls(**fields)

    def public_identity(self):
        return {"protocol_version": 1, "agent_id": self.agent_id, "benchmark_id": self.benchmark_id,
                "evaluation_id": self.cell.evaluation_id, "strategy_digest": self.strategy_digest,
                "case_id": self.cell.case_id, "run": self.cell.run_index, "cell_id": self.cell.cell_id,
                "expected_version": self.cell.expected_version, "provider": self.provider,
                "model_id": self.model_id, "thinking_mode": self.thinking_mode,
                "run_fingerprint": self.cell.run_fingerprint, "judge_digest": self.judge_digest}

    def validate_receipt(self, receipt):
        identity = self.public_identity()
        metrics = {"status", "score", "cost", "duration_ms", "session_id", "trace_ref",
                   "trace_digest", "artifact_digest", "repo_commit"}
        if (not isinstance(receipt, dict) or set(receipt) != identity.keys() | metrics
                or any(receipt[k] != value for k, value in identity.items()) or receipt["status"] != "ok"):
            raise CellFailure("evaluation_failed")
        score, cost = receipt["score"], receipt["cost"]
        if (type(score) not in (int, float) or not 0 <= score <= 100
                or (cost is not None and (type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0))
                or type(receipt["duration_ms"]) is not int or receipt["duration_ms"] < 0
                or receipt["trace_ref"] != f"{self.cell.cell_id}/events.jsonl"):
            raise CellFailure("evaluation_failed")
        for key, length in (("trace_digest", 64), ("artifact_digest", 64), ("session_id", 32), ("repo_commit", 40)):
            if not isinstance(receipt[key], str) or not re.fullmatch(rf"[0-9a-f]{{{length}}}", receipt[key]):
                raise CellFailure("evaluation_failed")


def check_run(run: Path, request: ScoreRequest, lock: dict, case: dict):
    try:
        return _read_verified_run(run, request, lock, case)
    except (TypeError, KeyError, ValueError, OSError, RuntimeError) as exc:
        raise CellFailure("evaluation_failed") from exc


def _read_verified_run(run: Path, request: ScoreRequest, lock: dict, case: dict):
    if run.is_symlink() or any((run / f).is_symlink() for f in ("identity.json", "outcome.json", "events.jsonl", "artifact")):
        raise CellFailure("evaluation_failed")
    identity = json.loads((run / "identity.json").read_text())
    ctx = FrozenContext(**identity["context"])
    if ctx.benchmark_digest != lock["benchmark_digest"] or identity["repo_commit"] != case["repo_commit"]:
        raise CellFailure("benchmark_invalid")
    if (identity["cell"] != asdict(request.cell) or identity["cell_id"] != request.cell.cell_id
            or identity["strategy_digest"] != request.strategy_digest
            or ctx.run_fingerprint(request.strategy_digest) != request.cell.run_fingerprint
            or (ctx.provider_id, ctx.model_id) != (request.provider, request.model_id)
            or ctx.policy_digest != lock["policy_digest"] or ctx.evaluator_digest != lock["evaluator_digest"]):
        raise CellFailure("version_changed")
    result = read_outcome(run, request.cell)
    if result["status"] not in {"completed", "turn_limit", "timed_out"}:
        raise CellFailure("evaluation_failed")
    for key in ("artifact_digest", "trace_digest"):
        require_digest(result[key])
    if (not re.fullmatch(r"[0-9a-f]{32}", result["session_id"])
            or result["trace_ref"] != f"{request.cell.cell_id}/events.jsonl"
            or type(result["duration_ms"]) is not int or result["duration_ms"] < 0):
        raise CellFailure("evaluation_failed")
    seen = 0
    terminal = False
    count = 0
    with (run / "events.jsonl").open() as stream:
        for count, line in enumerate(stream, 1):
            event = json.loads(line)
            if (terminal or event["session_id"] != result["session_id"] or event["seq"] != count
                    or (event["type"] == "started") != (count == 1)):
                raise CellFailure("evaluation_failed")
            if event["type"] == "model_request":
                if (event["provider"], event["model_id"], event["thinking_mode"]) != (
                    request.provider, request.model_id, request.thinking_mode
                ):
                    raise CellFailure("version_changed")
                seen += 1
            if event["type"] == "terminal":
                terminal = True
                if event["status"] != result["status"]:
                    raise CellFailure("evaluation_failed")
    if not seen or not terminal or count != result["events"]:
        raise CellFailure("evaluation_failed")
    return result, identity


async def score_cell(state: Path, request: ScoreRequest):
    lock, judge = verified_release()
    if judge != request.judge_digest:
        raise CellFailure("version_changed")
    cases = {c["case_id"]: c for c in lock["benchmark"]["cases"]}
    if request.cell.case_id not in cases:
        raise CellFailure("invalid_request")
    case = cases[request.cell.case_id]
    run = state / "runs" / request.cell.cell_id
    outcome, identity = check_run(run, request, lock, case)
    policy = json.loads((ROOT / "evaluation/policy/docker.json").read_text())
    image = json.loads(await docker_control("image", "inspect", policy["image"]))[0]
    if (image["Id"] != identity["image_id"] or policy["image"] != identity["context"]["image"]
            or f"{image['Os']}/{image['Architecture']}" != identity["context"]["platform"]):
        raise CellFailure("version_changed")
    rules = ROOT / "evaluation" / BENCHMARK / "private" / case["case_id"] / "checks.json"
    if file_digest(rules) != case["rubric_digest"]:
        raise CellFailure("benchmark_invalid")
    checks = json.loads(rules.read_text())["checks"]
    with tempfile.TemporaryDirectory(prefix="mewcode-grade-") as tmp:
        snapshot = Path(tmp).resolve() / "task"
        snapshot.mkdir()
        copy_files(run / "artifact", snapshot)
        if tree_digest(snapshot) != outcome["artifact_digest"]:
            raise CellFailure("version_changed")
        grade = await TestRunner(policy).grade(snapshot, checks)
        if tree_digest(snapshot) != outcome["artifact_digest"]:
            raise CellFailure("version_changed")
    if verified_release() != (lock, judge) or check_run(run, request, lock, case) != (outcome, identity):
        raise CellFailure("version_changed")
    receipt = dict(request.public_identity(), status="ok", score=grade.score, cost=None,
                   duration_ms=outcome["duration_ms"], session_id=outcome["session_id"],
                   trace_ref=outcome["trace_ref"], trace_digest=outcome["trace_digest"],
                   artifact_digest=outcome["artifact_digest"], repo_commit=case["repo_commit"])
    private = state / "evaluator-private"
    private.mkdir(mode=0o700, exist_ok=True)
    (private / (uuid.uuid4().hex + ".json")).write_text(json.dumps(
        {"receipt_digest": digest(receipt), "cell_id": request.cell.cell_id, "checks": grade.audit()}) + "\n")
    return receipt


def worker():
    request = None
    try:
        job = json.load(sys.stdin)
        state = Path(job["state"]).resolve()
        if not state.is_dir() or state.is_relative_to(ROOT):
            raise ValueError("无效的控制目录")
        request = ScoreRequest.parse(job["request"])
    except (TypeError, KeyError, ValueError, OSError):
        result = {"protocol_version": 1, "status": "failed", "failure_code": "invalid_request"}
    else:
        try:
            result = asyncio.run(score_cell(state, request))
        except CellFailure as exc:
            result = dict(request.public_identity(), status="failed", failure_code=exc.code)
        except (TypeError, KeyError, ValueError, OSError, RuntimeError, RecursionError, IndexError):
            result = dict(request.public_identity(), status="failed", failure_code="evaluation_failed")
    print(json.dumps(result, allow_nan=False))


class Evaluator:
    def __init__(self, state: Path):
        self.state = state.resolve()

    async def score(self, request: ScoreRequest) -> dict:
        # 独立解释器不继承模型凭据，只给 Docker 客户端所需的宿主设置。
        env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "TMPDIR"} or k.startswith("DOCKER_")}
        entry = "import sys; sys.path.insert(0,sys.argv[1]); from helix_harness.rsi.evaluator import worker; worker()"
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-IB", "-c", entry, str(ROOT), cwd=self.state, env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        payload = json.dumps({"state": str(self.state), "request": asdict(request)}).encode()
        try:
            output, _ = await proc.communicate(payload)
        except asyncio.CancelledError:
            # worker 自己管理容器清理，待当前受超时约束的评分完成后结束。
            await proc.wait()
            raise
        if proc.returncode:
            raise CellFailure("evaluation_failed")
        try:
            receipt = json.loads(output)
            if any(receipt.get(k) != v for k, v in request.public_identity().items()):
                raise ValueError("回执与请求不一致")
            if receipt["status"] == "failed":
                code = receipt["failure_code"]
                if code not in {"invalid_request", "version_changed", "evaluation_failed", "benchmark_invalid"}:
                    raise ValueError("评分错误码无效")
                raise CellFailure(code)
            request.validate_receipt(receipt)
            return receipt
        except (TypeError, KeyError, ValueError) as exc:
            raise CellFailure("evaluation_failed") from exc
