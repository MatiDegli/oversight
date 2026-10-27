"""Two agents on the live app, end to end: their calls interleaved in one log, back-to-back
approvals, and a shutdown with both mid-call. Slot B is closed by a constant until a merge on a
moved main is tested; `slot_b_open` opens it."""

import asyncio
from pathlib import Path

import pytest
from live_helpers import (
    DEADLINE_S,
    BySlot,
    GatedModel,
    Stepped,
    is_type,
    live_server,
    script_json,
    start,
    two_fakes,
)

from oversight.agents import parse_script

pytestmark = pytest.mark.anyio


async def test_two_agents_interleave_in_one_log(tmp_path: Path, slot_b_open: None) -> None:
    listing = {"tool_calls": [{"name": "list_files", "arguments": {}}]}
    three = parse_script(script_json("Look around.", [listing, listing, listing]))
    models = {"A": Stepped(three), "B": Stepped(three)}
    clients = BySlot(lambda _profile, slot: models[slot])
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), clients) as s:
        assert (await start(s)).status_code == 201
        assert (await start(s)).status_code == 201
        # One call each in turn, each waited for on its own `agent.step`: A1 B1 A2 B2 A3 B3.
        for step in (1, 2, 3):
            for slot, agent in (("A", "agent-1"), ("B", "agent-2")):
                models[slot].permits.release()
                await s.until(is_type("agent.step", agent_id=agent, step=step))
        events = s.events()  # each one validated against `ServerEvent` by the helper
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    steps = [(e.payload.agent_id, e.payload.step) for e in events if e.type == "agent.step"]
    assert steps == [(agent, step) for step in (1, 2, 3) for agent in ("agent-1", "agent-2")]
    statuses = {(e.payload.agent_id, e.payload.slot) for e in events if e.type == "agent.status"}
    assert statuses == {("agent-1", "A"), ("agent-2", "B")}


async def test_back_to_back_approvals_chain_their_deltas(tmp_path: Path, slot_b_open: None) -> None:
    # A and B edit different files. Both approvals are sent at once: the second waits for main's
    # lock, merges on the moved main and starts its delta where the first one ended.
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path)) as s:
        assert (await start(s, slot="A")).status_code == 201
        assert (await start(s, slot="B")).status_code == 201
        for agent in ("agent-1", "agent-2"):
            await s.until(is_type("agent.status", agent_id=agent, state="ready_for_review"))
        first = await s.client.post("/changes/change-1/approve")
        second = await s.client.post("/changes/change-2/approve")
        assert (first.status_code, second.status_code) == (202, 202)
        for agent in ("agent-1", "agent-2"):
            await s.until(is_type("agent.status", agent_id=agent, state="done"))
        events = s.events()
    merges = [(e.payload.change_id, e.payload.merge) for e in events if e.type == "change.resolved"]
    assert merges == [("change-1", "merged"), ("change-2", "merged")]
    deltas = [e.payload for e in events if e.type == "graph.delta"]
    assert len(deltas) == 2 and deltas[1].from_version == deltas[0].to_version


async def test_shutdown_mid_two_runs_ends_both(tmp_path: Path, slot_b_open: None) -> None:
    gated = {"A": GatedModel(), "B": GatedModel()}
    clients = BySlot(lambda _profile, slot: gated[slot])
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), clients) as s:
        created = [(await start(s)).json(), (await start(s)).json()]
        assert [c["slot"] for c in created] == ["A", "B"]
        for model in gated.values():
            await model.entered.wait()
        await s.stop()
        assert all(model.cancelled.is_set() for model in gated.values())
        assert s.live._runs == {}  # pyright: ignore[reportPrivateUsage]
        # Both changes were ended before the store closed: none is left `working`, which no
        # decision can take, so the manager's drain had nothing to wait for.
        manager = s.live._manager  # pyright: ignore[reportPrivateUsage]
        assert manager is not None
        for agent in created:
            assert manager._agents[agent["agent_id"]].state == "unproposed"  # pyright: ignore[reportPrivateUsage]
