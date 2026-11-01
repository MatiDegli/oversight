"""Contracts 1.15 declares lead thread replies without producing them."""

import json
from pathlib import Path
from typing import Literal

import pytest
from pydantic import TypeAdapter

from oversight.workspace import run_process
from oversight_contracts.events import LeadReply, LeadReplyEvent, LeadStatus, ServerEvent

ROOT = Path(__file__).resolve().parents[2]
EVENTS = TypeAdapter(ServerEvent)
CUTS = ["tool_calls", "model_calls", "time", "spend_brake", None]


@pytest.mark.parametrize("answers_seq", [None, 2])
@pytest.mark.parametrize("cut", CUTS)
@pytest.mark.parametrize("text", ["Reply", "x" * 4096, None])
def test_reply_event_round_trip(answers_seq: int | None, cut: str | None, text: str | None) -> None:
    if text is None and cut is None:
        return  # The publication-only shape is covered at its receipt boundary.
    reply = LeadReply.model_validate(
        dict(agent_id="agent-1", answers_seq=answers_seq, text=text, through_seq=3, cut=cut)
    )
    event = LeadReplyEvent(seq=4, ts="2026-11-01T17:04:57.000Z", type="lead.reply", payload=reply)
    assert EVENTS.validate_json(EVENTS.dump_json(event)) == event
    assert set(reply.model_dump()) == {"agent_id", "answers_seq", "text", "through_seq", "cut"}


@pytest.mark.parametrize("state", ["idle", "done"])
def test_idle_and_historical_done_round_trip(state: Literal["idle", "done"]) -> None:
    status = LeadStatus(
        agent_id="agent-1",
        revision=2,
        task="Coordinate",
        model="fake",
        state=state,
        step=1,
        detail=None,
    )
    assert LeadStatus.model_validate_json(status.model_dump_json()) == status


@pytest.mark.anyio
async def test_thread_fixture_against_generated_schema(tmp_path: Path) -> None:
    fixture = ROOT / "fixtures/events/handwritten-lead-thread.jsonl"
    events = [EVENTS.validate_json(line) for line in fixture.read_text().splitlines()]
    assert [event.seq for event in events] == list(range(1, len(events) + 1))
    serialized = tmp_path / "events.json"
    serialized.write_text(
        json.dumps(
            [dict(source=fixture.name, event=event.model_dump(mode="json")) for event in events]
        )
    )
    result = await run_process(
        ["node", "contracts/tests/validate_serialized.cjs", str(serialized)], cwd=ROOT, timeout_s=60
    )
    assert result.returncode == 0, result.stdout + result.stderr
