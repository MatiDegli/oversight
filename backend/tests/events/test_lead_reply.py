"""Reply references see committed/earlier accepted same-lead human messages only."""

import asyncio
from pathlib import Path
from typing import Literal

import pytest
from pydantic import TypeAdapter

from oversight.events import EventRejected, EventStore, StoredEvent, read_store
from oversight.events.references import validate_references
from oversight_contracts.events import AgentMessageSent, LeadReply, LeadReplyEvent, ServerEvent

TS = "2026-11-01T17:04:57.000Z"


def reply(**updates: object) -> LeadReply:
    return LeadReply.model_validate(
        dict(
            agent_id="agent-1",
            answers_seq=None,
            text="Reply",
            through_seq=None,
            cut=None,
        )
        | updates
    )


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["live", "replay"])
async def test_references_and_rejection_isolation(
    tmp_path: Path, mode: Literal["live", "replay"]
) -> None:
    async with EventStore(tmp_path / "run.sqlite", "run-20261101-170457", mode=mode) as store:
        outcomes = await asyncio.gather(
            store.publish(AgentMessageSent(agent_id="agent-1", text="Inspect")),
            store.publish(reply(answers_seq=99)),
            store.publish(reply(answers_seq=1, through_seq=1)),
            store.publish(AgentMessageSent(agent_id="agent-2", text="Other lead")),
            return_exceptions=True,
        )
        assert isinstance(outcomes[0], StoredEvent) and outcomes[0].seq == 1
        assert isinstance(outcomes[1], EventRejected)
        assert isinstance(outcomes[2], StoredEvent) and outcomes[2].seq == 2
        assert isinstance(outcomes[3], StoredEvent) and outcomes[3].seq == 3
        for patch, reason in [
            ({"answers_seq": 4}, "earlier"),
            ({"answers_seq": 5}, "earlier"),
            ({"answers_seq": 2}, "requires agent.message"),
            ({"answers_seq": 3}, "same lead"),
            ({"through_seq": 4}, "lower"),
            ({"through_seq": 5}, "lower"),
        ]:
            with pytest.raises(EventRejected, match=reason):
                await store.publish(reply(**patch))
            assert store.last_seq == 3
        # Horizons are bounded seqs, not typed references: zero and any earlier event work.
        for horizon in (None, 0, 2, 3):
            assert (await store.publish(reply(answers_seq=1, through_seq=horizon))).seq > 3
        # model_construct cannot bypass live/ordinary null-text validation.
        invalid = reply().model_copy(update={"text": None})
        with pytest.raises(EventRejected, match="null reply text"):
            await store.publish(invalid)
        assert (await store.publish(reply())).seq == 8
    rows = read_store(store.path)
    assert [row.seq for row in rows] == list(range(1, 9))
    assert all(row.type == "lead.reply" for row in rows[3:])


@pytest.mark.parametrize("target", [2, 3])
def test_even_an_untrusted_reference_index_cannot_allow_self_or_future(target: int) -> None:
    source = TypeAdapter(ServerEvent).validate_python(
        dict(
            seq=target,
            ts=TS,
            type="agent.message",
            payload=dict(agent_id="agent-1", text="Inspect"),
        )
    )
    event = LeadReplyEvent(seq=2, ts=TS, type="lead.reply", payload=reply(answers_seq=target))
    with pytest.raises(ValueError, match="earlier"):
        validate_references(event, {target: source})


def test_absent_earlier_answer_is_rejected() -> None:
    event = LeadReplyEvent(seq=4, ts=TS, type="lead.reply", payload=reply(answers_seq=1))
    with pytest.raises(ValueError, match="absent or not earlier"):
        validate_references(event, {})


def test_replies_add_no_observations_to_any_fold() -> None:
    from dataclasses import replace

    from oversight.events import (
        build_usage_view,
        fold_activity,
        fold_stages,
        fold_supervision,
        fold_usage,
        price_usage,
        read_jsonl,
    )

    fixture = Path(__file__).resolve().parents[3] / "fixtures/events/handwritten-level1.jsonl"
    events = read_jsonl(fixture)
    assert events[0].type == "run.started"
    run_id = events[0].payload.run_id
    before = list(events)
    # A reply can advance the horizon without inventing a call, actor, change or interval.
    for cut in (None, "tool_calls", "model_calls", "time", "spend_brake"):
        events.append(
            LeadReplyEvent(
                seq=len(events) + 1,
                ts=events[-1].ts,
                type="lead.reply",
                payload=reply(text="Reply" if cut is None else None, cut=cut),
            )
        )
        for fold in (fold_usage, fold_activity, fold_supervision, price_usage):
            assert fold(events) == replace(fold(before), through_seq=len(events))
        assert fold_stages(events, "change-1") == fold_stages(before, "change-1")
        assert build_usage_view(run_id, events) == build_usage_view(run_id, before).model_copy(
            update={"seq": len(events)}
        )


def test_idle_does_not_change_state_agnostic_lead_readers() -> None:
    from oversight.events import build_usage_view, fold_activity, read_jsonl

    fixture = Path(__file__).resolve().parents[3] / "fixtures/events/handwritten-lead-thread.jsonl"
    events = read_jsonl(fixture)[:4]
    assert events[-1].type == "lead.status" and events[-1].payload.state == "idle"
    idle = events[-1]
    working = idle.model_copy(
        update={"payload": idle.payload.model_copy(update={"state": "working"})}
    )
    assert fold_activity(events) == fold_activity([*events[:-1], working])
    assert build_usage_view("run-20261101-170457", events) == build_usage_view(
        "run-20261101-170457", [*events[:-1], working]
    )
