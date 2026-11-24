"""Run upstream tests in the source layout they expect, including MEWCODE.md."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def upstream_working_directory(monkeypatch):
    monkeypatch.chdir(Path(__file__).resolve().parent)
