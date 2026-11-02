"""Pure local-hour/day accounting over a complete recorded prefix; globals own totals."""

from bisect import bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from math import fsum
from typing import Literal

from oversight.events.activity_fold import ActivityFold, fold_activity
from oversight.events.clock import parse_ts
from oversight.events.list_prices import (
    LIST_PRICES,
    ListPriceFold,
    PriceTable,
    price_call,
    price_usage,
)
from oversight_contracts.events import ServerEvent

type PeriodUnit = Literal["hour", "day"]
type _Interval = tuple[int, int]
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MS = timedelta(milliseconds=1)


@dataclass(frozen=True)
class UsagePeriod:
    start: str
    end: str
    length_ms: int
    known_agent_ms: int
    agent_ms: int | None
    known_active_ms: int
    active_ms: int | None
    parallelism: float | None
    calls: int
    known_usd: float
    usd: float | None
    unpriced_calls: int
    ranged_calls: int


@dataclass(frozen=True)
class PeriodFold:
    zone: str
    unit: PeriodUnit
    periods: tuple[UsagePeriod, ...]
    activity: ActivityFold
    prices: ListPriceFold


def _union(intervals: Iterable[_Interval]) -> tuple[_Interval, ...]:
    merged: list[_Interval] = []
    for start, end in sorted(intervals):
        if end < start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return tuple(merged)


def _duration(intervals: Iterable[_Interval], start: int, end: int) -> int:
    return sum(max(0, min(stop, end) - max(begin, start)) for begin, stop in intervals)


def _instant(ms: int) -> datetime:
    return _EPOCH + ms * _MS


def _wall_boundary(wall: datetime, zone: tzinfo, unit: PeriodUnit) -> set[int]:
    """Round trips omit imaginary hours and preserve both offsets of an ambiguous hour."""
    valid: set[int] = set()
    forward: set[int] = set()
    for fold in (0, 1):
        utc = wall.replace(tzinfo=zone, fold=fold).astimezone(UTC)
        local = utc.astimezone(zone).replace(tzinfo=None)
        ms = (utc - _EPOCH) // _MS
        if local == wall:
            valid.add(ms)
        if local >= wall:
            forward.add(ms)
    if unit == "hour":
        return valid
    # A midnight offset change still starts one day. If midnight was skipped, the
    # day starts at its first real instant; a wholly skipped date adds no period.
    candidates = valid or forward
    return {min(candidates)} if candidates else set()


def _boundaries(points: Sequence[int], zone: tzinfo, unit: PeriodUnit) -> tuple[int, ...]:
    if not points:
        return ()
    local = [_instant(point).astimezone(zone).replace(tzinfo=None) for point in points]
    wall = min(local).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    stop = max(local).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=2)
    step = timedelta(hours=1) if unit == "hour" else timedelta(days=1)
    boundaries: set[int] = set()
    while wall <= stop:
        boundaries.update(_wall_boundary(wall, zone, unit))
        wall += step
    ordered = sorted(boundaries)
    first = bisect_right(ordered, min(points)) - 1
    last = bisect_right(ordered, max(points))
    # The final endpoint belongs to its containing period, even at an exact edge.
    return tuple(ordered[first : last + 1])


@dataclass
class _Cost:
    amounts: list[float] = field(default_factory=list[float])
    unpriced: int = 0
    ranged: int = 0


def _period(
    start: int,
    end: int,
    zone: tzinfo,
    agent_intervals: Sequence[tuple[_Interval, ...]],
    active_intervals: tuple[_Interval, ...],
    cost: _Cost,
    activity: ActivityFold,
    prices: ListPriceFold,
) -> UsagePeriod:
    known_agent = sum(_duration(intervals, start, end) for intervals in agent_intervals)
    known_active = _duration(active_intervals, start, end)
    agent = known_agent if activity.coverage == "complete" else None
    active = known_active if activity.coverage == "complete" else None
    known_usd = fsum(cost.amounts)
    return UsagePeriod(
        start=_instant(start).astimezone(zone).isoformat(),
        end=_instant(end).astimezone(zone).isoformat(),
        length_ms=end - start,
        known_agent_ms=known_agent,
        agent_ms=agent,
        known_active_ms=known_active,
        active_ms=active,
        parallelism=agent / active if agent is not None and active else None,
        calls=len(cost.amounts),
        known_usd=known_usd,
        usd=known_usd if prices.coverage == "complete" else None,
        unpriced_calls=cost.unpriced,
        ranged_calls=cost.ranged,
    )


def fold_periods(
    events: Sequence[ServerEvent],
    zone: tzinfo,
    unit: PeriodUnit,
    table: PriceTable = LIST_PRICES,
) -> PeriodFold:
    """Split observed intervals and call receipts; never derive global totals from periods.

    Both source folds validate the whole prefix before any grouping. Their coverage
    governs every period, including empty periods. No interval or tick means no span.
    """
    activity = fold_activity(events)
    prices = price_usage(events, table)
    if unit not in ("hour", "day"):
        raise ValueError(f"unknown period unit: {unit!r}")
    actors = [
        _union((interval.start_ms, interval.end_ms) for interval in actor.intervals)
        for actor in activity.actors
    ]
    # Match activity_fold._Builder.finish: only actors with known ms contribute
    # agent time. Match activity_fold._active_ms: all valid intervals contribute
    # active time, even valid pieces of an actor whose other intervals are invalid.
    agent_intervals = [
        intervals
        for actor, intervals in zip(activity.actors, actors, strict=True)
        if actor.ms is not None
    ]
    active_intervals = _union(interval for intervals in actors for interval in intervals)
    ticks = [
        ((parse_ts(event.ts) - _EPOCH) // _MS, event.payload)
        for event in events
        if event.type == "usage.tick"
    ]
    points = [
        edge
        for actor in activity.actors
        for i in actor.intervals
        for edge in (i.start_ms, i.end_ms)
    ]
    points.extend(ms for ms, _ in ticks)
    bounds = _boundaries(points, zone, unit)
    costs = [_Cost() for _ in bounds[:-1]]
    for ms, tick in ticks:
        call = price_call(tick, table)
        cost = costs[bisect_right(bounds, ms) - 1]
        cost.amounts.append(call.known)
        cost.unpriced += call.kind == "unpriced"
        cost.ranged += call.kind == "ranged"
    periods = tuple(
        _period(start, end, zone, agent_intervals, active_intervals, cost, activity, prices)
        for start, end, cost in zip(bounds[:-1], bounds[1:], costs, strict=True)
    )
    return PeriodFold(str(zone), unit, periods, activity, prices)
