"""Helix Harness Per-Repo RSI Web API 与 Coding Workspace F24 绑定测试。"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

from fastapi.testclient import TestClient
import yaml

from mewcode.rsi.default_optimizer import Edit
from mewcode.rsi.repo import RepoRSIStore, apply_playbook_ops


def _init_demo_repo(repo_dir: Path) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "README.md").write_text("# Demo Stock Web\n", encoding="utf-8")
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
    }
    for cmd in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "add", "."],
        ["git", "commit", "-q", "-m", "init"],
    ):
        subprocess.run(cmd, cwd=repo_dir, env=env, check=True)


def test_web_api_and_coding_f24_harness_binding(tmp_path: Path, monkeypatch):
    from helix_harness import api as web_api
    from helix_harness.coding import profiles as coding_profiles
    from helix_harness.coding import routes as coding_routes
    from helix_harness.coding.store import Store as CodingStore

    repo = tmp_path / "demo-stock-repo"
    _init_demo_repo(repo)
    coding_home = tmp_path / "coding-home"
    rsi_home = tmp_path / "repo-rsi-home"
    monkeypatch.setenv("CODING_WEB_HOME", str(coding_home))
    monkeypatch.setenv("REPO_RSI_HOME", str(rsi_home))
    coding_routes.store.cache_clear()
    coding_routes.manager.cache_clear()

    headers = {"x-rsi-client": "console"}
    with TestClient(web_api.app, base_url="http://127.0.0.1") as client:
        # 1. 注册仓库
        r = client.post(
            "/api/repo-rsi/repos",
            json={"repo_root": str(repo), "name": "demo-stock-repo"},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        repo_id = r.json()["meta"]["repo_id"]

        # 2. 在仓库里封存一个带 Playbook + Tool Guidance 的已验证 v2 版本
        store = RepoRSIStore(rsi_home / repo_id)
        head = store.head()
        pb = apply_playbook_ops(
            None,
            [
                {
                    "op": "add",
                    "id": "ui-101",
                    "category": "design",
                    "trigger": "开发前端图表时",
                    "rule": "保持暗色金融终端配色并展示周期切换器。",
                }
            ],
        )
        tg = yaml.safe_dump(
            {
                "version": 1,
                "description_overlays": {"Bash": "交付前执行 npm run build。"},
                "tool_hooks": [],
            },
            allow_unicode=True,
            sort_keys=False,
        )
        v2_digest = store.snapshots.seal_edits(
            head.digest, [Edit("playbook.yaml", pb), Edit("tool_guidance.yaml", tg)]
        )
        store.record_candidate(
            parent=1,
            expected_revision=1,
            candidate_digest=v2_digest,
            status="accepted",
            mean_score=96.0,
            per_case={"c1": 96.0},
            proposal={"hypothesis": "F24 绑定测试"},
            critic={"verdict": "accept"},
            summary="v2 accepted",
            promote=True,
        )

        # 3. 通过 Web API 将 v2 绑定到 Coding Workspace（F24）
        b = client.post(
            f"/api/repo-rsi/repos/{repo_id}/bind",
            json={"version": 2, "export_to_repo": True},
            headers=headers,
        )
        assert b.status_code == 200, b.text
        assert b.json()["binding"]["version"] == 2
        assert b.json()["binding"]["digest"] == v2_digest

        # 4. 在 Coding Workspace 为同一仓库新建会话并生成配置快照 -> 自动携带 harness_ref 与本地副本
        cstore = CodingStore(coding_home)
        sess = cstore.create_session(str(repo), "demo:local")
        snap = coding_profiles.for_session(cstore, sess["session_id"], "demo:local")
        assert snap["harness_ref"] is not None
        assert snap["harness_ref"]["repo_id"] == repo_id
        assert snap["harness_ref"]["version"] == 2
        assert snap["harness_ref"]["digest"] == v2_digest

        material = json.loads((cstore.root / "bindings" / snap["bindings"]).read_text())
        assert "暗色金融终端配色" in material["harness"]["instructions"]
        assert material["harness"]["description_overlays"]["Bash"] == "交付前执行 npm run build。"

        # 5. 通过 API 查看仓库列表
        listed = client.get("/api/repo-rsi/repos")
        assert listed.status_code == 200
        assert listed.json()["repos"][0]["head_version"] == 2

    coding_routes.store.cache_clear()
    coding_routes.manager.cache_clear()
