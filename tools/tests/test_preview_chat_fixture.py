"""Fixture append ordering and public-schema evidence through the actual event writer."""

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter
from tools.preview_chat_fixture import LEAD_ID, seed_preview_chat
from tools.preview_scenario import Candidate

from oversight.events import EventStore
from oversight_contracts.events import AgentStatus, LeadReply, LeadStatus, ServerEvent
from oversight_contracts.views import LeadView

pytestmark = pytest.mark.anyio


async def test_seed_preserves_prefix_and_validates_inputs(tmp_path: Path) -> None:
    text = await asyncio.to_thread(Path("fixtures/views/reference-cases.json").read_text)
    raw = json.loads(text)["events"][:6]
    adapter = TypeAdapter(ServerEvent)
    events = [adapter.validate_python(event) for event in raw]
    first = events[0]
    assert first.type == "run.started"
    async with EventStore(tmp_path / "events.sqlite", first.payload.run_id) as store:
        for event in events:
            await store.publish(event.payload)
        agent = next(event.payload for event in events if event.type == "agent.status")
        await store.publish(
            AgentStatus.model_validate({**agent.model_dump(), "state": "ready_for_review"})
        )
        before = list(store.committed_events())
        target = Candidate(first.payload.run_id, "agent-1", "change-1", 1, "head")
        for wrong in [
            Candidate(target.run_id, target.agent_id, target.change_id, 2, target.head_commit),
            Candidate(target.run_id, target.agent_id, target.change_id, 1, "stale"),
        ]:
            with pytest.raises(ValueError, match="exact current"):
                await seed_preview_chat(store, wrong)
            assert list(store.committed_events()) == before
        seqs = await seed_preview_chat(store, target)
        after = list(store.committed_events())
        assert after[: len(before)] == before
        assert seqs == list(range(len(before) + 1, len(before) + 5))
        assert [event.type for event in after[-4:]] == [
            "lead.status",
            "lead.view",
            "lead.reply",
            "lead.status",
        ]
        for event in after[-4:]:
            validated = adapter.validate_json(event.model_dump_json())
            assert isinstance(validated.payload, (LeadStatus, LeadView, LeadReply))
            assert validated.payload.agent_id == LEAD_ID
        with pytest.raises(ValueError, match="fresh lead"):
            await seed_preview_chat(store, target)
