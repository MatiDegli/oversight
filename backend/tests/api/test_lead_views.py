"""Dormant callbacks publish under the lead lock; every refusal leaves the log intact."""

# Lifecycle race tests inspect the service's owned lead and dormant callback.
# pyright: reportPrivateUsage=false

import asyncio
import json
from pathlib import Path

import pytest
from backend.tests.agents.test_lead_views import INVALID
from backend.tests.api.lead_helpers import ThreadModels, start_idle
from backend.tests.events.test_lead_view import EVENTS
from live_helpers import DEADLINE_S, live_server, two_fakes

pytestmark = pytest.mark.anyio


async def prepared(s):
    await start_idle(s)
    for event in EVENTS[1:12]:
        await s.store.publish(event.payload)
    lead = s.live.leads.leads["agent-1"]
    tools = s.live.leads._tools(lead)
    tools.status = lambda: s.store.lead_status(include_views=True)
    assert (await tools.call("status", "{}")).ok
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
async def test_accepted_callback_appends_exactly_one_view(tmp_path: Path, arguments: dict) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), ThreadModels()) as s:
        _, tools = await prepared(s)
        # Translate the shared-fixture milestone to this run's committed sequence.
        if arguments["kind"] == "moment":
            arguments = arguments | {
                "event_seq": next(e.seq for e in s.events() if e.type == "change.proposed")
            }
        before = s.store.last_seq
        outcome = await tools.show_view(json.dumps(arguments))
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
        assert not (await tools.show_view(json.dumps(arguments))).ok
        assert s.store.committed_events() == before


@pytest.mark.parametrize("closing", ["stopping", "service", "live"])
async def test_refusal_under_lead_lock_after_stop_or_closing(tmp_path: Path, closing: str) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), ThreadModels()) as s:
        lead, tools = await prepared(s)
        await lead.lock.acquire()
        pending = asyncio.create_task(tools.show_view('{"kind":"behavior","change_id":"change-1"}'))
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
            assert not (await tools.show_view(arguments)).ok
            assert s.store.committed_events() == before
