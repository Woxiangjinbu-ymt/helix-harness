"""多文件仓库的导出、复原与失败时的发布语义。"""

from zipfile import ZipFile

import pytest

from helix_harness.rsi.artifacts import export_archive
from helix_harness.rsi.evaluator import CellFailure
from helix_harness.rsi.fixtures import tree_digest


def test_export_restores_exact_snapshot_and_is_idempotent(tmp_path):
    source = tmp_path / "artifact"
    nested = source / "数据"
    nested.mkdir(parents=True)
    (source / "app.py").write_bytes(b"print('ok')\r\n")
    (nested / "table.bin").write_bytes(bytes(range(256)))
    expected = tree_digest(source)
    target = tmp_path / "exports" / "cell.zip"
    receipt = export_archive(source, target, expected)
    with ZipFile(target) as bundle:
        assert set(bundle.namelist()) == {"app.py", "数据/table.bin"}
        bundle.extractall(tmp_path / "fresh")
    assert tree_digest(tmp_path / "fresh") == receipt["artifact_digest"] == expected
    assert export_archive(source, target, expected) == receipt


def test_mismatched_source_never_publishes(tmp_path):
    source = tmp_path / "artifact"
    source.mkdir()
    (source / "app.py").write_text("edited")
    target = tmp_path / "exports" / "cell.zip"
    with pytest.raises(CellFailure, match="version_changed"):
        export_archive(source, target, "0" * 64)
    assert list(target.parent.iterdir()) == []


def test_preexisting_different_archive_is_preserved(tmp_path):
    source = tmp_path / "artifact"
    source.mkdir()
    (source / "app.py").write_text("answer")
    target = tmp_path / "cell.zip"
    target.write_bytes(b"existing file")
    with pytest.raises(CellFailure, match="version_changed"):
        export_archive(source, target, tree_digest(source))
    assert target.read_bytes() == b"existing file"


def test_link_in_artifact_cannot_export_host_file(tmp_path):
    secret = tmp_path / "unrelated"
    secret.write_text("outside")
    source = tmp_path / "artifact"
    source.mkdir()
    (source / "payload").symlink_to(secret)
    target = tmp_path / "exports" / "cell.zip"
    with pytest.raises(ValueError, match="禁止"):
        export_archive(source, target, "0" * 64)
    assert not target.exists()
