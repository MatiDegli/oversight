"""Handwritten supervision receipts preserve outcomes, attribution and unknown spend."""

import dataclasses
from pathlib import Path

import pytest
from changes.test_policy_profiles import PROFILE, policy_inputs
from events.test_usage_fold import TS, proposal, resolution, tick

from oversight.changes._policy import assess
from oversight.events import Decisions, Outcomes, Spend, fold_supervision, fold_usage, read_jsonl
from oversight_contracts.events import (
    AgentFocus,
    AgentFocusEvent,
    AgentStep,
    AgentStepEvent,
    ChangeResolved,
    ChangeUpdatedEvent,
    ServerEvent,
)

DEMO = Path(__file__).resolve().parents[3] / "fixtures/events/recorded-demo.jsonl"


def numbered(*events: ServerEvent) -> list[ServerEvent]:
    return [event.model_copy(update={"seq": seq}) for seq, event in enumerate(events, 1)]


def proposed(revision: int = 1, added: int = 10, removed: int = 2) -> ServerEvent:
    event = proposal(1, revision)
    return event.model_copy(
        update={
            "payload": event.payload.model_copy(
                update={"lines_added": added, "lines_removed": removed}
            )
        }
    )


def step(agent: str, duration: int = 7) -> AgentStepEvent:
    return AgentStepEvent(
        seq=1,
        ts=TS,
        type="agent.step",
        payload=AgentStep(
            agent_id=agent,
            step=1,
            tool="read_file",
            summary="Read app.py",
            result="ok",
            error=None,
            duration_ms=duration,
            tests=None,
        ),
    )


def updated(change: str = "change-1") -> ChangeUpdatedEvent:
    return ChangeUpdatedEvent.model_validate(
        {
            "seq": 1,
            "ts": TS,
            "type": "change.updated",
            "payload": {
                "change_id": change,
                "agent_id": "agent-1",
                "author": "user",
                "file": {
                    "path": "app.py",
                    "status": "modified",
                    "original": "a",
                    "modified": "b",
                    "lines_added": 1,
                    "lines_removed": 1,
                },
                "node_ids": [],
                "graph": proposal(1).payload.graph.model_dump(),
                "files_changed": 1,
                "lines_added": 1,
                "lines_removed": 1,
            },
        }
    )


def test_second_round_merge_counts_first_round_as_not_merged() -> None:
    result = fold_supervision(
        numbered(
            tick(1, 0.1),
            proposed(),
            tick(1, 0.2, role="review"),
            resolution(1),
            tick(1, 0.4),
            proposed(2, 15, 4),
            resolution(1, decision="approved", merge="merged", revision=2),
        )
    )
    change = result.changes[0]
    assert (change.proposals, change.review_rounds, change.last_revision) == (2, 1, 2)
    assert (change.lines_added, change.lines_removed, change.net_lines_merged) == (15, 4, 11)
    assert change.end == "merged"
    assert change.usage.cost_usd == pytest.approx(0.7)
    assert result.run.outcomes == Outcomes(merged=1, changes_requested=1)
    assert result.run.changes == 1 and result.run.proposals == 2
    assert result.agents[0].review_rounds == result.run.review_rounds == 1
    assert result.run.net_lines_merged == 11
    assert result.not_merged.earlier_rounds == Spend(1, 0.1 + 0.2, 0)


def test_stopped_rejected_and_conflict_end_states() -> None:
    result = fold_supervision(
        numbered(
            tick(1, 1, change="change-1"),
            resolution(1, "change-1", "rejected", revision=0, stopped=True),
            tick(1, 2, change="change-2"),
            resolution(1, "change-2", "rejected"),
            tick(1, 3, change="change-3"),
            resolution(1, "change-3", "approved", merge="conflict"),
        )
    )
    assert [c.end for c in result.changes] == ["stopped", "rejected", "conflict"]
    assert result.not_merged.stopped == Spend(1, 1, 0)
    assert result.not_merged.rejected == Spend(1, 2, 0)
    assert result.not_merged.conflict == Spend(1, 3, 0)
    assert result.run.open_changes == result.agents[0].open_changes == 0
    assert result.run.outcomes == Outcomes(stopped=1, rejected=1, conflict=1)
    assert result.changes[0].last_revision is None
    assert result.changes[0].lines_added is result.changes[0].lines_removed is None


def test_rules_and_person_decisions_are_counted_apart() -> None:
    # Same validated rules receipt construction as test_revert_preflight's repo fixture.
    event = resolution(1, decision="approved", merge="merged")
    rules = ChangeResolved.model_validate(
        {
            **event.payload.model_dump(),
            "decided_by": "rules",
            "assessment": assess(policy_inputs(), PROFILE),
            "reasons": ["All rules passed."],
        }
    )
    result = fold_supervision(
        numbered(
            event.model_copy(update={"payload": rules}),
            resolution(1, "change-2", "rejected"),
        )
    )
    assert result.decisions == Decisions(person=1, rules=1)
    assert result.changes[0].net_lines_merged is None
    assert result.agents[0].net_lines_merged is result.run.net_lines_merged is None


def test_change_open_at_end_of_prefix() -> None:
    result = fold_supervision(numbered(tick(1), proposed()))
    assert result.changes[0].end == "open"
    assert result.run.open_changes == result.agents[0].open_changes == 1
    assert all(
        getattr(result.not_merged, f.name) == Spend(0, 0, 0)
        for f in dataclasses.fields(result.not_merged)
    )


@pytest.mark.parametrize("extra_proposal", [False, True])
def test_revision_request_or_new_proposal_keeps_change_open(extra_proposal: bool) -> None:
    events = numbered(proposed(), resolution(1))
    if extra_proposal:
        events = numbered(*events, proposed(2))
    result = fold_supervision(events)
    assert result.changes[0].end == "open"
    assert result.run.open_changes == 1
    assert result.not_merged.earlier_rounds == Spend(0, 0, 0)


@pytest.mark.parametrize("role_unknown", [False, True])
def test_unknown_cost_nulls_agent_cost_and_its_spend_figure(role_unknown: bool) -> None:
    result = fold_supervision(
        numbered(
            tick(1, 1 if role_unknown else None, role=None if role_unknown else "agent"),
            resolution(1, decision="rejected"),
            tick(1, 2, change=None, agent="agent-2"),
        )
    )
    assert result.agents[0].cost_usd is result.run.cost_usd is None
    assert result.agents[0].unknown_calls == (0 if role_unknown else 1)
    assert result.not_merged.rejected == Spend(1, None, 0 if role_unknown else 1)
    assert result.agents[1].cost_usd == 2 and result.agents[1].unknown_calls == 0


def test_recorded_demo_totals_match_hand_count() -> None:
    events = read_jsonl(DEMO)
    result = fold_supervision(events)
    assert len(events) == result.through_seq == 69
    # First appearance includes status/update references, before either proposal.
    assert [c.change_id for c in result.changes] == list(fold_usage(events).by_change)
    by_id = {c.change_id: c for c in result.changes}
    for cid, agent, added in (("change-1", "agent-1", 28), ("change-2", "agent-2", 9)):
        c = by_id[cid]
        assert (c.agent_id, c.last_revision, c.proposals, c.review_rounds) == (agent, 1, 1, 0)
        assert (c.lines_added, c.lines_removed, c.net_lines_merged, c.end) == (
            added,
            0,
            added,
            "merged",
        )
        assert c.usage.cost_usd == 0 and c.usage.unknown_calls == 0
    assert [(e.seq, e.payload.change_id) for e in events if e.type == "change.proposed"] == [
        (56, "change-2"),
        (62, "change-1"),
    ]
    assert [(e.seq, e.payload.change_id) for e in events if e.type == "change.resolved"] == [
        (64, "change-1"),
        (67, "change-2"),
    ]
    a, b = result.agents
    assert (a.agent_id, a.steps, a.step_ms, a.unknown_calls) == ("agent-1", 8, 259, 0)
    assert a.cost_usd == pytest.approx(0.0020239, abs=1e-12)
    assert f"{a.cost_usd:.6f}" == "0.002024"
    assert (b.agent_id, b.steps, b.step_ms, b.unknown_calls, b.cost_usd) == (
        "agent-2",
        11,
        514,
        10,
        None,
    )
    for agent, added in ((a, 28), (b, 9)):
        assert (agent.changes, agent.proposals, agent.review_rounds, agent.open_changes) == (
            1,
            1,
            0,
            0,
        )
        assert (agent.lines_added, agent.lines_removed, agent.net_lines_merged) == (added, 0, added)
        assert agent.outcomes == Outcomes(merged=1)
    run = result.run
    assert (run.changes, run.proposals, run.review_rounds, run.open_changes) == (2, 2, 0, 0)
    assert (run.lines_added, run.lines_removed, run.net_lines_merged) == (37, 0, 37)
    assert (run.steps, run.step_ms, run.cost_usd, run.unknown_calls) == (19, 773, None, 10)
    assert run.outcomes == Outcomes(merged=2) and result.decisions == Decisions(unrecorded=2)
    usage = fold_usage(events)
    assert len(usage.unassigned) == 17
    assert sum(u.agent_id == "agent-1" for u in usage.unassigned) == 7
    assert sum(u.agent_id == "agent-2" for u in usage.unassigned) == 10
    assert all(e.payload.change_id is None for e in events if e.type == "usage.tick")
    assert all(
        getattr(result.not_merged, f.name) == Spend(0, 0, 0)
        for f in dataclasses.fields(result.not_merged)
    )


def test_new_event_types_are_ignored() -> None:
    base = [proposed(), resolution(1, decision="approved", merge="merged"), step("agent-1")]
    focus = AgentFocusEvent(
        seq=1,
        ts=TS,
        type="agent.focus",
        payload=AgentFocus(
            agent_id="agent-9",
            mode="read",
            ranges=[],
            node_ids=[],
        ),
    )
    expected = fold_supervision(numbered(*base))
    actual = fold_supervision(
        numbered(base[0], focus, updated(), tick(1, 0, change=None), *base[1:])
    )
    assert dataclasses.replace(actual, through_seq=3) == expected
    assert actual.through_seq == 6


def test_refuses_incomplete_prefix() -> None:
    with pytest.raises(ValueError, match="expected seq 2, got 3"):
        fold_supervision([proposal(1), tick(3)])


@pytest.mark.parametrize("first", [proposal(1), tick(1), resolution(1)])
def test_two_owners_for_one_change_raise(first: ServerEvent) -> None:
    with pytest.raises(ValueError, match="two owners for change change-1 at seq 2"):
        fold_supervision(numbered(first, tick(1, agent="agent-2")))


def test_unowned_changes_are_omitted_but_keep_first_appearance_order() -> None:
    result = fold_supervision(
        numbered(
            updated("change-2"),
            updated("change-3"),
            proposal(1),
            tick(1, change="change-2"),
            step("agent-3"),
        )
    )
    assert [c.change_id for c in result.changes] == ["change-2", "change-1"]
    assert [a.agent_id for a in result.agents] == ["agent-1", "agent-3"]
    assert result.changes[0].proposals == 0 and result.changes[0].last_revision is None
    assert result.run.changes == 1 and result.run.open_changes == 2
    assert result.run.steps == 1 and result.run.step_ms == 7


def test_empty_prefix_and_unknown_unassigned_calls() -> None:
    empty = fold_supervision([])
    assert empty.through_seq == 0 and empty.agents == empty.changes == ()
    assert empty.run.cost_usd == 0 and empty.run.net_lines_merged == 0
    result = fold_supervision([tick(1, None, change=None)])
    assert result.changes == () and result.run.cost_usd is None
    assert result.agents[0].unknown_calls == result.run.unknown_calls == 1
    assert result.not_merged.rejected == Spend(0, 0, 0)


def test_missing_merged_revision_nulls_net_even_with_another_proposal() -> None:
    result = fold_supervision(
        numbered(proposed(), resolution(1, revision=2, decision="approved", merge="merged"))
    )
    assert result.changes[0].net_lines_merged is result.run.net_lines_merged is None


def test_unknown_earlier_bucket_and_empty_earlier_round() -> None:
    result = fold_supervision(
        numbered(
            tick(1, 2, model=None),
            proposed(),
            resolution(1),
            tick(1, 3),
            proposed(2),
            resolution(1, revision=2, decision="approved", merge="merged"),
            proposal(1, change="change-2"),
            resolution(1, "change-2"),
            tick(1, 4, change="change-2"),
            proposal(1, 2, "change-2"),
            resolution(1, "change-2", revision=2, decision="approved", merge="merged"),
        )
    )
    assert result.not_merged.earlier_rounds == Spend(1, None, 0)
    assert result.run.cost_usd is None and result.run.unknown_calls == 0


def test_new_proposal_reopens_terminal_change_without_erasing_outcomes() -> None:
    result = fold_supervision(
        numbered(
            proposed(),
            resolution(1, decision="approved", merge="merged"),
            proposed(2, 20, 5),
        )
    )
    assert result.run.outcomes == Outcomes(merged=1)
    assert result.run.open_changes == 1 and result.changes[0].end == "open"
    assert (result.run.lines_added, result.run.lines_removed, result.run.net_lines_merged) == (
        20,
        5,
        8,
    )
