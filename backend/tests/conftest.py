"""Expose shared helpers in importlib mode, including agent helpers imported by API tests
when the agent test directory itself is not collected."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "agents"))


@pytest.fixture(autouse=True)
def no_host_memory_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    """General tests never depend on host load; memory admission tests inject their readings."""
    monkeypatch.setattr("oversight.app.memory_used", lambda: None)


@pytest.fixture
def baseten_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    """Muse Spark Open may run agents, whether or not the Baseten probe's branch is merged: the
    server's start check and the loop's own both read their own `UNVERIFIED_PROFILES`."""
    monkeypatch.setattr("oversight.app.UNVERIFIED_PROFILES", frozenset())
    monkeypatch.setattr("oversight.agents.client.UNVERIFIED_PROFILES", frozenset())


@pytest.fixture(autouse=True)
def no_kernel_startup_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject available C for general server tests; policy tests explicitly model no C.

    Only the session-selected runner is replaced. C1's direct runner/entry/attack tests
    retain the real launcher and their own injected confirmation path.
    """
    from unittest.mock import AsyncMock

    from oversight.workspace import IsolatedRunner, run_process

    class SessionRunner(IsolatedRunner):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._ready = True

        async def _run(self, argv, cwd, timeout_s, env, cap, *, probe=False):
            return await run_process(argv, cwd=cwd, timeout_s=timeout_s, env=env, output_cap=cap)

    monkeypatch.setattr("oversight.workspace.capability.check_effect", AsyncMock())
    monkeypatch.setattr("oversight.workspace.capability.IsolatedRunner", SessionRunner)
