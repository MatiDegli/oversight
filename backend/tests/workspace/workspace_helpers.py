"""Shared support moved verbatim from the split test modules."""

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from oversight.workspace import ProcessResult, Workspace
from oversight.workspace import core as workspace_core
from oversight.workspace import runner as workspace_runner

pytestmark = pytest.mark.anyio


MESSAGE = "Revert rule merge\n\nPreserve later work.\n"


@pytest.fixture
def api():
    from oversight.workspace import revert

    return revert


async def candidate(workspace, api):
    """H contains later work; T removes only the selected change."""
    async with workspace.locks(main=True):
        main = workspace.main
        h0 = (await workspace.git_ok(main, "rev-parse", "HEAD")).strip()
        path = main.root / "shop/mod.py"
        original = path.read_text()
        path.write_text("def value():\n    return 2\n")
        await workspace.git_ok(main, "add", "shop/mod.py")
        await workspace.git_ok(main, "commit", "-qm", "Rule merge")
        path = main.root / "later.py"
        path.write_text("later = 1\n")
        await workspace.git_ok(main, "add", "later.py")
        await workspace.git_ok(main, "commit", "-qm", "Later work")
        head = (await workspace.git_ok(main, "rev-parse", "HEAD")).strip()
        path = main.root / "shop/mod.py"
        path.write_text(original)
        await workspace.git_ok(main, "add", "shop/mod.py")
        target = (await workspace.git_ok(main, "write-tree")).strip()
        await workspace.git_ok(main, "read-tree", "-m", "-u", head)
        assert head != h0
    return head, target


async def facts(workspace):
    git = workspace.git_ok
    main = workspace.main
    return tuple(
        [
            await git(main, *args)
            for args in (
                ("symbolic-ref", "HEAD"),
                ("rev-parse", "HEAD"),
                ("write-tree",),
                ("status", "--porcelain", "-z"),
                ("diff", "HEAD"),
            )
        ]
    )


async def prepared(workspace, api):
    h, t = await candidate(workspace, api)
    async with (
        workspace.locks(main=True, agent_id="agent-1"),
        api.prepare_inverse(
            workspace,
            agent_id="agent-1",
            main_commit=h,
            inverse_tree=t,
            message=MESSAGE,
            timeout_s=5,
        ) as item,
    ):
        pass
    return item


def effect_args(item):
    return dict(
        main_commit=item.main_commit,
        inverse_commit=item.inverse_commit,
        inverse_tree=item.inverse_tree,
        timeout_s=5,
    )


type GitOut = Callable[..., Awaitable[str]]


type MakeWorkspace = Callable[..., Workspace]


def count_spawns(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """Record every process the workspace starts, and still run it."""
    spawned: list[tuple[str, ...]] = []
    real = workspace_runner.run_process

    async def counting(argv: Any, **kwargs: Any) -> ProcessResult:
        spawned.append(tuple(argv))
        return await real(argv, **kwargs)

    monkeypatch.setattr(workspace_core, "run_process", counting)
    return spawned
