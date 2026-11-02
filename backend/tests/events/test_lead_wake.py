"""Wake attribution and reply references share the same closed, committed-event catalog."""

import asyncio
from pathlib import Path
from typing import Literal

import pytest
from pydantic import TypeAdapter

from oversight.events import EventRejected, EventStore, is_lead_wake_trigger, read_jsonl
from oversight.events.references import validate_references
from oversight_contracts.events import ServerEvent

EVENT = TypeAdapter(ServerEvent)
TS = "2026-11-02T17:00:00.000Z"
ROOT = Path(__file__).resolve().parents[3]
LOGS = sorted((ROOT / "fixtures").rglob("*.jsonl")) + sorted(
    (ROOT / "frontend/src").rglob("*.jsonl")
)


def plan(seq: int = 1, **updates: object) -> ServerEvent:
    return event(
        seq,
        "plan.updated",
        dict(
            plan_id="plan-1",
            revision=seq,
            task="Work",
            graph_version="v1",
            state="planning",
            started_by="agent-1",
        )
        | updates,
    )


def status(seq: int = 2, **updates: object) -> ServerEvent:
    return event(
        seq,
        "agent.status",
        dict(
            agent_id="agent-2",
            slot="A",
            change_id="change-1",
            task="Work",
            model="fake",
            state="working",
            step=1,
            max_steps=30,
            detail=None,
            plan_id="plan-1",
        )
        | updates,
    )


def suite_execution(seq: int = 3, *, outcome: str = "failed", **updates: object) -> ServerEvent:
    return event(
        seq,
        "tests.executed",
        dict(
            change_id="change-1",
            agent_id="agent-2",
            revision=1,
            phase="combined",
            tested_commit="head",
            tested_tree="tree",
            suite=dict(
                outcome=outcome,
                passed=0,
                failed=1,
                skipped=0,
                duration_ms=1,
                failed_tests=[],
            ),
        )
        | updates,
    )


def reply(seq: int = 10, **updates: object) -> ServerEvent:
    return event(
        seq,
        "lead.reply",
        dict(
            agent_id="agent-1",
            answers_seq=None,
            text="Reply",
            through_seq=None,
            cut=None,
        )
        | updates,
    )


def event(seq: int, kind: str, payload: dict[str, object]) -> ServerEvent:
    return EVENT.validate_python(dict(seq=seq, ts=TS, type=kind, payload=payload))


def index(*events: ServerEvent) -> dict[int, ServerEvent]:
    return {row.seq: row for row in events}


@pytest.mark.parametrize("outcome", ["failed", "error", "timeout"])
def test_combined_nonpassing_suite_is_a_trigger(outcome: str) -> None:
    assert is_lead_wake_trigger(
        index(plan(), status(), suite_execution(outcome=outcome)), 3, "agent-1"
    )


def test_halted_status_is_a_trigger_using_its_own_plan() -> None:
    assert is_lead_wake_trigger(
        index(plan(), status(plan_id=None), status(3, state="halted")), 3, "agent-1"
    )


@pytest.mark.parametrize("state", ["completed", "failed"])
def test_first_terminal_plan_transition_is_a_trigger(state: str) -> None:
    assert is_lead_wake_trigger(index(plan(), plan(3, state=state)), 3, "agent-1")


@pytest.mark.parametrize(
    "target", [suite_execution(), status(3, state="halted"), plan(3, state="failed")]
)
def test_another_leads_plan_is_not_a_trigger(target: ServerEvent) -> None:
    assert not is_lead_wake_trigger(
        index(plan(started_by="agent-3"), status(), target), 3, "agent-1"
    )


@pytest.mark.parametrize("phase", ["agent", "proposal", "base_probe", "head_probe"])
def test_noncombined_tests_are_not_triggers(phase: str) -> None:
    assert not is_lead_wake_trigger(
        index(plan(), status(), suite_execution(phase=phase)), 3, "agent-1"
    )


def test_passed_combined_tests_are_not_a_trigger() -> None:
    assert not is_lead_wake_trigger(
        index(plan(), status(), suite_execution(outcome="passed")), 3, "agent-1"
    )


@pytest.mark.parametrize(
    "first,last",
    [
        ("completed", "completed"),
        ("failed", "failed"),
        ("completed", "failed"),
        ("failed", "completed"),
    ],
)
def test_later_terminal_revision_never_triggers_again(first: str, last: str) -> None:
    assert not is_lead_wake_trigger(
        index(plan(), plan(2, state=first), plan(3), plan(4, state=last)), 4, "agent-1"
    )
    assert not is_lead_wake_trigger(
        index(plan(), plan(2, state=first), plan(3, state=last)), 3, "agent-1"
    )


def test_suite_uses_latest_status_even_when_it_names_no_plan() -> None:
    assert not is_lead_wake_trigger(
        index(plan(), status(), status(3, plan_id=None), suite_execution(4)), 4, "agent-1"
    )


def test_suite_without_an_earlier_status_is_not_a_trigger() -> None:
    assert not is_lead_wake_trigger(index(plan(), suite_execution(), status(4)), 3, "agent-1")


def test_suite_uses_status_plan_rather_than_status_started_by() -> None:
    assert is_lead_wake_trigger(
        index(plan(), status(started_by="agent-3"), suite_execution()), 3, "agent-1"
    )


@pytest.mark.parametrize("target", [status(1, state="halted"), plan(1, state="failed")])
def test_event_before_plan_exists_is_not_a_trigger(target: ServerEvent) -> None:
    assert not is_lead_wake_trigger(index(target, plan(2)), 1, "agent-1")


@pytest.mark.parametrize(
    "target", [suite_execution(4), status(4, state="halted"), plan(4, state="failed")]
)
def test_latest_prior_plan_owns_trigger_despite_insertion_order_and_future(
    target: ServerEvent,
) -> None:
    prior = index(plan(5), target, plan(3, started_by="agent-3"), status(), plan())
    assert is_lead_wake_trigger(prior, 4, "agent-3")
    assert not is_lead_wake_trigger(prior, 4, "agent-1")


def test_terminal_event_cannot_attribute_itself_to_a_new_owner() -> None:
    prior = index(plan(), plan(2, state="failed", started_by="agent-3"))
    assert is_lead_wake_trigger(prior, 2, "agent-1")
    assert not is_lead_wake_trigger(prior, 2, "agent-3")


def test_unlisted_events_states_and_absent_seqs_are_not_triggers() -> None:
    prior = index(plan(), status(), reply(3))
    for seq in (1, 2, 3, 99):
        assert not is_lead_wake_trigger(prior, seq, "agent-1")
    assert not is_lead_wake_trigger(
        index(plan(started_by=None), status(state="halted")), 2, "agent-1"
    )


def test_wake_can_name_multiple_unconsumed_triggers() -> None:
    prior = index(
        plan(), reply(2), status(3, state="halted"), suite_execution(4), plan(5, state="failed")
    )
    validate_references(reply(woken_by=[3, 4, 5]), prior)


@pytest.mark.parametrize("seq", [10, 11])
def test_wake_cannot_name_self_or_future_even_in_an_untrusted_index(seq: int) -> None:
    prior = index(plan(), reply(2), status(seq, state="halted"))
    with pytest.raises(ValueError, match="earlier than the reply"):
        validate_references(reply(woken_by=[seq]), prior)


@pytest.mark.parametrize("seq", [1, 3, 9])
def test_wake_must_name_an_existing_catalog_trigger(seq: int) -> None:
    prior = index(plan(), reply(2), suite_execution())
    with pytest.raises(ValueError, match="catalog trigger"):
        validate_references(reply(woken_by=[seq]), prior)


def test_wake_cannot_name_a_foreign_leads_trigger() -> None:
    prior = index(plan(started_by="agent-3"), reply(2), status(3, state="halted"))
    with pytest.raises(ValueError, match="same lead"):
        validate_references(reply(woken_by=[3]), prior)


@pytest.mark.parametrize("earlier_lead", ["agent-1", "agent-3"])
def test_wake_cannot_consume_a_seq_from_any_earlier_reply(earlier_lead: str) -> None:
    prior = index(
        plan(), reply(2), status(3, state="halted"), reply(4, agent_id=earlier_lead, woken_by=[3])
    )
    with pytest.raises(ValueError, match="already named"):
        validate_references(reply(woken_by=[3]), prior)


def test_first_reply_for_each_lead_cannot_be_a_wake() -> None:
    prior = index(plan(), reply(2, agent_id="agent-3"), status(3, state="halted"))
    with pytest.raises(ValueError, match="first lead reply"):
        validate_references(reply(woken_by=[3]), prior)


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["live", "replay"])
async def test_committed_batch_validates_wakes_and_rejected_replies_do_not_consume(
    tmp_path: Path,
    mode: Literal["live", "replay"],
) -> None:
    async with EventStore(tmp_path / "wake.sqlite", "run-20261102-170000", mode=mode) as store:
        rows = await asyncio.gather(
            store.publish(plan().payload),
            store.publish(reply(2).payload),
            store.publish(status(3, state="halted").payload),
        )
        assert [row.seq for row in rows] == [1, 2, 3]
        with pytest.raises(EventRejected, match="catalog trigger"):
            await store.publish(reply(woken_by=[1, 3]).payload)
        assert (await store.publish(reply(woken_by=[3]).payload)).seq == 4
        with pytest.raises(EventRejected, match="already named"):
            await store.publish(reply(woken_by=[3]).payload)
        assert store.last_seq == 4


@pytest.mark.parametrize("log", LOGS, ids=lambda path: str(path.relative_to(ROOT)))
def test_all_recorded_logs_and_bundled_replay_keep_valid_references(log: Path) -> None:
    prior: dict[int, ServerEvent] = {}
    for row in read_jsonl(log):
        validate_references(row, prior)
        prior[row.seq] = row


def test_historical_later_null_replies_keep_validating() -> None:
    rows = read_jsonl(ROOT / "fixtures/events/handwritten-lead-thread.jsonl")
    later = [row for row in rows if row.type == "lead.reply" and row.seq in {9, 10, 11, 12}]
    assert len(later) == 4
    assert all(row.payload.answers_seq is None and row.payload.woken_by is None for row in later)
