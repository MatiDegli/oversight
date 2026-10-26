"""Repository-wide test fixtures."""

import os
import sys
from pathlib import Path

import pytest

# The memory cap (tools/memwatch.py) is a plugin of every run: make check, CI and a bare `pytest`.
sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
pytest_plugins = ["memwatch"]


@pytest.fixture(autouse=True)
def _no_inherited_git_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop GIT_* variables, so a test's git commands touch only the repository it names.

    Git exports GIT_DIR and others to its hooks. The pre-push hook runs make check, and a test that
    ran `git init` or `git commit` in a temporary directory under that environment once wrote to
    this repository instead: it set core.bare and a test identity, and committed on the branch.
    """
    for name in list(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)
