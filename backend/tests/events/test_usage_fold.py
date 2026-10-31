"""Event-derived usage preserves paid attempts, unknowns and committed round order."""

import math
from pathlib import Path
from typing import Literal

import pytest

from oversight.events import EventStore, fold_usage, play
from oversight_contracts.events import (
    AgentMessageEvent,
    AgentMessageSent,
    AgentStatus,
    AgentStatusEvent,
    ChangeProposed,
    ChangeProposedEvent,
    ChangeResolved,
    ChangeResolvedEvent,
    Conflict,
    ErrorReport,
    ErrorReportEvent,
    GraphSnapshotEvent,
    ServerEvent,
    UsageTick,
    UsageTickEvent,
)
from oversight_contracts.graph import GraphSnapshot

type Role = Literal["agent", "review", "plan", "lead"]
TS = "2026-10-29T12:00:00.000Z"


def tick(
    seq: int,
    cost: float | None = 1.0,
    *,
    change: str | None = "change-1",
    role: Role | None = "agent",
    model: str | None = "reported-model",
    agent: str = "agent-1",
    plan: str | None = None,
) -> UsageTickEvent:
    return UsageTickEvent(
        seq=seq,
        ts=TS,
        type="usage.tick",
        payload=UsageTick(
            agent_id=agent,
            change_id=change,
            role=role,
            model=model,
            plan_id=plan,
            input_tokens=None,
            output_tokens=None,
            cached_tokens=None,
            cost_usd=cost,
            spent_usd=99999.0,
            budget_usd=100.0,
            brake="blocked",
        ),
    )


@pytest.mark.parametrize("seqs", [(2,), (1, 3), (2, 1), (1, 1), (1, 2, 2)])
def test_refuses_incomplete_or_unordered_sequence_inventory(seqs: tuple[int, ...]) -> None:
    with pytest.raises(ValueError, match="seq"):
        fold_usage([tick(seq) for seq in seqs])


@pytest.mark.parametrize("cost", [-0.01, math.nan, math.inf, -math.inf])
def test_refuses_negative_or_non_finite_individual_costs(cost: float) -> None:
    # UsageTick itself allows these floats; the fold must refuse a plausible total.
    with pytest.raises(ValueError, match="cost"):
        fold_usage([tick(1, cost)])


def resolution(
    seq: int,
    change: str = "change-1",
    decision: Literal["approved", "changes_requested", "rejected"] = "changes_requested",
    *,
    revision: int = 1,
    merge: Literal["merged", "conflict"] | None = None,
    stopped: bool = False,
) -> ChangeResolvedEvent:
    return ChangeResolvedEvent(
        seq=seq,
        ts=TS,
        type="change.resolved",
        payload=ChangeResolved(
            change_id=change,
            agent_id="agent-1",
            revision=revision,
            decision=decision,
            decided_by="person",
            reason="stopped" if stopped else None,
            comment=None,
            merge=merge,
            merge_commit="merged-head" if merge == "merged" else None,
            conflict=Conflict(kind="merge", paths=["app.py"], tests=None)
            if merge == "conflict"
            else None,
        ),
    )


def proposal(seq: int, revision: int = 1, change: str = "change-1") -> ChangeProposedEvent:
    return ChangeProposedEvent(
        seq=seq,
        ts=TS,
        type="change.proposed",
        payload=ChangeProposed.model_validate(
            {
                "change_id": change,
                "agent_id": "agent-1",
                "revision": revision,
                "task": "Synthetic task",
                "summary": None,
                "base_commit": "base",
                "head_commit": "head",
                "files": [],
                "lines_added": 0,
                "lines_removed": 0,
                "graph": {
                    "nodes_added": [],
                    "nodes_modified": [],
                    "nodes_removed": [],
                    "edges_added": [],
                    "edges_removed": [],
                },
                "tests": {
                    "outcome": "failed",
                    "passed": 0,
                    "failed": 1,
                    "skipped": 0,
                    "duration_ms": 1,
                    "failed_tests": ["test_app.py::test_app"],
                },
                "impact": {
                    "seeds": [],
                    "entries": [],
                    "edges_followed": "exact",
                    "max_depth": 3,
                    "max_nodes": 100,
                    "omitted_by_depth": 0,
                    "omitted_by_limit": 0,
                    "tests_reaching": 0,
                },
            }
        ),
    )


def test_costs_group_by_explicit_change_role_exact_model_and_include_real_zero() -> None:
    events = [
        tick(1, 0.1),
        tick(2, 0.2),
        tick(3, 0.0, role="review"),
        tick(4, 2, role="review", model="Reported-Model"),
        tick(5, 3, role="plan"),
        tick(6, 4, role="lead"),
        tick(7, 5, change="change-2"),
        resolution(8, decision="approved", merge="merged"),
    ]
    result = fold_usage(events)
    assert result.through_seq == 8
    usage = result.by_change["change-1"].usage
    assert usage.cost_usd == math.fsum([0.1, 0.2, 0, 2, 3, 4])
    assert usage.unknown_calls == 0
    assert usage.by_role is not None
    assert [(b.role, b.model, b.round, b.cost_usd) for b in usage.by_role] == [
        ("agent", "reported-model", 0, math.fsum([0.1, 0.2])),
        ("review", "reported-model", 0, 0),
        ("review", "Reported-Model", 0, 2),
        ("plan", "reported-model", 0, 3),
        ("lead", "reported-model", 0, 4),
    ]
    assert result.by_change["change-2"].usage.cost_usd == 5
    assert result.run.cost_usd == math.fsum([0.1, 0.2, 0, 2, 3, 4, 5])
    assert result.run.known_cost_usd == result.run.cost_usd
    assert result.run.calls == 7
    assert result.run.accepted_changes == 1
    assert result.run.cost_per_accepted_change_usd == result.run.cost_usd
    # spent_usd is intentionally unrelated; token counts are unknown despite known prices.
    assert result.run.cost_usd != events[0].payload.spent_usd


def test_unknown_cost_preserves_known_subtotal_and_unpriced_model_bucket() -> None:
    result = fold_usage(
        [
            tick(1, 2),
            tick(2, None),
            tick(3, None, change="change-2", model="meta-models/muse-spark-open-1026"),
            resolution(4, decision="approved", merge="merged"),
        ]
    )
    for change in result.by_change.values():
        assert change.usage.cost_usd is None
        assert change.usage.unknown_calls == 1
        assert change.usage.by_role is not None
        assert change.usage.by_role[0].cost_usd is None
    assert result.run.known_cost_usd == 2
    assert result.run.unknown_calls == 2
    assert result.run.cost_usd is None
    assert result.run.cost_per_accepted_change_usd is None


@pytest.mark.parametrize("role,model", [(None, "reported"), ("agent", None), (None, None)])
def test_missing_bucket_attribution_is_unavailable_even_with_a_known_cost(
    role: Role | None,
    model: str | None,
) -> None:
    result = fold_usage([tick(1, 4, role=role, model=model)])
    usage = result.by_change["change-1"].usage
    assert usage.cost_usd is None
    assert usage.unknown_calls == 0  # Missing attribution is not an unknown dollar cost.
    assert usage.by_role is not None
    assert [(b.role, b.model, b.round, b.cost_usd, b.unknown_calls) for b in usage.by_role] == [
        (role, model, 0, None, 0),
    ]
    assert result.run.cost_usd == 4  # No allocation is required to sum the entire run.


def test_unassigned_calls_keep_plan_agent_role_model_and_unknown_cost_without_guessing() -> None:
    result = fold_usage(
        [
            tick(1, 2),
            tick(2, 3, change=None, role="plan", plan="plan-1"),
            tick(3, None, change=None, role=None, model=None),
        ]
    )
    assert result.by_change["change-1"].usage.cost_usd == 2
    assert len(result.by_change) == 1
    assert [
        (u.seq, u.agent_id, u.role, u.model, u.plan_id, u.cost_usd) for u in result.unassigned
    ] == [
        (2, "agent-1", "plan", "reported-model", "plan-1", 3),
        (3, "agent-1", None, None, None, None),
    ]
    assert result.run.calls == 3
    assert result.run.known_cost_usd == 5
    assert result.run.unknown_calls == 1
    assert result.run.cost_usd is None


def test_rounds_follow_committed_resolutions_not_proposals_or_other_changes() -> None:
    events: list[ServerEvent] = [
        tick(1),
        proposal(2, revision=40),
        proposal(3, revision=40),
        tick(4, role="review"),
        resolution(5, revision=40),
        tick(6),
        resolution(7, change="change-2"),
        tick(8, change="change-2"),
        proposal(9, revision=41),
        tick(10, role="review"),
        resolution(11, revision=41),
        tick(12),
    ]
    result = fold_usage(events)
    first = result.by_change["change-1"]
    assert first.revision_rounds == 2
    assert first.usage.by_role is not None
    assert [(b.role, b.round, b.cost_usd) for b in first.usage.by_role] == [
        ("agent", 0, 1),
        ("review", 0, 1),
        ("agent", 1, 1),
        ("review", 1, 1),
        ("agent", 2, 1),
    ]
    second = result.by_change["change-2"]
    assert second.revision_rounds == 1
    assert second.usage.by_role is not None
    assert second.usage.by_role[0].round == 1
    for end, rounds in [(4, 0), (5, 1), (10, 1), (11, 2)]:
        prefix = fold_usage(events[:end])
        assert prefix.through_seq == end
        assert prefix.by_change["change-1"].revision_rounds == rounds
    assert fold_usage(events[:11]).by_change["change-1"].usage.cost_usd == 4


def test_unsuccessful_pending_and_unassigned_work_all_count_toward_accepted_cost() -> None:
    result = fold_usage(
        [
            tick(1, 1),
            resolution(2, decision="rejected"),
            tick(3, 2, change="change-2"),
            resolution(4, "change-2", "rejected", revision=0, stopped=True),
            tick(5, 3, change="change-3"),
            proposal(6, change="change-3"),
            tick(7, 4, change=None, role="lead"),
            resolution(8, "change-4", "approved", merge="conflict"),
            tick(9, 5, change="change-5"),
            resolution(10, "change-5", "approved", merge="merged"),
            resolution(11, "change-5", "approved", merge="merged"),
        ]
    )
    assert set(result.by_change) == {f"change-{n}" for n in range(1, 6)}
    assert result.by_change["change-4"].usage.cost_usd == 0
    assert result.run.accepted_changes == 1  # Distinct changes, not resolution count.
    assert result.run.cost_usd == 15
    assert result.run.cost_per_accepted_change_usd == 15


def test_zero_acceptance_conflicts_and_zero_cost_are_not_unknown_or_free_attempts() -> None:
    events = [tick(1, 0), resolution(2, decision="approved", merge="conflict")]
    result = fold_usage(events)
    assert result.run.cost_usd == 0
    assert result.run.accepted_changes == 0
    assert result.run.cost_per_accepted_change_usd is None
    result = fold_usage([*events, resolution(3, decision="approved", merge="merged")])
    assert result.run.cost_per_accepted_change_usd == 0
    result = fold_usage(
        [
            *events,
            resolution(3, decision="approved", merge="merged"),
            resolution(4, "change-2", "approved", merge="merged"),
            tick(5, 5, change=None),
        ]
    )
    assert result.run.cost_per_accepted_change_usd == 2.5


def test_empty_prefix_has_complete_empty_inventory_and_unavailable_ratio() -> None:
    result = fold_usage([])
    assert result.through_seq == 0
    assert result.by_change == {}
    assert result.unassigned == ()
    assert result.run.calls == result.run.unknown_calls == result.run.accepted_changes == 0
    assert result.run.cost_usd == result.run.known_cost_usd == 0
    assert result.run.cost_per_accepted_change_usd is None


def test_failed_and_pending_changes_can_be_discovered_before_ticks_or_proposals() -> None:
    failure = ErrorReportEvent(
        seq=1,
        ts=TS,
        type="error",
        payload=ErrorReport(
            source="model_api",
            message="Failed attempt",
            agent_id="agent-1",
            change_id="change-1",
            fatal=True,
        ),
    )
    pending = AgentStatusEvent(
        seq=2,
        ts=TS,
        type="agent.status",
        payload=AgentStatus(
            agent_id="agent-2",
            slot="B",
            change_id="change-2",
            task="Pending task",
            model="configured-model",
            state="starting",
            step=0,
            max_steps=10,
            detail=None,
        ),
    )
    result = fold_usage([failure, pending])
    assert set(result.by_change) == {"change-1", "change-2"}
    assert all(c.revision_rounds == 0 for c in result.by_change.values())
    # This is the committed receipt inventory only, not proof an unrecorded call was free.
    assert all(c.usage.by_role == [] for c in result.by_change.values())


def test_reviewer_and_decision_remain_combined_despite_agent_model_and_timing() -> None:
    result = fold_usage(
        [
            tick(1, 2, role="review", agent="agent-1"),
            tick(2, 3, role="review", agent="agent-2"),
            tick(3, 5, role="review", agent="agent-1", model="decision-named-model"),
        ]
    )
    buckets = result.by_change["change-1"].usage.by_role
    assert buckets is not None
    assert [(b.role, b.model, b.cost_usd) for b in buckets] == [
        ("review", "reported-model", 5),
        ("review", "decision-named-model", 5),
    ]


def test_identical_prefix_is_deterministic_independent_and_does_not_modify_inputs() -> None:
    events: list[ServerEvent] = [tick(1), proposal(2), resolution(3), tick(4)]
    before = [event.model_dump_json() for event in events]
    first, second = fold_usage(events), fold_usage(events)
    assert first == second
    buckets = first.by_change["change-1"].usage.by_role
    assert buckets is not None
    buckets.clear()
    first.by_change.clear()
    assert second == fold_usage(events)
    assert before == [event.model_dump_json() for event in events]


@pytest.mark.parametrize("seq", [0, -1, 1.5, True])
def test_refuses_unvalidated_invalid_sequence_values(seq: object) -> None:
    with pytest.raises(ValueError, match="seq"):
        fold_usage([tick(1).model_copy(update={"seq": seq})])


def test_refuses_overflow_in_known_cost_subtotal_even_with_unknown_calls() -> None:
    with pytest.raises(ValueError, match="cost"):
        fold_usage([tick(1, 1e308), tick(2, 1e308), tick(3, None)])


def test_stable_sum_preserves_small_costs_next_to_large_costs() -> None:
    result = fold_usage([tick(1, 1e16), tick(2, 1), tick(3, 1)])
    assert result.run.cost_usd == result.by_change["change-1"].usage.cost_usd == 1e16 + 2


@pytest.mark.parametrize("text", [None, "Please reconsider."])
def test_message_events_advance_sequence_without_changing_accounting(text: str | None) -> None:
    message = AgentMessageEvent(
        seq=2,
        ts=TS,
        type="agent.message",
        payload=AgentMessageSent(agent_id="agent-1", text=text),
    )
    result = fold_usage([tick(1, 2), message, tick(3, 3), resolution(4)])
    baseline = fold_usage([tick(1, 2), tick(2, 3), resolution(3)])
    assert result.through_seq == 4
    assert result.by_change == baseline.by_change
    assert result.run == baseline.run
    assert result.unassigned == baseline.unassigned


def test_unrelated_events_advance_through_seq_without_creating_calls_or_changes() -> None:
    snapshot = GraphSnapshotEvent(
        seq=2,
        ts=TS,
        type="graph.snapshot",
        payload=GraphSnapshot(
            version="v0", commit="main", nodes=[], edges=[], parse_errors=[], positions={}
        ),
    )
    result = fold_usage([tick(1, 2), snapshot])
    assert result.through_seq == 2
    assert result.run.calls == 1
    assert result.run.cost_usd == 2
    assert set(result.by_change) == {"change-1"}


@pytest.mark.anyio
async def test_live_and_speed_zero_replay_fold_identical_committed_prefixes(tmp_path: Path) -> None:
    synthetic: list[ServerEvent] = [
        tick(1, 1),
        proposal(2),
        resolution(3),
        tick(4, None, role="review"),
        tick(5, 2, change=None, role="plan", plan="plan-1"),
        resolution(6, decision="approved", merge="merged"),
    ]
    async with EventStore(tmp_path / "live.sqlite", "run-20261029-120000") as live:
        for event in synthetic:
            await live.publish(event.payload, ts=event.ts)
        committed = live.committed_events()
        result = fold_usage(committed)
    async with EventStore(
        tmp_path / "replay.sqlite", "run-20261029-120000", mode="replay"
    ) as replay:
        await play(replay, committed, speed=0)
        replayed = replay.committed_events()
        assert replayed == committed
        for end in range(len(committed) + 1):
            assert fold_usage(replayed[:end]) == fold_usage(committed[:end])
        assert fold_usage(replayed) == result
