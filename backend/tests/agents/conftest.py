"""Fixtures for the agent runtime tests. pytest's importlib mode adds no test folder to sys.path,
so this file adds its own, for agent_helpers."""

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from agent_helpers import DEMO_REPO, SMALL_TEMPLATE, open_tree, write_tree

from oversight.workspace import Tree, Workspace


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def small_template(tmp_path: Path) -> Path:
    return write_tree(tmp_path / "template", SMALL_TEMPLATE)


@pytest.fixture
async def started(tmp_path: Path) -> AsyncIterator[list[Workspace]]:
    """Workspaces made by a test; their runtime locks are released at the end."""
    made: list[Workspace] = []
    yield made
    for workspace in made:
        workspace.release_runtime()


@pytest.fixture
async def small_tree(
    tmp_path: Path, started: list[Workspace], small_template: Path
) -> tuple[Workspace, Tree]:
    return await open_tree(started, tmp_path / "rt", small_template)


@pytest.fixture
async def demo_tree(tmp_path: Path, started: list[Workspace]) -> tuple[Workspace, Tree]:
    return await open_tree(started, tmp_path / "demo", DEMO_REPO)
