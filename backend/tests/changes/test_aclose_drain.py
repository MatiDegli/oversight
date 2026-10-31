"""Shutdown must let task completion callbacks retire finished work."""

import asyncio

import pytest
from changes_helpers import ADD_THRICE, Harness, queued_callbacks_ran
from decision_helpers import observed_target

from oversight.changes import _delivery
from oversight.events import EventPayload, EventRejected
from oversight_contracts.events import ChangeResolved, ErrorReport

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("outcome", ["ok", "error", "cancelled"])
@pytest.mark.parametrize("direct", [False, True], ids=["manager", "delivery"])
async def test_aclose_drains_completed_task_before_its_discard_callback(
    h: Harness, outcome: str, direct: bool
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
        await (_delivery.aclose(ctx) if direct else h.manager.aclose())

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


async def test_aclose_drains_rejection_report_spawned_by_close_out_retry(h: Harness) -> None:
    await h.run_agent(1, ADD_THRICE)
    h.fail_once, h.fail_times = ChangeResolved, 2
    await h.manager.reject("change-1", None, **observed_target(h.manager, "change-1"))
    ctx = h.manager._ctx

    async def reject_resolution(payload: EventPayload):
        if isinstance(payload, ChangeResolved):
            raise EventRejected("Close-out retry rejected")
        return await h.publish(payload)

    ctx.publish = reject_resolution
    h.gate = ErrorReport
    closing = asyncio.create_task(_delivery.aclose(ctx))
    try:
        async with asyncio.timeout(5):
            await h.at_gate.wait()
            await queued_callbacks_ran()
            assert not closing.done()
            h.open_gate.set()
            await closing
        assert not ctx.state.tasks
        assert any("Delivery rejected" in error.message for error in h.payloads(ErrorReport))
    finally:
        h.open_gate.set()
        await closing
