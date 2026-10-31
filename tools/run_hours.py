"""Report observed agent-hours, active hours and parallelism for a recorded event log."""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from oversight.events import ActivityFold, LogError, fold_activity, read_jsonl


def _hours(ms: int) -> tuple[str, str]:
    seconds = ms // 1000
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{ms / 3_600_000:.2f}", f"{hours}:{minutes:02d}:{seconds:02d}"


def _line(fold: ActivityFold) -> str:
    counts: list[str] = []
    for role in ("agent", "review", "lead"):
        count = sum(actor.role == role for actor in fold.actors)
        if role != "lead" or count:
            counts.append(f"{count} {role}{'' if count == 1 else 's'}")
    lower = "" if fold.coverage == "complete" else "at least "
    agent_hours, agent_clock = _hours(fold.known_agent_ms)
    active_hours, active_clock = _hours(fold.known_active_ms)
    parallelism = f"{fold.parallelism:.2f}" if fold.parallelism is not None else "unavailable"
    problems = f": {'; '.join(fold.problems)}" if fold.coverage != "complete" else ""
    return (
        f"{', '.join(counts)}: {lower}{agent_hours} agent-hours ({agent_clock}) over "
        f"{lower}{active_hours} active hours ({active_clock}); parallelism {parallelism}; "
        f"through seq {fold.through_seq}; coverage {fold.coverage}{problems}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--json", action="store_true", help="print the complete fold as JSON")
    args = parser.parse_args(argv)
    try:
        fold = fold_activity(read_jsonl(args.log))
    except (LogError, ValueError, OSError) as error:
        print(f"run hours refused: {error}", file=sys.stderr)
        return 1
    print(json.dumps(asdict(fold), sort_keys=True) if args.json else _line(fold))
    return 0


if __name__ == "__main__":
    sys.exit(main())
