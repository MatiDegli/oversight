"""Shutdown must let task completion callbacks retire finished work."""

import asyncio

import pytest
from changes_helpers import Harness

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("outcome", ["ok", "error", "cancelled"])
async def test_aclose_drains_completed_task_before_its_discard_callback(
    h: Harness, outcome: str
) -> None:
    ctx = h.manager._ctx
    completed = asyncio.Event()

    async def work() -> None:
        # Wake the closer before Task queues its done callbacks. This is also the
        # ordering of a publisher notifying its caller just before it returns.
        completed.set()
        if outcome == "error":
            raise RuntimeError("Publication failed")
        if outcome == "cancelled":
            raise asyncio.CancelledError

    task = ctx.spawn(work())
    await completed.wait()
    assert task.done() and task in ctx.state.tasks

    async with asyncio.timeout(1):
        await h.manager.aclose()

    assert not ctx.state.tasks


async def test_aclose_drains_work_spawned_by_a_completion_callback(h: Harness) -> None:
    ctx = h.manager._ctx
    completed = asyncio.Event()
    delivered: list[str] = []

    async def work() -> None:
        completed.set()

    async def record() -> None:
        delivered.append("receipt")

    async def followup() -> None:
        assert ctx.state.closing
        await ctx.whole(record())

    task = ctx.spawn(work())
    task.add_done_callback(lambda _: ctx.spawn(followup()))
    await completed.wait()
    assert task.done() and task in ctx.state.tasks

    async with asyncio.timeout(1):
        await h.manager.aclose()

    assert delivered == ["receipt"]
    assert not ctx.state.tasks
