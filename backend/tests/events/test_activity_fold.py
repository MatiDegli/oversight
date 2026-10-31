"""Observed intervals preserve exact time, attribution, unknowns and prefix integrity."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest

from oversight.events import ActivityInterval, fold_activity, read_jsonl
from oversight_contracts.events import (
    AgentState,
    AgentStatus,
    AgentStatusEvent,
    AgentStep,
    AgentStepEvent,
    ChangeReviewedEvent,
    LeadStatus,
    LeadStatusEvent,
    ServerEvent,
    UsageTick,
    UsageTickEvent,
)
from oversight_contracts.review import CrossReview

DEMO = Path(__file__).resolve().parents[3] / "fixtures/events/recorded-demo.jsonl"
BASE_MS = 1_793_275_200_000  # 2026-10-29T12:00:00.000Z


def ts(seconds: int) -> str:
    return f"2026-10-29T12:00:{seconds:02d}.000Z"


def status(
    seq: int,
    state: AgentState,
    seconds: int,
    agent: str = "agent-1",
    *,
    change: str = "change-1",
    model: str = "author-model",
) -> AgentStatusEvent:
    return AgentStatusEvent(
        seq=seq,
        ts=ts(seconds),
        type="agent.status",
        payload=AgentStatus(
            agent_id=agent,
            slot="A",
            change_id=change,
            task="Implement",
            model=model,
            state=state,
            step=0,
            max_steps=10,
            detail=None,
        ),
    )


def review(seq: int, seconds: int, change: str = "change-1") -> ChangeReviewedEvent:
    return ChangeReviewedEvent(
        seq=seq,
        ts=ts(seconds),
        type="change.reviewed",
        payload=CrossReview(
            change_id=change,
            revision=1,
            base_commit="base",
            head_commit="head",
            main_commit="main",
            candidate_tree="tree",
            author_model="author-model",
            reviewer_model="review-model",
            reviewer_provider="fake",
            reasoning=None,
            verdict="clean",
            findings=[],
            complete=True,
            problems=[],
        ),
    )


def tick(
    seq: int,
    seconds: int,
    role: Literal["agent", "review", "plan", "lead"] | None = None,
    *,
    agent: str = "agent-1",
    change: str | None = None,
) -> UsageTickEvent:
    return UsageTickEvent(
        seq=seq,
        ts=ts(seconds),
        type="usage.tick",
        payload=UsageTick(
            agent_id=agent,
            role=role,
            change_id=change,
            model="tick-model",
            input_tokens=None,
            output_tokens=None,
            cached_tokens=None,
            cost_usd=None,
            spent_usd=0,
            budget_usd=1,
            brake="ok",
        ),
    )


def step(seq: int, seconds: int, agent: str = "agent-1") -> AgentStepEvent:
    return AgentStepEvent(
        seq=seq,
        ts=ts(seconds),
        type="agent.step",
        payload=AgentStep(
            agent_id=agent,
            step=1,
            tool="read_file",
            summary="Read",
            result="ok",
            duration_ms=1,
            error=None,
            tests=None,
        ),
    )


def lead(seq: int, seconds: int) -> LeadStatusEvent:
    return LeadStatusEvent(
        seq=seq,
        ts=ts(seconds),
        type="lead.status",
        payload=LeadStatus(
            agent_id="agent-10",
            revision=seq,
            task="Coordinate",
            model="lead-model",
            state="working",
            step=0,
            detail=None,
        ),
    )


def test_recorded_demo_hours() -> None:
    fold = fold_activity(read_jsonl(DEMO))
    assert (fold.agent_ms, fold.active_ms, fold.through_seq) == (32982, 21457, 69)
    assert (fold.known_agent_ms, fold.known_active_ms) == (32982, 21457)
    assert fold.parallelism == 32982 / 21457
    assert fold.coverage == "complete" and fold.problems == () and fold.groups_add_up
    a, b = fold.actors
    assert (a.actor_id, a.role, a.model, a.ms, a.open) == (
        "agent-1",
        "agent",
        "muse-spark-1.3-contributor",
        21457,
        False,
    )
    assert (b.actor_id, b.role, b.model, b.ms, b.open) == (
        "agent-2",
        "agent",
        "meta-models/muse-spark-open-1026",
        11525,
        False,
    )
    assert [(i.start_seq, i.end_seq) for i in a.intervals] == [(3, 63)]
    assert [(i.start_seq, i.end_seq) for i in b.intervals] == [(5, 57)]
    assert [(g.role, g.model, g.actors, g.known_ms, g.ms) for g in fold.groups] == [
        ("agent", "meta-models/muse-spark-open-1026", 1, 11525, 11525),
        ("agent", "muse-spark-1.3-contributor", 1, 21457, 21457),
    ]


def test_revising_reopens_and_paused_and_halted_stop() -> None:
    states: list[AgentState] = [
        "starting",
        "working",
        "ready_for_review",
        "revising",
        "paused",
        "working",
        "halted",
    ]
    fold = fold_activity([status(n, state, n * 2) for n, state in enumerate(states, 1)])
    assert fold.actors[0].intervals == (
        ActivityInterval(1, 3, BASE_MS + 2000, BASE_MS + 6000),
        ActivityInterval(4, 5, BASE_MS + 8000, BASE_MS + 10000),
        ActivityInterval(6, 7, BASE_MS + 12000, BASE_MS + 14000),
    )
    assert fold.agent_ms == fold.active_ms == 8000
    assert not fold.actors[0].open


def test_counting_to_counting_keeps_one_interval() -> None:
    fold = fold_activity(
        [status(1, "starting", 0), status(2, "working", 2), status(3, "working", 4)]
    )
    assert fold.actors[0].intervals == (ActivityInterval(1, None, BASE_MS, BASE_MS + 4000),)
    assert fold.actors[0].open and fold.agent_ms == 4000


def test_review_runs_from_ready_for_review_to_change_reviewed() -> None:
    fold = fold_activity([status(1, "working", 0), status(2, "ready_for_review", 3), review(3, 8)])
    actor = fold.actors[1]
    assert (actor.actor_id, actor.role, actor.model, actor.change_id, actor.ms, actor.open) == (
        "review@3",
        "review",
        "review-model",
        "change-1",
        5000,
        False,
    )
    assert actor.intervals == (ActivityInterval(2, 3, BASE_MS + 3000, BASE_MS + 8000),)
    assert fold.agent_ms == fold.active_ms == 8000
    assert fold.parallelism == 1 and fold.groups_add_up


def test_second_review_of_a_change_starts_after_the_first() -> None:
    fold = fold_activity([status(1, "ready_for_review", 0), review(2, 3), review(3, 7)])
    assert fold.actors[2].intervals == (ActivityInterval(2, 3, BASE_MS + 3000, BASE_MS + 7000),)
    assert fold.agent_ms == fold.active_ms == 7000
    assert fold.groups[1].actors == 2 and fold.groups[1].ms == 7000


def test_new_ready_for_review_restarts_the_review_clock() -> None:
    fold = fold_activity(
        [
            status(1, "ready_for_review", 0),
            review(2, 3),
            status(3, "revising", 5),
            status(4, "ready_for_review", 8),
            review(5, 10),
        ]
    )
    assert fold.actors[2].intervals == (ActivityInterval(4, 5, BASE_MS + 8000, BASE_MS + 10000),)
    assert fold.agent_ms == fold.active_ms == 8000


def test_review_without_ready_for_review_is_missing_metrics() -> None:
    fold = fold_activity([review(1, 3)])
    assert fold.agent_ms is fold.active_ms is fold.parallelism is None
    assert fold.known_agent_ms == fold.known_active_ms == 0
    assert fold.actors[0].ms is None and fold.actors[0].intervals == ()
    assert fold.coverage == "missing_metrics" and not fold.groups_add_up
    assert fold.problems == ("review at seq 1 has no ready_for_review start",)
    assert fold.groups[0].known_ms == 0 and fold.groups[0].ms is None


def test_open_interval_is_clipped_at_the_prefix_end() -> None:
    fold = fold_activity([status(1, "working", 2), step(2, 9)])
    assert fold.actors[0].open
    assert fold.actors[0].intervals == (ActivityInterval(1, None, BASE_MS + 2000, BASE_MS + 9000),)
    assert fold.agent_ms == fold.active_ms == 7000
    assert fold.coverage == "complete" and fold.through_seq == 2


def test_overlapping_agents_add_up_and_union_once() -> None:
    fold = fold_activity(
        [
            status(1, "working", 0),
            status(2, "working", 2, "agent-2"),
            status(3, "done", 5),
            status(4, "stopped", 8, "agent-2"),
        ]
    )
    assert (fold.agent_ms, fold.active_ms, fold.parallelism) == (11000, 8000, 11000 / 8000)
    assert fold.groups[0].actors == 2 and fold.groups[0].ms == 11000
    assert fold.groups_add_up


def test_lead_and_plan_work_leave_totals_unavailable() -> None:
    fold = fold_activity([lead(1, 0), tick(2, 2, "plan"), lead(3, 4)])
    assert len(fold.actors) == 1
    actor = fold.actors[0]
    assert (actor.actor_id, actor.role, actor.model) == ("agent-10", "lead", "lead-model")
    assert actor.ms is None and actor.intervals == () and not actor.open
    assert fold.coverage == "missing_metrics" and not fold.groups_add_up
    assert fold.agent_ms is fold.active_ms is fold.parallelism is None
    assert fold.problems == ("lead hours are unavailable", "plan calls have no activity interval")


def test_work_without_an_interval_is_missing_metrics() -> None:
    fold = fold_activity([step(1, 0), tick(2, 2, "review", change="change-1")])
    assert fold.actors[0].model is None and fold.actors[0].ms is None
    assert fold.coverage == "missing_metrics"
    assert fold.problems == (
        "agent-1 worked with no activity interval",
        "review calls for change-1 have no change.reviewed",
    )


def test_ts_regression_is_incomplete() -> None:
    fold = fold_activity([status(1, "working", 5), status(2, "done", 2)])
    assert fold.coverage == "incomplete" and fold.problems == ("ts goes back at seq 2",)
    assert fold.agent_ms is fold.active_ms is fold.parallelism is None
    assert fold.known_agent_ms == fold.known_active_ms == 0
    assert fold.actors[0].ms is None and fold.groups[0].ms is None
    assert not fold.groups_add_up


@pytest.mark.parametrize("seqs", [(1, 3), (1, 1), (2,), (1, 2, 1)])
def test_seqs_are_validated(seqs: tuple[int, ...]) -> None:
    expected = next(n for n, seq in enumerate(seqs, 1) if seq != n)
    with pytest.raises(ValueError, match=f"expected seq {expected}, got {seqs[expected - 1]}"):
        fold_activity([status(seq, "working", 0) for seq in seqs])


def test_empty_prefix() -> None:
    fold = fold_activity([])
    assert fold.through_seq == 0 and fold.actors == fold.groups == fold.problems == ()
    assert fold.agent_ms == fold.active_ms == fold.known_agent_ms == fold.known_active_ms == 0
    assert fold.parallelism is None and fold.coverage == "complete" and fold.groups_add_up


def test_costs_are_never_read() -> None:
    events: list[ServerEvent] = [status(1, "working", 0), tick(2, 2), status(3, "done", 4)]
    other = tick(2, 2)
    other = other.model_copy(
        update={
            "payload": other.payload.model_copy(
                update={
                    "cost_usd": 123.0,
                    "spent_usd": 456.0,
                    "input_tokens": 999,
                    "output_tokens": 888,
                    "cached_tokens": 777,
                }
            )
        }
    )
    assert fold_activity(events) == fold_activity([events[0], other, events[2]])


def test_missing_problems_are_deduplicated_in_origin_seq_order() -> None:
    fold = fold_activity(
        [
            tick(1, 0, "review"),
            tick(2, 1),
            tick(3, 2, "plan"),
            tick(4, 3, "review"),
            tick(5, 4, "plan"),
        ]
    )
    assert fold.problems == (
        "review calls without a change have no change.reviewed",
        "agent-1 worked with no activity interval",
        "plan calls have no activity interval",
    )


def test_later_observations_supply_missing_intervals() -> None:
    fold = fold_activity(
        [
            tick(1, 0, "review", change="change-1"),
            step(2, 1),
            status(3, "working", 2),
            status(4, "ready_for_review", 4),
            review(5, 6),
        ]
    )
    assert fold.coverage == "complete" and fold.problems == ()
    assert fold.agent_ms == 4000 and fold.actors[0].model == "author-model"


def test_models_use_only_the_first_run_profiles_and_first_status() -> None:
    run = read_jsonl(DEMO)[0]
    assert run.type == "run.started"
    run = run.model_copy(update={"ts": ts(0)})
    second = run.model_copy(
        update={"seq": 2, "payload": run.payload.model_copy(update={"models": []})}
    )
    fold = fold_activity(
        [
            run,
            second,
            status(3, "working", 1, model="muse-spark"),
            status(4, "done", 3, model="other", change="change-2"),
        ]
    )
    assert fold.actors[0].model == "muse-spark-1.3-contributor"
    assert fold.actors[0].change_id == "change-2"


def test_lead_ticks_keep_exact_model_and_lead_status_maps_profiles() -> None:
    run = read_jsonl(DEMO)[0].model_copy(update={"ts": ts(0)})
    entry = lead(2, 1)
    entry = entry.model_copy(
        update={"payload": entry.payload.model_copy(update={"model": "muse-spark"})}
    )
    fold = fold_activity(
        [
            run,
            entry,
            tick(3, 2, "lead", agent="agent-20"),
            tick(4, 3, "lead", agent="agent-10"),
        ]
    )
    assert [(a.actor_id, a.model) for a in fold.actors] == [
        ("agent-10", "muse-spark-1.3-contributor"),
        ("agent-20", "tick-model"),
    ]
    assert fold.problems == ("lead hours are unavailable",)


def test_incomplete_retains_valid_intervals_but_suppresses_group_totals() -> None:
    fold = fold_activity(
        [
            status(1, "working", 1),
            status(2, "paused", 4),
            status(3, "working", 6),
            status(4, "done", 5),
            status(5, "working", 7, "agent-2"),
            status(6, "done", 9, "agent-2"),
        ]
    )
    assert fold.actors[0].ms is None and fold.actors[1].ms == 2000
    assert fold.known_agent_ms == 2000 and fold.known_active_ms == 5000
    assert fold.groups[0].known_ms == 2000 and fold.groups[0].ms is None


def test_known_groups_remain_available_when_another_group_is_unknown() -> None:
    fold = fold_activity(
        [
            status(1, "working", 0),
            status(2, "done", 3),
            step(3, 4, "agent-30"),
            review(4, 5),
            lead(5, 6),
        ]
    )
    assert [(g.role, g.model, g.ms) for g in fold.groups] == [
        ("agent", "author-model", 3000),
        ("agent", None, None),
        ("review", "review-model", None),
        ("lead", "lead-model", None),
    ]
    assert fold.known_agent_ms == fold.known_active_ms == 3000 and not fold.groups_add_up


def test_timestamps_preserve_milliseconds_far_from_epoch() -> None:
    start = status(1, "working", 0).model_copy(update={"ts": "9999-01-01T00:00:00.001Z"})
    end = status(2, "done", 0).model_copy(update={"ts": "9999-01-01T00:00:00.004Z"})
    fold = fold_activity([start, end])
    expected = (datetime(9999, 1, 1, tzinfo=UTC) - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(
        milliseconds=1,
    )
    assert fold.actors[0].intervals == (ActivityInterval(1, 2, expected + 1, expected + 4),)
    assert fold.agent_ms == fold.active_ms == 3


def test_previous_review_can_start_an_interval_without_ready_status() -> None:
    fold = fold_activity([review(1, 0), review(2, 3)])
    assert fold.actors[0].ms is None and fold.actors[1].ms == 3000
    assert fold.actors[1].intervals == (ActivityInterval(1, 2, BASE_MS, BASE_MS + 3000),)
    assert fold.known_agent_ms == fold.known_active_ms == 3000
    assert fold.coverage == "missing_metrics"
    assert fold.problems == ("review at seq 1 has no ready_for_review start",)


@pytest.mark.parametrize("interrupted,start", [(False, 1), (True, 3)])
def test_repeated_ready_keeps_first_start_until_a_non_ready_status(
    interrupted: bool, start: int
) -> None:
    states: list[AgentState] = ["ready_for_review", "ready_for_review"]
    if interrupted:
        states = ["ready_for_review", "paused", "ready_for_review"]
    events: list[ServerEvent] = [status(n, state, n) for n, state in enumerate(states, 1)]
    end = len(events) + 1
    fold = fold_activity([*events, review(end, end)])
    assert fold.actors[1].intervals == (
        ActivityInterval(start, end, BASE_MS + start * 1000, BASE_MS + end * 1000),
    )
    assert fold.actors[1].ms == (end - start) * 1000


def test_unknown_profile_is_preserved_with_a_run_profile_map() -> None:
    run = read_jsonl(DEMO)[0].model_copy(update={"ts": ts(0)})
    fold = fold_activity([run, status(2, "paused", 1, model="unknown-profile")])
    assert fold.actors[0].model == "unknown-profile"


@pytest.mark.parametrize("working", [False, True])
def test_first_and_repeated_non_counting_statuses_add_no_interval(working: bool) -> None:
    states: list[AgentState] = ["paused", "paused"]
    if working:
        states.insert(0, "working")
    fold = fold_activity([status(n, state, n) for n, state in enumerate(states, 1)])
    expected = (ActivityInterval(1, 2, BASE_MS + 1000, BASE_MS + 2000),) if working else ()
    assert fold.actors[0].intervals == expected and not fold.actors[0].open
    assert fold.agent_ms == fold.active_ms == (1000 if working else 0)
    assert fold.coverage == "complete" and fold.problems == ()
