"""第 1 天冻结的输入类型。正式运行共用这些身份字段。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re


def digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def require_digest(value: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("需要完整 SHA-256 摘要")


@dataclass(frozen=True)
class CasePin:
    case_id: str
    repo_commit: str
    statement_digest: str
    rubric_digest: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z0-9-]+", self.case_id):
            raise ValueError("case 标识无效")
        if not re.fullmatch(r"[0-9a-f]{40}", self.repo_commit):
            raise ValueError("需要完整 Git 提交号")
        require_digest(self.statement_digest)
        require_digest(self.rubric_digest)


@dataclass(frozen=True)
class BenchmarkPin:
    benchmark_id: str
    manifest_digest: str
    cases: tuple[CasePin, ...]

    @property
    def fingerprint(self) -> str:
        return digest(asdict(self))


@dataclass(frozen=True)
class FrozenContext:
    benchmark_digest: str
    evaluator_digest: str
    policy_digest: str
    runtime_digest: str
    image: str
    platform: str
    provider_id: str
    model_id: str
    inference_digest: str
    budget_digest: str

    def __post_init__(self) -> None:
        for value in (self.benchmark_digest, self.evaluator_digest, self.policy_digest,
                      self.runtime_digest, self.inference_digest, self.budget_digest):
            require_digest(value)
        if not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", self.image):
            raise ValueError("运行镜像必须固定到 OCI 摘要")
        if not all(v.strip() for v in (self.platform, self.provider_id, self.model_id)):
            raise ValueError("运行平台、Provider 和模型身份必须完整")

    def run_fingerprint(self, strategy_digest: str) -> str:
        require_digest(strategy_digest)
        return digest({"context": asdict(self), "strategy": strategy_digest})


@dataclass(frozen=True)
class EvalCell:
    evaluation_id: str
    case_id: str
    run_index: int
    expected_version: int
    run_fingerprint: str

    def __post_init__(self) -> None:
        require_digest(self.run_fingerprint)
        if not self.evaluation_id.strip() or not self.case_id.strip():
            raise ValueError("无效的评测单元身份")
        for number in (self.run_index, self.expected_version):
            if type(number) is not int or number < 1:
                raise ValueError("运行序号和版本需要为正整数")

    @property
    def cell_id(self) -> str:
        return digest(asdict(self))
