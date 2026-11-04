"""The injectable clock behind every event's `ts` (docs/proposal.md, rule 7)."""

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

type Clock = Callable[[], datetime]
"""Returns the current time; it must be timezone-aware."""


def system_clock() -> datetime:
    return datetime.now(UTC)


class EventClock:
    """One store's UTC clock: advance by monotonic elapsed time, catching up to wall time."""

    def __init__(
        self,
        wall_clock: Clock = system_clock,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._wall_clock = wall_clock
        self._monotonic = monotonic
        self._previous: datetime | None = None
        self._previous_monotonic = 0.0

    def __call__(self) -> datetime:
        monotonic_now = self._monotonic()
        wall_now = self._wall_clock()
        if wall_now.tzinfo is None or wall_now.utcoffset() is None:
            raise ValueError("the clock must return a timezone-aware datetime")
        moment = wall_now.astimezone(UTC)
        if self._previous is not None:
            elapsed = timedelta(seconds=monotonic_now - self._previous_monotonic)
            moment = max(self._previous + elapsed, moment)
        self._previous = moment
        self._previous_monotonic = monotonic_now
        return moment


def format_ts(moment: datetime) -> str:
    """UTC with millisecond precision and a `Z`, as section 4.1 says: `2026-10-27T14:03:12.345Z`."""
    if moment.tzinfo is None:
        raise ValueError("the clock must return a timezone-aware datetime")
    utc = moment.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def parse_ts(ts: str) -> datetime:
    """The inverse of `format_ts`, for the replay player's gaps."""
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
