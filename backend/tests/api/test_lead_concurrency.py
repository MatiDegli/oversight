"""Concurrent lead admission and isolated, overlapping threads use scripted clients only."""

# Tests inspect run-owned state and control each client's admission explicitly.
# pyright: reportPrivateUsage=false

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from backend.tests.agents.lead_helpers import call, plan, turn
from backend.tests.api.lead_helpers import ThreadModels, idle, send, start_idle
from live_helpers import DEADLINE_S, error_code, live_server, two_fakes

from oversight.agents import ScriptedModel
from oversight_contracts.events import LeadStatus

pytestmark = pytest.mark.anyio


class HeldScript(ScriptedModel):
    """Every client has its own gate, request history and cleanup receipt."""

    def __init__(self, model: ScriptedModel) -> None:
        self.__dict__.update(model.__dict__)
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.closed = False

    async def complete(self, messages: Any, tools: Any) -> Any:
        self.entered.set()
        await self.release.wait()
        return await super().complete(messages, tools)

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.parametrize("capacity", [None, 1, 2, 7])
async def test_idle_capacity_refusal_has_no_side_effect_and_stop_frees_it(
    tmp_path: Path, capacity: int | None
) -> None:
    factory = ThreadModels()
    settings = two_fakes(tmp_path, max_agents=2)
    if capacity is not None:
        settings = replace(settings, max_leads=capacity)
    capacity = capacity or 6
    async with asyncio.timeout(DEADLINE_S), live_server(settings, factory) as s:
        ids = [await start_idle(s) for _ in range(capacity)]
        before = s.store.last_seq, s.live._count, len(factory.built)
        response = await s.client.post("/leads", json={"task": "overflow"})
        assert error_code(response) == (409, "wrong_state")
        detail = response.json()["error"]["message"]
        assert f"OVERSIGHT_MAX_LEADS is {capacity}" in detail
        assert "Stop a lead" in detail and "next run" in detail
        assert (s.store.last_seq, s.live._count, len(factory.built)) == before
        assert list(s.live.leads.leads) == ids and not s.live._agents
        first = s.live.leads.leads[ids[0]]
        assert (
            await s.client.post(f"/leads/{ids[0]}/stop", json={"revision": first.revision})
        ).status_code == 202
        assert first.task is not None
        await first.task
        if capacity > 1:
            await send(s, agent_id=ids[-1])
        assert await start_idle(s) not in ids
        assert all(slot is None for slot in s.live._slots.values())


async def test_simultaneous_starts_reserve_the_last_place_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    factory = ThreadModels()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(two_fakes(tmp_path, max_leads=2), factory) as s,
    ):
        await start_idle(s)
        original = s.store.publish

        async def publish(payload: Any) -> Any:
            if isinstance(payload, LeadStatus) and payload.state == "starting":
                entered.set()
                await release.wait()
            return await original(payload)

        monkeypatch.setattr(s.store, "publish", publish)
        first = asyncio.create_task(s.client.post("/leads", json={"task": "last place"}))
        await entered.wait()
        before = s.store.last_seq
        refused = await s.client.post("/leads", json={"task": "contender"})
        assert error_code(refused) == (409, "wrong_state")
        assert s.store.last_seq == before and len(factory.built) == 1
        assert len(s.live.leads.leads) == s.live._count == 2
        release.set()
        assert (await first).status_code == 201
        await idle(s, agent_id="agent-2")


async def test_overlapping_calls_interleaved_messages_and_plan_budgets_are_isolated(
    tmp_path: Path,
) -> None:
    scripts = ThreadModels(
        [
            [turn(call("status"), call("request_plan", **plan())), {"text": label}]
            for label in ("alpha opening", "beta opening", "beta followup", "alpha followup")
        ]
    )
    models: list[HeldScript] = []
    built: asyncio.Queue[HeldScript] = asyncio.Queue()

    def factory(profile: Any, slot: Any) -> HeldScript:
        model = HeldScript(scripts(profile, slot))
        models.append(model)
        built.put_nowait(model)
        return model

    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(two_fakes(tmp_path, lead_max_plans=1), factory) as s,
    ):
        for task in ("alpha task", "beta task"):
            assert (await s.client.post("/leads", json={"task": task})).status_code == 201
            await (await built.get()).entered.wait()
        assert len(models) == 2 and not any(m.closed for m in models)
        models[0].release.set()
        await idle(s, agent_id="agent-1")
        assert not models[1].closed  # The other call is still in flight.
        models[1].release.set()
        await idle(s, agent_id="agent-2")
        for agent_id in ("agent-2", "agent-1"):
            after = s.store.last_seq
            assert (
                await s.client.post(f"/leads/{agent_id}/message", json={"text": agent_id + " only"})
            ).status_code == 202
            client = await built.get()
            await client.entered.wait()
            client.release.set()
            await idle(s, after, agent_id)
        for agent_id, label, other in (("agent-1", "alpha", "beta"), ("agent-2", "beta", "alpha")):
            lead = s.live.leads.leads[agent_id]
            assert lead.run is not None and lead.turns == 2 and lead.run.steps == 4
            assert lead.run.turn_steps == 2 and lead.run.turns == 2
            assert len(lead.run.tools.plan_ids) == 1
            events = [e for e in s.events() if getattr(e.payload, "agent_id", None) == agent_id]
            statuses = [e.payload for e in events if e.type == "lead.status"]
            assert [e.revision for e in statuses] == list(range(1, len(statuses) + 1))
            assert [e.step for e in statuses] == sorted(e.step for e in statuses)
            replies = [e for e in events if e.type == "lead.reply"]
            message = next(e for e in events if e.type == "agent.message")
            assert [e.payload.text for e in replies] == [label + " opening", label + " followup"]
            assert replies[0].payload.answers_seq is None
            assert replies[1].payload.answers_seq == message.seq
            assert message.seq < replies[1].payload.through_seq < replies[1].seq
            client = models[3 if agent_id == "agent-1" else 2]
            assert f"{other} opening" not in str(client.requests)
            assert f"{label} opening" in str(client.requests)
            assert "already requested 1 plans" in str(client.requests)
            plans = [p for p in s.live.plans.plans.values() if p.started_by == agent_id]
            assert len(plans) == 1 and plans[0].state == "proposed"
        assert not s.live._agents and not any(e.type == "queue.updated" for e in s.events())
        assert all(m.closed for m in models)
