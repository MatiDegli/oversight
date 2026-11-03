"""Production dispatch publishes under the lead lock; refusals leave the log intact."""

# Lifecycle race tests inspect the service's owned lead and publication callback.
# pyright: reportPrivateUsage=false

import asyncio
import json
from pathlib import Path

import pytest
from backend.tests.agents.lead_helpers import call, turn
from backend.tests.agents.test_lead_views import INVALID
from backend.tests.api.lead_helpers import ThreadModels, send, start_idle
from backend.tests.events.test_lead_view import EVENTS
from live_helpers import DEADLINE_S, live_server, two_fakes

pytestmark = pytest.mark.anyio


async def prepared(s):
    await start_idle(s)
    for event in EVENTS[1:12]:
        await s.store.publish(event.payload)
    lead = s.live.leads.leads["agent-1"]
    tools = s.live.leads._tools(lead)
    outcome = await tools.call("status", "{}")
    assert outcome.ok
    for label in ("agent", "path", "file", "behavior", "moment"):
        assert f"eligible {label} data:" in outcome.content
    return lead, tools


@pytest.mark.parametrize(
    "arguments",
    [
        {"kind": "highlight", "agent_ids": ["agent-1"], "paths": ["extra.py"]},
        {"kind": "files", "files": [{"change_id": "change-1", "path": "a.py"}]},
        {"kind": "behavior", "change_id": "change-1"},
        {"kind": "moment", "event_seq": 6},
    ],
)
async def test_accepted_dispatch_appends_exactly_one_view(tmp_path: Path, arguments: dict) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), ThreadModels()) as s:
        _, tools = await prepared(s)
        # Translate the shared-fixture milestone to this run's committed sequence.
        if arguments["kind"] == "moment":
            arguments = arguments | {
                "event_seq": next(e.seq for e in s.events() if e.type == "change.proposed")
            }
        before = s.store.last_seq
        outcome = await tools.call("show_view", json.dumps(arguments))
        assert outcome.ok and s.store.last_seq == before + 1
        view = s.events()[-1]
        assert view.type == "lead.view" and view.payload.agent_id == "agent-1"
        assert view.payload.through_seq == before
        assert outcome.content == f"Shown {arguments['kind']} view through event {before}."


@pytest.mark.parametrize(
    "arguments",
    [
        *INVALID,
        {"kind": "highlight", "agent_ids": ["agent-999"]},
        {"kind": "highlight", "paths": ["missing.py"]},
        {"kind": "files", "files": [{"change_id": "change-2", "path": "a.py"}]},
        {"kind": "behavior", "change_id": "change-999"},
        {"kind": "moment", "event_seq": 99999},
        {"kind": "moment", "event_seq": 1},
    ],
)
async def test_every_adapter_refusal_appends_nothing(tmp_path: Path, arguments: dict) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), ThreadModels()) as s:
        _, tools = await prepared(s)
        before = s.store.committed_events()
        assert not (await tools.call("show_view", json.dumps(arguments))).ok
        assert s.store.committed_events() == before


@pytest.mark.parametrize("closing", ["stopping", "service", "live"])
async def test_refusal_under_lead_lock_after_stop_or_closing(tmp_path: Path, closing: str) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), ThreadModels()) as s:
        lead, tools = await prepared(s)
        await lead.lock.acquire()
        pending = asyncio.create_task(
            tools.call("show_view", '{"kind":"behavior","change_id":"change-1"}')
        )
        await asyncio.sleep(0)
        assert not pending.done()
        if closing == "stopping":
            lead.stopping = True
        elif closing == "service":
            s.live.leads._closing = True
        else:
            s.live._closing = True
        before = s.store.committed_events()
        lead.lock.release()
        assert not (await pending).ok
        assert s.store.committed_events() == before
        # Restore injected flags so normal context cleanup owns the full shutdown.
        lead.stopping = False
        s.live.leads._closing = s.live._closing = False


async def test_old_milestone_unmeasured_behavior_and_malformed_json_append_nothing(
    tmp_path: Path,
) -> None:
    from oversight_contracts.events import LeadReply

    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), ThreadModels()) as s:
        _, tools = await prepared(s)
        first = next(e.seq for e in s.events() if e.type == "change.proposed")
        for _ in range(33):
            await s.store.publish(
                LeadReply(
                    agent_id="agent-1",
                    answers_seq=None,
                    text="Milestone",
                    through_seq=None,
                    cut=None,
                )
            )
        proposal = EVENTS[7].payload.model_copy(update={"revision": 3})
        await s.store.publish(proposal)
        assert (await tools.call("status", "{}")).ok
        before = s.store.committed_events()
        for arguments in [
            json.dumps({"kind": "moment", "event_seq": first}),
            '{"kind":"behavior","change_id":"change-1"}',
            "not JSON",
        ]:
            assert not (await tools.call("show_view", arguments)).ok
            assert s.store.committed_events() == before


@pytest.mark.parametrize("kind", ["highlight", "files", "behavior", "moment"])
@pytest.mark.parametrize("correct", [False, True])
async def test_scripted_production_status_view_finish(
    tmp_path: Path, kind: str, correct: bool
) -> None:
    models = ThreadModels()
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), models) as s:
        _, _ = await prepared(s)
        arguments = {
            "highlight": {"agent_ids": ["agent-1"], "paths": ["extra.py"]},
            "files": {"files": [{"change_id": "change-1", "path": "a.py"}]},
            "behavior": {"change_id": "change-1"},
            "moment": {"event_seq": next(e.seq for e in s.events() if e.type == "change.proposed")},
        }[kind] | {"kind": kind}
        script = [turn(call("status"))]
        if correct:
            script.append(turn(call("show_view", **(arguments | {"facts": "invented"}))))
        script += [turn(call("show_view", **arguments)), turn(call("finish", reply="Shown."))]
        models.scripts.append(script)
        before = s.store.last_seq
        await send(s)
        views = [e for e in s.events() if e.seq > before and e.type == "lead.view"]
        assert len(views) == 1 and views[0].payload.view.kind == kind
        assert views[0].payload.through_seq < views[0].seq
        # Only status advances lead.status; view acceptance and refusal emit no step.
        statuses = [e for e in s.events() if e.seq > before and e.type == "lead.status"]
        assert [e.payload.step for e in statuses] == [0, 1, 3 + correct]
        observations = str(models.built[-1].requests)
        assert f"Shown {kind} view through event" in observations
        assert ("View refused." in observations) is correct


async def test_status_window_is_frozen_until_next_successful_status(tmp_path: Path) -> None:
    from oversight_contracts.events import LeadReply

    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), ThreadModels()) as s:
        _, tools = await prepared(s)
        shown_seq = tools.view_S
        old = next(e.seq for e in s.events() if e.type == "change.proposed")
        new = await s.store.publish(
            LeadReply(
                agent_id="agent-1", text="Later", answers_seq=None, through_seq=None, cut=None
            )
        )
        before = s.store.committed_events()
        assert not (
            await tools.call("show_view", json.dumps({"kind": "moment", "event_seq": new.seq}))
        ).ok
        assert s.store.committed_events() == before
        assert (await tools.call("show_view", json.dumps({"kind": "moment", "event_seq": old}))).ok
        assert s.events()[-1].payload.through_seq == shown_seq
        assert (await tools.call("status", "{}")).ok
        assert (
            await tools.call("show_view", json.dumps({"kind": "moment", "event_seq": new.seq}))
        ).ok


async def test_production_inventory_overflow_invalidates_previous_status(tmp_path: Path) -> None:
    from oversight_contracts.graph import GraphNode, GraphSnapshot

    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), ThreadModels()) as s:
        _, tools = await prepared(s)
        await s.store.publish(
            GraphSnapshot(
                version="overflow",
                commit="base",
                edges=[],
                parse_errors=[],
                positions={},
                nodes=[
                    GraphNode(
                        id=f"node-{i}",
                        kind="function",
                        name="long",
                        qualname=f"long{i}",
                        path=f"path{i}/" + "a" * 490,
                        start_line=1,
                        end_line=1,
                        parent_id=None,
                        is_test=False,
                    )
                    for i in range(100)
                ],
            )
        )
        before = s.store.committed_events()
        outcome = await tools.call("status", "{}")
        assert not outcome.ok and "no views are eligible" in outcome.content
        assert not tools.status_seen and tools.view_S is None
        assert not (await tools.call("show_view", '{"kind":"behavior","change_id":"change-1"}')).ok
        assert s.store.committed_events() == before
