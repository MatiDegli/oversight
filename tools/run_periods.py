"""Report observed hours and dated list-price equivalents by local hour or day."""

import argparse
import json
import sys
from dataclasses import asdict
from datetime import UTC, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from oversight.events import LogError, read_jsonl
from oversight.events.usage_periods import UsagePeriod, fold_periods


def _figures(
    agent: int | None,
    known_agent: int,
    active: int | None,
    known_active: int,
    parallelism: float | None,
    usd: float | None,
    known_usd: float,
) -> str:
    agent_text = f"{'' if agent is not None else 'at least '}{known_agent / 3_600_000:.6f}"
    active_text = f"{'' if active is not None else 'at least '}{known_active / 3_600_000:.6f}"
    ratio = f"{parallelism:.6f}" if parallelism is not None else "—"
    cost = f"{'' if usd is not None else 'at least '}US${known_usd:.6f}"
    return (
        f"{agent_text} agent-hours; {active_text} active hours; "
        f"parallelism {ratio}; {cost} list-price equivalent"
    )


def _line(period: UsagePeriod) -> str:
    return (
        f"{period.start}; length {period.length_ms} ms; "
        + _figures(
            period.agent_ms,
            period.known_agent_ms,
            period.active_ms,
            period.known_active_ms,
            period.parallelism,
            period.usd,
            period.known_usd,
        )
        + f"; {period.unpriced_calls} unpriced, {period.ranged_calls} ranged calls"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--tz", required=True, help="IANA time-zone name, or UTC")
    parser.add_argument("--by", required=True, choices=("hour", "day"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        zone: tzinfo = UTC if args.tz == "UTC" else ZoneInfo(args.tz)
        fold = fold_periods(read_jsonl(args.log), zone, args.by)
    except (LogError, ValueError, OSError, ZoneInfoNotFoundError) as error:
        print(f"run periods refused: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(asdict(fold), sort_keys=True))
        return 0
    print(f"Run periods: {args.tz}, by {fold.unit}")
    for period in fold.periods:
        print(_line(period))
    activity, prices = fold.activity, fold.prices
    print(
        "Totals: "
        + _figures(
            activity.agent_ms,
            activity.known_agent_ms,
            activity.active_ms,
            activity.known_active_ms,
            activity.parallelism,
            prices.usd,
            prices.known_usd,
        )
        + f"; activity {activity.coverage}; cost {prices.coverage}; "
        f"list prices retrieved {prices.retrieved} from {prices.source_url}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
