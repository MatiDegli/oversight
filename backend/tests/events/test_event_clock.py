"""Store timestamps advance monotonically while catching up to UTC wall time."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from oversight.changes import build_change_view
from oversight.events import EventStore, format_ts, parse_ts, read_store
from oversight.events import clock as clocks
from oversight.events import store as stores

from .test_stage_fold import log
from .test_store import step

NOW = datetime(2026, 11, 4, 13, 53, 7, 542000, tzinfo=UTC)
RUN_ID = "run-20261104-135307"


def scripted(wall: list[datetime], monotonic: list[float]) -> clocks.EventClock:
    return clocks.EventClock(wall_clock=iter(wall).__next__, monotonic=iter(monotonic).__next__)


def test_steady_time_follows_wall_clock() -> None:
    wall = [NOW, NOW + timedelta(seconds=1), NOW + timedelta(seconds=3)]
    clock = scripted(wall, [10, 11, 13])
    assert [clock() for _ in wall] == wall


def test_1428_ms_backward_step_preserves_monotonic_durations() -> None:
    wall = [NOW, NOW - timedelta(milliseconds=1428), NOW - timedelta(milliseconds=1178)]
    clock = scripted(wall, [10, 10.125, 10.375])
    assert [clock() for _ in wall] == [
        NOW,
        NOW + timedelta(milliseconds=125),
        NOW + timedelta(milliseconds=375),
    ]


def test_forward_jump_and_sleep_catch_up_to_wall_clock() -> None:
    # The monotonic source may exclude suspend time; wall time still wins on wake.
    wall = [NOW, NOW + timedelta(hours=4), NOW + timedelta(hours=4, seconds=1)]
    clock = scripted(wall, [10, 11, 12])
    assert [clock() for _ in wall] == wall


def test_mixed_sequence_never_decreases() -> None:
    wall = [NOW + timedelta(seconds=n) for n in [0, 1, -2, -1, 20, 18, 21, 40]]
    clock = scripted(wall, [10, 11, 12, 13, 14, 14, 15, 16])
    moments = [clock() for _ in wall]
    assert moments == [NOW + timedelta(seconds=n) for n in [0, 1, 2, 3, 20, 20, 21, 40]]
    assert moments == sorted(moments)


def test_results_are_aware_utc_including_the_first_call() -> None:
    art = timezone(timedelta(hours=-3))
    clock = scripted([NOW.astimezone(art), (NOW - timedelta(seconds=1)).astimezone(art)], [0, 1])
    first, second = clock(), clock()
    assert (first, second) == (NOW, NOW + timedelta(seconds=1))
    assert first.tzinfo is UTC and second.tzinfo is UTC


def test_naive_wall_clock_is_rejected() -> None:
    clock = scripted([NOW.replace(tzinfo=None)], [0])
    with pytest.raises(ValueError, match="timezone-aware"):
        clock()


@pytest.mark.anyio
async def test_default_stores_get_independent_clocks(tmp_path: Path, monkeypatch) -> None:
    created: list[clocks.EventClock] = []

    def fresh_clock() -> clocks.EventClock:
        clock = scripted([NOW, NOW - timedelta(seconds=1)], [0, 1])
        created.append(clock)
        return clock

    monkeypatch.setattr(stores, "EventClock", fresh_clock)
    async with (
        EventStore(tmp_path / "first.sqlite", RUN_ID) as first,
        EventStore(tmp_path / "second.sqlite", RUN_ID) as second,
    ):
        assert len(created) == 2 and created[0] is not created[1]
        assert first._clock is created[0] and second._clock is created[1]
        assert (await first.publish(step(1, 1))).ts == format_ts(NOW)
        assert (await first.publish(step(1, 2))).ts == format_ts(NOW + timedelta(seconds=1))
        assert (await second.publish(step(2, 1))).ts == format_ts(NOW)


@pytest.mark.anyio
async def test_store_commits_non_decreasing_timestamps(tmp_path: Path) -> None:
    wall = [NOW, NOW - timedelta(milliseconds=1428), NOW - timedelta(milliseconds=1000)]
    clock = scripted(wall, [0, 0.125, 0.5])
    async with EventStore(tmp_path / "run.sqlite", RUN_ID, clock=clock) as store:
        for number in range(1, 4):
            await store.publish(step(1, number))
    rows = read_store(store.path)
    assert [row.seq for row in rows] == [1, 2, 3]
    assert [row.ts for row in rows] == [
        format_ts(NOW + timedelta(milliseconds=ms)) for ms in [0, 125, 500]
    ]


@pytest.mark.anyio
async def test_injected_fixed_clock_stamps_exactly(tmp_path: Path) -> None:
    def fixed() -> datetime:
        return NOW

    async with EventStore(tmp_path / "run.sqlite", RUN_ID, clock=fixed) as store:
        assert store._clock is fixed
        first = await store.publish(step(1, 1))
        second = await store.publish(step(1, 2))
    assert first.ts == second.ts == "2026-11-04T13:53:07.542Z"


@pytest.mark.anyio
async def test_revision_two_wait_stays_known_across_backward_wall_step(tmp_path: Path) -> None:
    # flake-evidence.md: seq 35 receipt .526Z, seq 37 person's approval .525Z.
    # Reuse validated payload helpers; omit unrelated suites and renumber this complete prefix.
    events = log(
        ("change.proposed", 0, {"revision": 2}),
        ("change.facts_recorded", 16, {"revision": 2, "test_runs": [], "added_tests": []}),
        ("change.resolved", 15, {"revision": 2, "decision": "approved", "decided_by": "person"}),
    )
    wall = [parse_ts(f"2026-11-04T13:54:31.{ms}Z") for ms in ["510", "526", "525"]]
    clock = scripted(wall, [0, 0.016, 0.310])
    async with EventStore(tmp_path / "run.sqlite", RUN_ID, clock=clock) as store:
        for event in events:
            await store.publish(event.payload)
        view = build_change_view("change-1", store.committed_events())
    assert view.stages is not None
    assert view.stages.waiting_for_person.ms is not None
    assert view.stages.waiting_for_person.ms == 294
    assert not any("goes back" in problem for problem in view.stages.problems)
