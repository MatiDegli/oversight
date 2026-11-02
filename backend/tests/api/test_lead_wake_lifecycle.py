"""Subscriptions end before the store closes, including startup and wake shutdown races."""

# Ownership and cleanup assertions intentionally inspect retained asyncio tasks.
# pyright: reportPrivateUsage=false

import asyncio
from pathlib import Path
from typing import Any

import pytest
from backend.tests.api.wake_helpers import (
    ControlledClock,
    Models,
    caught_up,
    owned,
    ready,
    spin,
    triggers,
)
from backend.tests.events.test_lead_wake import plan
from live_helpers import DEADLINE_S, live_server, two_fakes

from oversight_contracts.events import LeadStatus

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("ending", ["stop", "failure"])
async def test_terminal_lead_cancels_open_watcher(tmp_path: Path, monkeypatch, ending):
    factory, clock = Models(), ControlledClock()
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), factory) as s:
        await ready(s, clock)
        lead = owned(s)
        watcher = lead.watching
        assert watcher and not watcher.done()
        if ending == "stop":
            response = await s.client.post("/leads/agent-1/stop", json={"revision": lead.revision})
            assert response.status_code == 202
        else:

            def failed_factory(*args):
                raise RuntimeError("fake failure")

            monkeypatch.setattr(s.live, "_client_factory", failed_factory)
            response = await s.client.post("/leads/agent-1/message", json={"text": "Fail"})
            assert response.status_code == 202
        await lead.task
        await spin(watcher.done)
        assert lead.watcher.subscription.done()
        assert not clock.waiters
        # Closing the store has played no part in ending either subscription or timer.
        await s.store.publish(plan().payload)


@pytest.mark.parametrize("initializing", [False, True])
async def test_close_cancels_watchers_before_gathers_with_store_open(
    tmp_path: Path, monkeypatch, initializing
):
    factory, clock = Models(), ControlledClock()
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), factory) as s:
        service = s.live.leads
        if not initializing:
            await ready(s, clock)
            await service.close()
            assert owned(s).watching.done() and owned(s).watcher.subscription.done()
        else:
            entered, release = asyncio.Event(), asyncio.Event()
            original = s.store.publish

            async def publish(payload: Any):
                if isinstance(payload, LeadStatus) and payload.state == "starting":
                    entered.set()
                    await release.wait()
                return await original(payload)

            monkeypatch.setattr(s.store, "publish", publish)
            starting = asyncio.create_task(s.client.post("/leads", json={"task": "Start"}))
            await entered.wait()
            closing = asyncio.create_task(service.close())
            await spin(lambda: service._closing)
            assert not closing.done()
            release.set()
            assert (await starting).status_code == 201
            await closing
            assert owned(s).watching is None and not factory.built
        assert not clock.waiters
        assert owned(s).phase == "halted"
        await s.store.publish(plan().payload)


async def test_shutdown_during_wake_closes_client_once(tmp_path: Path):
    factory, clock = Models(), ControlledClock()
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), factory) as s:
        await ready(s, clock)
        await triggers(s)
        factory.release.clear()
        factory.entered.clear()
        clock.advance(5)
        await factory.entered.wait()
        await s.live.leads.close()
        assert sorted(factory.closed) == [1, 2]
        assert owned(s).phase == "halted" and owned(s).watching.done()
        assert owned(s).watcher.subscription.done() and not clock.waiters
        await s.store.publish(plan().payload)


async def test_restart_discards_pending_wakes_and_calls_no_model(tmp_path: Path):
    clock, factory = ControlledClock(), Models()
    settings = two_fakes(tmp_path)
    async with asyncio.timeout(DEADLINE_S), live_server(settings, factory) as s:
        await ready(s, clock)
        await triggers(s)
        assert owned(s).watcher.pending
        await s.live.leads.close()
        assert owned(s).watching.done() and owned(s).watcher.subscription.done()
    fresh = Models()
    async with asyncio.timeout(DEADLINE_S), live_server(settings, fresh) as restarted:
        assert restarted.live.leads.leads == {}
        assert not fresh.built
        assert not any(e.type == "lead.reply" for e in restarted.events())


async def test_zero_disables_wake_execution_and_human_still_takes_digest(tmp_path: Path):
    factory, clock = Models(), ControlledClock()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(two_fakes(tmp_path, lead_wakes=0), factory) as s,
    ):
        await ready(s, clock)
        seqs = await triggers(s)
        clock.advance(100)
        await caught_up(s)
        assert owned(s).wakes == 0 and len(factory.built) == 1
        assert (
            await s.client.post("/leads/agent-1/message", json={"text": "Summarize"})
        ).status_code == 202
        await owned(s).task
        assert owned(s).run.history.notices[-1].triggers == tuple(seqs)
        assert owned(s).wakes == 0
