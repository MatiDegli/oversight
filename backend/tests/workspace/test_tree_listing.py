"""A cancelled tree listing keeps the workspace locked until its disk walk ends."""

import asyncio
import threading
from pathlib import Path

import pytest

from oversight.workspace import Workspace
from oversight.workspace import tree as module

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("cancel", [False, True], ids=["timeout", "cancel"])
@pytest.mark.parametrize("worker_raises", [False, True], ids=["returns", "raises"])
async def test_listing_joins_worker_before_releasing_lock(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, cancel: bool, worker_raises: bool
) -> None:
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    finished = asyncio.Event()
    release = threading.Event()

    def delayed(root: Path, deadline: float):
        loop.call_soon_threadsafe(started.set)
        try:
            assert release.wait(5), "the test did not release its walker"
            if worker_raises:
                raise OSError("disk read failed during cancellation")
            return [], [], False
        finally:
            loop.call_soon_threadsafe(finished.set)

    monkeypatch.setattr(module, "TREE_TIMEOUT_S", 10 if cancel else 0.1)
    monkeypatch.setattr(module, "walk_tree", delayed)
    pending = asyncio.create_task(module.list_tree(workspace.main))
    try:
        await asyncio.wait_for(started.wait(), 5)
        if cancel:
            pending.cancel()
        else:
            await asyncio.sleep(0.15)  # let the request's 0.1 s timeout cancel it
        # Allow cancellation to propagate through the listing, rather than inspecting the
        # task immediately after its cancel flag was set.
        for _ in range(5):
            await asyncio.sleep(0)
        assert not pending.done(), "listing released its lock while the walker was running"
        assert not workspace.operations.idle
        assert not finished.is_set()
        # A second cancellation must not interrupt the join either.
        if cancel:
            pending.cancel()
            for _ in range(5):
                await asyncio.sleep(0)
            assert not pending.done()
            assert not workspace.operations.idle
    finally:
        release.set()
        await asyncio.wait_for(finished.wait(), 5)
        result = (await asyncio.gather(pending, return_exceptions=True))[0]
    assert isinstance(result, asyncio.CancelledError if cancel else TimeoutError)
    assert workspace.operations.idle
