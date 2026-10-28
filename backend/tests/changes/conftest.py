"""Fixtures for the change manager tests. pytest's importlib mode adds no test folder to sys.path,
so this file adds its own, for changes_helpers."""

import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from changes_helpers import Harness, start_harness


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def h(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Harness]:
    async with asyncio.timeout(60):
        harness = await start_harness(tmp_path)
    if request.path.stem.startswith("test_probes"):
        from unittest.mock import AsyncMock

        from oversight.workspace import IsolatedRunner, run_process

        monkeypatch.setattr("oversight.workspace.capability.check_effect", AsyncMock())
        # Inject only the kernel boundary; retain real capture, suite and report handling.
        monkeypatch.setattr(IsolatedRunner, "__call__", staticmethod(run_process))
        await harness.workspace.check_isolation()
    try:
        yield harness
    finally:
        async with asyncio.timeout(60):
            await harness.close()
