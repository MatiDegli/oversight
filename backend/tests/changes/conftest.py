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
async def h(tmp_path: Path) -> AsyncIterator[Harness]:
    async with asyncio.timeout(60):
        harness = await start_harness(tmp_path)
    try:
        yield harness
    finally:
        async with asyncio.timeout(60):
            await harness.close()
