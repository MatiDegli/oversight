"""Local periods preserve observed bounds, receipt placement and global coverage."""

from datetime import UTC, datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

from oversight.events.activity_fold import fold_activity
from oversight.events.list_prices import LIST_PRICES, ListPrice, PriceTable, price_call, price_usage
from oversight.events.usage_periods import fold_periods
from oversight_contracts.events import (
    AgentState,
    AgentStatus,
    AgentStatusEvent,
    ChangeReviewedEvent,
    ServerEvent,
    UsageTick,
    UsageTickEvent,
)
from oversight_contracts.review import CrossReview

MODEL = "muse-spark-1.3-contributor"
ART = timezone(timedelta(hours=-3))
HOUR = 3_600_000


def status(seq: int, ts: str, state: AgentState, agent: str = "agent-1") -> AgentStatusEvent:
    return AgentStatusEvent(
        seq=seq,
        ts=ts,
        type="agent.status",
        payload=AgentStatus(
            agent_id=agent,
            slot="A",
            change_id="change-1",
            task="Implement",
            model=MODEL,
            state=state,
            step=0,
            max_steps=10,
            detail=None,
        ),
    )


def tick(
    seq: int,
    ts: str,
    *,
    model: str = MODEL,
    i: int | None = 1_000_000,
    c: int | None = 400_000,
    o: int | None = 1_000_000,
    role: Literal["agent", "review", "plan", "lead"] | None = None,
) -> UsageTickEvent:
    return UsageTickEvent(
        seq=seq,
        ts=ts,
        type="usage.tick",
        payload=UsageTick(
            agent_id="agent-1",
            model=model,
            input_tokens=i,
            cached_tokens=c,
            output_tokens=o,
            cost_usd=None,
            spent_usd=999,
            budget_usd=1000,
            brake="ok",
            role=role,
        ),
    )


def review(seq: int, ts: str) -> ChangeReviewedEvent:
    return ChangeReviewedEvent(
        seq=seq,
        ts=ts,
        type="change.reviewed",
        payload=CrossReview(
            change_id="change-1",
            revision=1,
            base_commit="base",
            head_commit="head",
            main_commit="main",
            candidate_tree="tree",
            author_model=MODEL,
            reviewer_model="review-model",
            reviewer_provider="fake",
            reasoning=None,
            verdict="clean",
            findings=[],
            complete=True,
            problems=[],
        ),
    )


@pytest.fixture
def crossing() -> list[ServerEvent]:
    return [
        status(1, "2026-11-01T01:30:00.000Z", "working"),
        tick(2, "2026-11-01T02:00:00.000Z"),
        tick(3, "2026-11-01T03:00:00.000Z"),
        status(4, "2026-11-01T03:30:00.000Z", "done"),
        tick(5, "2026-11-01T06:15:00.000Z"),
    ]


def test_hour_clips_midnight_places_ticks_and_lists_empty_middle(
    crossing: list[ServerEvent],
) -> None:
    fold = fold_periods(crossing, ART, "hour")
    rows = fold.periods
    assert [p.start for p in rows] == [
        "2026-10-31T22:00:00-03:00",
        "2026-10-31T23:00:00-03:00",
        "2026-11-01T00:00:00-03:00",
        "2026-11-01T01:00:00-03:00",
        "2026-11-01T02:00:00-03:00",
        "2026-11-01T03:00:00-03:00",
    ]
    assert rows[-1].end == "2026-11-01T04:00:00-03:00"
    assert [p.length_ms for p in rows] == [HOUR] * 6
    assert [p.agent_ms for p in rows] == [HOUR // 2, HOUR, HOUR // 2, 0, 0, 0]
    assert [p.active_ms for p in rows] == [p.agent_ms for p in rows]
    assert [p.parallelism for p in rows] == [1, 1, 1, None, None, None]
    assert [p.calls for p in rows] == [0, 1, 1, 0, 0, 1]
    assert [p.usd for p in rows] == pytest.approx([0, 0.2608, 0.2608, 0, 0, 0.2608])
    assert sum(p.known_agent_ms for p in rows) == fold.activity.agent_ms
    assert sum(p.known_active_ms for p in rows) == fold.activity.active_ms
    assert sum(p.known_usd for p in rows) == pytest.approx(fold.prices.usd)
    assert fold.activity == fold_activity(crossing)
    assert fold.prices == price_usage(crossing)


def test_day_clips_at_local_midnight(crossing: list[ServerEvent]) -> None:
    fold = fold_periods(crossing, ART, "day")
    assert [p.start for p in fold.periods] == [
        "2026-10-31T00:00:00-03:00",
        "2026-11-01T00:00:00-03:00",
    ]
    assert [p.agent_ms for p in fold.periods] == [3 * HOUR // 2, HOUR // 2]
    assert [p.length_ms for p in fold.periods] == [24 * HOUR] * 2
    assert [p.usd for p in fold.periods] == pytest.approx([0.2608, 0.5216])


def test_review_counts_as_an_actor_and_active_time_is_a_union() -> None:
    events: list[ServerEvent] = [
        status(1, "2026-11-01T01:30:00.000Z", "starting"),
        status(2, "2026-11-01T01:45:00.000Z", "working", "agent-2"),
        status(3, "2026-11-01T02:15:00.000Z", "ready_for_review"),
        review(4, "2026-11-01T02:45:00.000Z"),
        status(5, "2026-11-01T03:15:00.000Z", "stopped", "agent-2"),
    ]
    fold = fold_periods(events, ART, "hour")
    assert [p.agent_ms for p in fold.periods] == [45 * 60_000, 105 * 60_000, 15 * 60_000]
    assert [p.active_ms for p in fold.periods] == [HOUR // 2, HOUR, HOUR // 4]
    assert [p.parallelism for p in fold.periods] == [1.5, 1.75, 1]
    assert sum(p.known_agent_ms for p in fold.periods) == fold.activity.agent_ms


def test_union_each_actor_before_clipping_and_keep_global_fold() -> None:
    events = [
        status(1, "2026-11-01T00:10:00.000Z", "working"),
        status(2, "2026-11-01T00:40:00.000Z", "paused"),
        status(3, "2026-11-01T00:20:00.000Z", "revising"),
        status(4, "2026-11-01T00:50:00.000Z", "done"),
    ]
    fold = fold_periods(events, UTC, "hour")
    (row,) = fold.periods
    assert fold.activity.coverage == "incomplete"
    assert fold.activity.known_agent_ms == HOUR
    assert row.agent_ms is row.active_ms is row.parallelism is None
    assert row.known_agent_ms == row.known_active_ms == 40 * 60_000


def test_invalid_actor_keeps_valid_active_pieces_as_activity_fold_does() -> None:
    events = [
        status(1, "2026-11-01T00:10:00.000Z", "working"),
        status(2, "2026-11-01T00:20:00.000Z", "paused"),
        status(3, "2026-11-01T00:30:00.000Z", "working"),
        status(4, "2026-11-01T00:25:00.000Z", "done"),
        status(5, "2026-11-01T01:00:00.000Z", "working", "agent-2"),
        status(6, "2026-11-01T01:15:00.000Z", "done", "agent-2"),
    ]
    fold = fold_periods(events, UTC, "hour")
    assert [p.known_agent_ms for p in fold.periods] == [0, HOUR // 4]
    assert [p.known_active_ms for p in fold.periods] == [10 * 60_000, HOUR // 4]
    assert all(p.agent_ms is p.active_ms is p.parallelism is None for p in fold.periods)
    assert sum(p.known_agent_ms for p in fold.periods) == fold.activity.known_agent_ms
    assert sum(p.known_active_ms for p in fold.periods) == fold.activity.known_active_ms


@pytest.mark.parametrize("role", ["agent", "review", "plan", "lead"])
def test_missing_activity_suppresses_every_period_including_empty(
    crossing: list[ServerEvent],
    role: Literal["agent", "review", "plan", "lead"],
) -> None:
    extra = tick(6, "2026-11-01T07:15:00.000Z", role=role)
    if role == "agent":
        extra = extra.model_copy(
            update={
                "payload": extra.payload.model_copy(
                    update={"agent_id": "agent-unknown"},
                )
            }
        )
    fold = fold_periods([*crossing, extra], ART, "hour")
    assert fold.activity.coverage == "missing_metrics"
    assert all(p.agent_ms is p.active_ms is p.parallelism is None for p in fold.periods)
    assert sum(p.known_agent_ms for p in fold.periods) == fold.activity.known_agent_ms == 2 * HOUR
    assert fold.periods[3].known_agent_ms == fold.periods[3].known_active_ms == 0
    assert all(p.usd is not None for p in fold.periods)


@pytest.mark.parametrize(
    "model,i,c,o,kind,coverage,known",
    [
        (MODEL, 1_000_000, 400_000, 1_000_000, "exact", "complete", 0.2608),
        (MODEL, 1_000_000, None, 0, "ranged", "missing_metrics", 0.002),
        ("unlisted", 1, 0, 0, "unpriced", "missing_metrics", 0),
        ("unlisted", 0, None, 0, "exact", "complete", 0),
        (MODEL, 1, 2, 0, "inconsistent", "incomplete", 0),
    ],
)
def test_shared_per_call_pricing_and_global_cost_coverage(
    crossing: list[ServerEvent],
    model: str,
    i: int,
    c: int | None,
    o: int,
    kind: str,
    coverage: str,
    known: float,
) -> None:
    extra = tick(6, "2026-11-01T07:15:00.000Z", model=model, i=i, c=c, o=o)
    call = price_call(extra.payload)
    assert call.kind == kind and call.known == pytest.approx(known)
    fold = fold_periods([*crossing, extra], ART, "hour")
    assert fold.prices.coverage == coverage
    assert fold.periods[-1].known_usd == pytest.approx(known)
    assert fold.periods[-1].unpriced_calls == int(kind == "unpriced")
    assert fold.periods[-1].ranged_calls == int(kind == "ranged")
    assert all((p.usd is not None) == (coverage == "complete") for p in fold.periods)
    assert fold.periods[3].known_usd == 0
    assert sum(p.known_usd for p in fold.periods) == pytest.approx(fold.prices.known_usd)
    assert fold.activity.coverage == "complete"


def test_custom_price_table_is_used_for_global_and_period_calls(
    crossing: list[ServerEvent],
) -> None:
    table = PriceTable("stored source", "2026-11-01", (ListPrice(MODEL, 1, 0.02, 2),))
    fold = fold_periods(crossing, ART, "hour", table)
    assert fold.prices == price_usage(crossing, table)
    assert sum(p.known_usd for p in fold.periods) == pytest.approx(3 * 2.608)
    receipt = crossing[1]
    assert receipt.type == "usage.tick"
    assert price_call(receipt.payload, table).known == pytest.approx(2.608)
    assert LIST_PRICES.retrieved == "2026-10-30"


def ny() -> ZoneInfo:
    try:
        return ZoneInfo("America/New_York")
    except ZoneInfoNotFoundError:
        pytest.skip("America/New_York unavailable: no system time-zone database")


@pytest.mark.parametrize("date,hours", [("2026-03-08", 23), ("2026-11-01", 25)])
def test_dst_day_length_and_whole_day_accounting(date: str, hours: int) -> None:
    zone = ny()
    start = datetime.fromisoformat(date).replace(tzinfo=zone)
    end = start + timedelta(days=1)
    events = [
        status(1, start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"), "working"),
        status(2, end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"), "done"),
    ]
    days = fold_periods(events, zone, "day")
    assert days.periods[0].length_ms == hours * HOUR
    assert days.periods[0].agent_ms == days.activity.agent_ms == hours * HOUR
    assert days.periods[1].agent_ms == 0  # The midnight endpoint's containing day.
    hourly = fold_periods(events, zone, "hour")
    rows = hourly.periods[:-1]
    assert len(rows) == hours and all(p.length_ms == HOUR for p in rows)
    assert sum(p.known_agent_ms for p in hourly.periods) == hourly.activity.agent_ms
    if hours == 23:
        assert not any(p.start.startswith(date + "T02:") for p in rows)
    else:
        repeated = [p for p in rows if p.start.startswith(date + "T01:")]
        assert [p.start for p in repeated] == [date + "T01:00:00-04:00", date + "T01:00:00-05:00"]
        assert repeated[0].end == repeated[1].start


def test_ticks_in_both_repeated_hours_land_in_distinct_periods() -> None:
    events = [tick(1, "2026-11-01T05:30:00.000Z"), tick(2, "2026-11-01T06:30:00.000Z")]
    fold = fold_periods(events, ny(), "hour")
    assert [p.start for p in fold.periods] == [
        "2026-11-01T01:00:00-04:00",
        "2026-11-01T01:00:00-05:00",
    ]
    assert [p.calls for p in fold.periods] == [1, 1]
    assert [p.usd for p in fold.periods] == pytest.approx([0.2608, 0.2608])


def test_seq_gap_refuses_with_original_price_usage_error() -> None:
    events = [tick(1, "2026-11-01T00:00:00.000Z"), tick(3, "2026-11-01T05:00:00.000Z")]
    with pytest.raises(ValueError) as original:
        price_usage(events)
    with pytest.raises(ValueError) as grouped:
        fold_periods(events, ART, "hour")
    assert str(grouped.value) == str(original.value) == "expected seq 2, got 3"


def test_empty_log_and_events_without_interval_or_tick_have_no_span() -> None:
    for events in ([], [review(1, "2026-11-01T00:00:00.000Z")]):
        fold = fold_periods(events, ART, "hour")
        assert fold.periods == ()
        assert fold.activity == fold_activity(events)
        assert fold.prices == price_usage(events)


def test_millisecond_edges_use_integer_arithmetic() -> None:
    events = [
        status(1, "2026-11-01T03:59:59.999Z", "working"),
        status(2, "2026-11-01T04:00:00.001Z", "done"),
    ]
    fold = fold_periods(events, ART, "hour")
    assert [p.agent_ms for p in fold.periods] == [1, 1]
