"""Shared fixture imports keep suite-specific helpers out of the shared path."""

import runpy
import sys
from importlib.machinery import PathFinder
from pathlib import Path

import pytest


def test_memory_fixture_keeps_agent_helpers_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loading the shared fixtures must not expose helpers from the agents test suite."""
    tests = Path(__file__).parent
    agents = tests / "agents"
    # Other selections may already have loaded agents/conftest.py in this worker.
    monkeypatch.setattr(sys, "path", [p for p in sys.path if Path(p).resolve() != agents])

    runpy.run_path(str(tests / "conftest.py"))

    assert PathFinder.find_spec("live_helpers") is not None
    assert PathFinder.find_spec("agent_helpers") is None
