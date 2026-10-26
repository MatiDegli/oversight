"""The injectable clock behind every event's `ts` (docs/proposal.md, rule 7)."""

from collections.abc import Callable
from datetime import UTC, datetime

type Clock = Callable[[], datetime]
"""Returns the current time; it must be timezone-aware."""


def system_clock() -> datetime:
    return datetime.now(UTC)


def format_ts(moment: datetime) -> str:
    """UTC with millisecond precision and a `Z`, as section 4.1 says: `2026-10-27T14:03:12.345Z`."""
    if moment.tzinfo is None:
        raise ValueError("the clock must return a timezone-aware datetime")
    utc = moment.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def parse_ts(ts: str) -> datetime:
    """The inverse of `format_ts`, for the replay player's gaps."""
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
