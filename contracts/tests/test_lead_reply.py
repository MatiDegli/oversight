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
    for answers_seq in (None, 2):
        for cut in CUTS:
            for text in ("Reply", "x" * 4096, None):
                # Serialized exports retain nullable text even without a cut.
                payload = LeadReply.model_validate(
                    dict(
                        agent_id="agent-1",
                        answers_seq=answers_seq,
                        text=text,
                        through_seq=None,
                        cut=cut,
                    ),
                    context={"message_text_withheld": True},
                )
                events.append(
                    EVENTS.validate_python(
                        dict(
                            seq=20, ts=events[0].ts, type="lead.reply", payload=payload.model_dump()
                        ),
                        context={"message_text_withheld": True},
                    )
                )
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


@pytest.mark.parametrize(
    "patch",
    [
        {"text": ""},
        {"text": " \t\n\r"},
        {"text": "\u2003\u00a0"},
        {"text": "x" * 4097},
        {"cut": "tokens"},
        {"text": None},
    ],
)
@pytest.mark.parametrize("context", [None, {"live": True}])
def test_ordinary_and_live_reject_invalid_replies(
    patch: dict[str, object], context: object
) -> None:
    from pydantic import ValidationError

    body = dict(agent_id="agent-1", answers_seq=None, text="Reply", through_seq=None, cut=None)
    with pytest.raises(ValidationError):
        LeadReply.model_validate({**body, **patch}, context=context)


def test_withholding_context_is_explicit_and_never_overrides_live() -> None:
    from pydantic import ValidationError

    body = dict(agent_id="agent-1", answers_seq=None, text=None, through_seq=None, cut=None)
    reply = LeadReply.model_validate(body, context={"message_text_withheld": True})
    assert reply.model_dump() == body
    assert (
        LeadReply.model_validate_json(
            reply.model_dump_json(), context={"message_text_withheld": True}
        )
        == reply
    )
    for context in (
        {},
        {"message_text_withheld": False},
        {"message_text_withheld": True, "live": True},
    ):
        with pytest.raises(ValidationError, match="null reply text"):
            LeadReply.model_validate(body, context=context)


def test_existing_message_whitespace_shape_is_preserved() -> None:
    from oversight_contracts.commands import AgentMessage
    from oversight_contracts.events import AgentMessageSent

    assert AgentMessage(text="  ").text == "  "
    assert AgentMessageSent(agent_id="agent-1", text="  ").text == "  "
