"""冻结清单、Git 重建和运行身份的回归测试。"""

from dataclasses import replace
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from helix_harness.rsi.contracts import EvalCell, FrozenContext, digest
from helix_harness.rsi.fixtures import BENCHMARK, materialize_repo, verify_lock


ROOT = Path(__file__).resolve().parents[3]
BENCH = ROOT / "evaluation" / BENCHMARK


def test_published_fixture_rebuilds_as_clean_public_checkout(tmp_path):
    lock = verify_lock(ROOT)
    for case in lock["benchmark"]["cases"]:
        dest = tmp_path / case["case_id"]
        run = subprocess.run(
            [sys.executable, "-B", "-m", "helix_harness.rsi.preflight", "checkout", case["case_id"], str(dest)],
            cwd=ROOT, capture_output=True, check=True, text=True,
        )
        head = subprocess.check_output(["git", "-C", dest, "rev-parse", "HEAD"], text=True).strip()
        assert head == case["repo_commit"] == json.loads(run.stdout)["repo_commit"]
        assert not subprocess.check_output(["git", "-C", dest, "status", "--porcelain"])
        names = subprocess.check_output(["git", "-C", dest, "ls-files"], text=True).splitlines()
        public = BENCH / "public" / case["case_id"] / "repo"
        assert names == sorted(p.relative_to(public).as_posix() for p in public.rglob("*") if p.is_file())


@pytest.mark.parametrize(("relative", "changed"), [
    ("evaluation/repo-python-v1/public/page-settings/statement.md", "benchmark_digest"),
    ("evaluation/repo-python-v1/public/page-settings/repo/app.py", "benchmark_digest"),
    ("evaluation/repo-python-v1/private/page-settings/checks.json", "benchmark_digest"),
    ("evaluation/repo-python-v1/private/page-settings/reference/app.py", "calibration_reference_digests"),
    ("evaluation/strategies/default-v1/prompt.md", "initial_strategy_digest"),
    ("evaluation/policy/docker.json", "policy_digest"),
    ("helix_harness/rsi/preflight.py", "evaluator_digest"),
    ("vendor/mtcode-runtime/mtcode/client.py", "runtime_digest"),
])
def test_drift_blocks_use_of_frozen_release(release_copy, relative, changed):
    file = release_copy / relative
    if relative.endswith("docker.json"):
        policy = json.loads(file.read_text())
        policy["image"] = "python@sha256:" + "1" * 64
        file.write_text(json.dumps(policy))
    else:
        with file.open("a") as stream:
            stream.write("\n")
    with pytest.raises(ValueError, match=changed):
        verify_lock(release_copy)


def test_link_to_private_input_is_refused_before_creating_task(release_copy):
    case = release_copy / "evaluation" / BENCHMARK
    (case / "public/page-settings/repo/leak").symlink_to(case / "private/page-settings/checks.json")
    with pytest.raises(ValueError, match="禁止"):
        verify_lock(release_copy)


def test_checkout_rejects_wrong_commit_and_leaves_existing_work_untouched(tmp_path):
    source = BENCH / "public/page-settings/repo"
    dest = tmp_path / "checkout"
    with pytest.raises(ValueError, match="起始提交漂移"):
        materialize_repo(source, dest, expected="f" * 40)
    assert not dest.exists()
    dest.mkdir()
    sentinel = dest / "user-work.txt"
    sentinel.write_text("untouched")
    with pytest.raises(FileExistsError):
        materialize_repo(source, dest)
    assert sentinel.read_text() == "untouched"


def test_relocated_runtime_replays_v1_but_a_changed_adapter_gets_a_new_identity(tmp_path, monkeypatch):
    from mtcode.config import ProviderConfig
    from helix_harness.rsi import runtime

    policy = json.loads((ROOT / "evaluation/policy/docker.json").read_text())
    lock = verify_lock(ROOT)
    provider = ProviderConfig("test", "openai-compat", "http://localhost", "model")
    # Execution digest from the pre-relocation release, independent of provider/turn budget.
    published = "f6f987efa0d524a11d87409dff24b33fd46dfb80645ab70465577c579647474d"
    assert runtime.runtime_context(lock, policy, provider, 12, 90).runtime_digest == published

    source = Path(runtime.__file__).parent
    for name in ("runtime.py", "strategy.py", "trajectory.py", "workspace.py"):
        shutil.copyfile(source / name, tmp_path / name)
    with (tmp_path / "trajectory.py").open("a") as changed:
        changed.write("\n")
    monkeypatch.setattr(runtime, "__file__", str(tmp_path / "runtime.py"))
    assert runtime.runtime_context(lock, policy, provider, 12, 90).runtime_digest != published


def test_cell_identity_includes_frozen_inputs_strategy_and_repeat():
    lock = verify_lock(ROOT)
    policy = json.loads((ROOT / "evaluation/policy/docker.json").read_text())
    context = FrozenContext(
        benchmark_digest=lock["benchmark_digest"], evaluator_digest=lock["evaluator_digest"],
        policy_digest=lock["policy_digest"], runtime_digest=lock["runtime_digest"],
        image=policy["image"], platform=policy["platform"], provider_id="test", model_id="fixture-model",
        inference_digest=digest({"thinking": False}), budget_digest=digest({"max_turns": 3}),
    )
    strategy = lock["initial_strategy_digest"]
    cell = EvalCell("evaluation-1", "page-settings", 1, 1, context.run_fingerprint(strategy))
    assert cell.cell_id == replace(cell).cell_id
    assert cell.cell_id != replace(cell, run_index=2).cell_id
    assert context.run_fingerprint(strategy) != context.run_fingerprint(digest("new strategy"))
    assert context.run_fingerprint(strategy) != replace(context, model_id="other").run_fingerprint(strategy)
    assert context.run_fingerprint(strategy) != replace(context, image="python@sha256:" + "2" * 64).run_fingerprint(strategy)
    with pytest.raises(ValueError):
        replace(context, image="python:latest")
    with pytest.raises(ValueError):
        replace(cell, run_index=0)


def test_preflight_reports_missing_backend_without_running_fixture_on_host(tmp_path, monkeypatch):
    (tmp_path / "git").symlink_to(shutil.which("git"))
    monkeypatch.setenv("PATH", str(tmp_path))
    result = subprocess.run([sys.executable, "-B", "-m", "helix_harness.rsi.preflight", "check"],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode != 0
    assert "docker" in result.stderr.lower()
    assert not result.stdout
