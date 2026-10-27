"""pytest's importlib mode adds no test folder to sys.path, so this file adds this one, for the
live server helpers the API, end-to-end and app tests share."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture
def baseten_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    """Muse Spark Open may run agents, whether or not the Baseten probe's branch is merged: the
    server's start check and the loop's own both read their own `UNVERIFIED_PROFILES`."""
    monkeypatch.setattr("oversight.app.UNVERIFIED_PROFILES", frozenset())
    monkeypatch.setattr("oversight.agents.client.UNVERIFIED_PROFILES", frozenset())
