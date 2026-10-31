"""Summarize supervision and spend from one recorded event log, without decision authority."""

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from oversight.events import (
    LogError,
    SupervisionFold,
    SupervisionTotals,
    fold_supervision,
    read_jsonl,
)
from oversight_contracts.review import ChangeUsage


def _dollars(cost: float | None, unknown: int) -> str:
    return f"US${cost:.6f}" if cost is not None else f"unknown ({unknown} calls with unknown cost)"


def _row(label: str, row: SupervisionTotals, merged: int) -> str:
    net = "unknown" if row.net_lines_merged is None else str(row.net_lines_merged)
    outcomes = row.outcomes
    return (
        f"{label}: {merged} of {row.changes} changes merged; {row.proposals} proposals; "
        f"{row.review_rounds} review rounds; {row.open_changes} open changes; "
        f"resolutions: merged {outcomes.merged}, conflict {outcomes.conflict}, "
        f"changes_requested {outcomes.changes_requested}, rejected {outcomes.rejected}, "
        f"stopped {outcomes.stopped}; lines proposed +{row.lines_added} -{row.lines_removed}; "
        f"net lines merged {net}; {row.steps} steps, {row.step_ms} ms; "
        f"cost {_dollars(row.cost_usd, row.unknown_calls)}"
    )


def _text(fold: SupervisionFold) -> str:
    merged = [c for c in fold.changes if c.proposals and c.end == "merged"]
    lines = [_row("Run", fold.run, len(merged))]
    lines.extend(
        _row(a.agent_id, a, sum(c.agent_id == a.agent_id for c in merged)) for a in fold.agents
    )
    spend = "; ".join(
        f"{field.name}: {value.changes} changes, {_dollars(value.cost_usd, value.unknown_calls)}"
        for field in dataclasses.fields(fold.not_merged)
        for value in [getattr(fold.not_merged, field.name)]
    )
    lines.append(f"Spend that did not reach main: {spend}")
    decisions = ", ".join(
        f"{key} {value}" for key, value in dataclasses.asdict(fold.decisions).items()
    )
    lines.append(f"Decisions: {decisions}; through seq {fold.through_seq}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        fold = fold_supervision(read_jsonl(args.log))
    except (LogError, ValueError, OSError) as error:
        message = " ".join(str(error).splitlines())
        print(f"supervision summary refused: {message}", file=sys.stderr)
        return 1
    print(
        json.dumps(dataclasses.asdict(fold), sort_keys=True, default=ChangeUsage.model_dump)
        if args.json
        else _text(fold)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
