"""Stop and shutdown drain each lead's own publications and clients, including initial status."""

# Lifecycle barriers deliberately inspect owned tasks and replace durable publications.
# pyright: reportPrivateUsage=false

import asyncio
from pathlib import Path
from typing import Any

import pytest
from backend.tests.api.lead_helpers import ThreadModels, idle, send, start_idle
from backend.tests.api.test_lead_concurrency import HeldScript
from live_helpers import DEADLINE_S, error_code, is_type, live_server, two_fakes

from oversight_contracts.events import AgentMessageSent, LeadReply, LeadStatus

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("barrier", ["starting", "message", "reply", "working", "idle"])
async def test_stop_isolated_at_publication_and_execution_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, barrier: str
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    factory = ThreadModels()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(two_fakes(tmp_path, max_leads=2), factory) as s,
    ):
        companion = await start_idle(s)
        original = s.store.publish
        original_status = s.live.leads._status

        async def status(lead: Any, state: str, detail: Any, **kwargs: Any) -> None:
            await original_status(lead, state, detail, **kwargs)
            if lead.status.agent_id == "agent-2" and state == barrier == "starting":
                entered.set()
                await release.wait()

        monkeypatch.setattr(s.live.leads, "_status", status)

        async def publish(payload: Any) -> Any:
            target = getattr(payload, "agent_id", None) == "agent-2"
            gate = (
                (isinstance(payload, LeadStatus) and payload.state == barrier != "starting")
                or (isinstance(payload, AgentMessageSent) and barrier == "message")
                or (isinstance(payload, LeadReply) and barrier == "reply")
            )
            if target and gate:
                entered.set()
                await release.wait()
            return await original(payload)

        monkeypatch.setattr(s.store, "publish", publish)
        caller = asyncio.create_task(s.client.post("/leads", json={"task": "target"}))
        if barrier == "message":
            assert (await caller).status_code == 201
            await idle(s, agent_id="agent-2")
            caller = asyncio.create_task(
                s.client.post("/leads/agent-2/message", json={"text": "target only"})
            )
        await entered.wait()
        target = s.live.leads.leads["agent-2"]
        assert (
            await s.client.post("/leads/agent-2/stop", json={"revision": target.revision})
        ).status_code == 202
        # Stopping still holds capacity, even when its first status is not committed yet.
        before = s.live._count
        assert error_code(await s.client.post("/leads", json={"task": "full"})) == (
            409,
            "wrong_state",
        )
        assert s.live._count == before
        await send(s, "companion stays live", companion)
        assert s.live.leads.leads[companion].phase == "idle"
        release.set()
        assert (await caller).status_code == (202 if barrier == "message" else 201)
        assert target.task is not None
        await target.task
        assert target.phase == "stopped"
        statuses = [
            e.payload
            for e in s.events()
            if e.type == "lead.status" and e.payload.agent_id == "agent-2"
        ]
        assert [x.state for x in statuses].count("stopped") == 1
        assert statuses[-1].state == "stopped"
        assert [x.revision for x in statuses] == list(range(1, len(statuses) + 1))
        assert all(x.limits == target.limits for x in statuses)
        assert await start_idle(s) == "agent-3"


async def test_stop_working_model_leaves_other_model_in_flight(tmp_path: Path) -> None:
    scripts, models = ThreadModels(), []
    built: asyncio.Queue[HeldScript] = asyncio.Queue()

    def factory(profile: Any, slot: Any) -> HeldScript:
        model = HeldScript(scripts(profile, slot))
        models.append(model)
        built.put_nowait(model)
        return model

    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), factory) as s:
        for _ in range(2):
            assert (await s.client.post("/leads", json={"task": "task"})).status_code == 201
            await (await built.get()).entered.wait()
        target = s.live.leads.leads["agent-1"]
        assert (
            await s.client.post("/leads/agent-1/stop", json={"revision": target.revision})
        ).status_code == 202
        await s.until(is_type("lead.status", agent_id="agent-1", state="stopped"))
        assert s.live.leads.leads["agent-2"].phase == "working"
        assert not models[1].closed
        models[1].release.set()
        await idle(s, agent_id="agent-2")
        await s.live.leads.close()
        assert all(m.closed for m in models)


async def test_shutdown_drains_all_initializations_admissions_and_prior_clients(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cleaning, release, entered, publishing, initializing = (asyncio.Event() for _ in range(5))
    factory, models = ThreadModels(), []

    class Client(HeldScript):
        async def aclose(self) -> None:
            if models[0] is self:
                cleaning.set()
                await release.wait()
            self.closed = True

    def make(profile: Any, slot: Any) -> Client:
        model = Client(factory(profile, slot))
        model.release.set()
        models.append(model)
        return model

    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), make) as s:
        await s.client.post("/leads", json={"task": "first"})
        await cleaning.wait()
        second = await start_idle(s)
        await send(s, agent_id=second)
        original = s.store.publish

        async def publish(payload: Any) -> Any:
            if isinstance(payload, AgentMessageSent):
                entered.set()
                await publishing.wait()
            elif isinstance(payload, LeadStatus) and payload.agent_id == "agent-3":
                if payload.state == "starting":
                    initializing.set()
                    await publishing.wait()
            return await original(payload)

        monkeypatch.setattr(s.store, "publish", publish)
        starting = asyncio.create_task(s.client.post("/leads", json={"task": "initializing"}))
        await initializing.wait()
        sending = asyncio.create_task(
            s.client.post(f"/leads/{second}/message", json={"text": "shutdown admission"})
        )
        await entered.wait()
        closing_entered = asyncio.Event()

        async def close() -> None:
            closing_entered.set()
            await s.live.leads.close()

        closing = asyncio.create_task(close())
        await closing_entered.wait()
        assert not closing.done()
        publishing.set()
        assert (await sending).status_code == 202
        assert (await starting).status_code == 201
        assert not models[0].closed
        release.set()
        await closing
        assert all(m.closed for m in models)
        assert (
            len(models) == 3
        )  # Two turns of the second lead, one of the first; none of the third.
        assert all(t.done() for lead in s.live.leads.leads.values() for t in lead.tasks)
        assert all(lead.phase == "halted" for lead in s.live.leads.leads.values())
        statuses = [e for e in s.events() if e.type == "lead.status"]
        await s.live.leads.close()
        assert [e for e in s.events() if e.type == "lead.status"] == statuses
