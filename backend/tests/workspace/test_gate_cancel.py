"""Cancellation during FIFO cleanup takes precedence over the gate's own timeout."""

import asyncio

import pytest

pytestmark = pytest.mark.anyio


async def test_cancel_during_timeout_cleanup_is_not_lost(gate_factory, monkeypatch):
    gate = gate_factory("cancel-during-cleanup")
    entered, release = asyncio.Event(), asyncio.Event()
    wait = asyncio.wait

    async def held_cleanup(*args, **kwargs):
        entered.set()
        await release.wait()
        return await wait(*args, **kwargs)

    monkeypatch.setattr(asyncio, "wait", held_cleanup)
    caller = asyncio.create_task(gate.open_gate(timeout_s=0))
    try:
        async with asyncio.timeout(5):
            await entered.wait()
            caller.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert caller.cancelled()
    finally:
        release.set()
        await asyncio.gather(caller, return_exceptions=True)
