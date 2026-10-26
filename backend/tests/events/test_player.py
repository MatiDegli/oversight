"""The fake player: the log's order and ts, through the store, at a speed setting."""

from pathlib import Path

import pytest

from oversight.events import EventStore, play, read_jsonl, read_store, replay_delays

pytestmark = pytest.mark.anyio
LOG = Path(__file__).resolve().parents[3] / "fixtures" / "events" / "handwritten-level1.jsonl"


def test_delays_follow_the_log_gaps_divided_by_speed_and_capped_at_two_seconds() -> None:
    events = read_jsonl(LOG)
    # The hand-written log has gaps of 40 ms, 2.4 s and 18.5 s at these positions.
    at_speed_1 = replay_delays(events)
    assert at_speed_1[0] == 0
    assert at_speed_1[1] == pytest.approx(0.04)
    assert at_speed_1[2] == pytest.approx(2.0)
    assert max(at_speed_1) == pytest.approx(2.0)
    at_speed_2 = replay_delays(events, speed=2)
    assert at_speed_2[1] == pytest.approx(0.02)
    assert at_speed_2[2] == pytest.approx(1.2)
    assert replay_delays(events, speed=0) == [0.0] * len(events)
    with pytest.raises(ValueError, match="speed"):
        replay_delays(events, speed=-1)


async def test_play_publishes_the_log_in_order_with_its_ts_and_waits_between(
    tmp_path: Path,
) -> None:
    events = read_jsonl(LOG)
    waits: list[float] = []

    async def record_sleep(seconds: float) -> None:
        waits.append(seconds)

    async with EventStore(tmp_path / "replay.sqlite", "run-20261027-140312") as store:
        await play(store, events, speed=1, sleep=record_sleep)

    rows = read_store(store.path)
    assert [(row.seq, row.ts, row.type) for row in rows] == [
        (event.seq, event.ts, event.type) for event in events
    ]
    assert waits == [delay for delay in replay_delays(events) if delay > 0]
