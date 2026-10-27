"""Use the backend's real local-server harness and scripted model, with no credentials."""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "backend" / "tests"))


@pytest.fixture
def anyio_backend():
    return "asyncio"
