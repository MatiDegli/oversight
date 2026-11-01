"""Use the backend's real local-server harness and scripted model, with no credentials."""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "backend" / "tests"))


@pytest.fixture(autouse=True)
def no_host_memory_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    """General tests never depend on host load; memory admission tests inject their readings."""
    monkeypatch.setattr("oversight.app.memory_used", lambda: None)


@pytest.fixture
def anyio_backend():
    return "asyncio"
