"""Compare labeled findings by commit/path/kind; never infer labels for new keys.

Usage: python tools/shortcut_precision.py LABELS BEFORE AFTER --output REPORT.json
Rows with the same identity may have different labels. They are all counted as
kept if that identity survives, even when the detector coalesces their lines.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import TypedDict


class Row(TypedDict):
    commit: str
    path: str
    kind: str


class LabeledRow(Row):
    label: str


def identity(row: Row) -> tuple[str, str, str]:
    return row["commit"], row["path"], row["kind"]


def compare(labels: list[LabeledRow], before: list[Row], after: list[Row]) -> dict[str, object]:
    baseline, current = {identity(row) for row in before}, {identity(row) for row in after}
    labeled = {identity(row) for row in labels}
    if labeled != baseline:
        raise ValueError("Labels must cover exactly the baseline finding identities")
    table: dict[str, dict[str, dict[str, int]]] = {}
    for kind in sorted({row["kind"] for row in labels}):
        table[kind] = {}
        for label in ("SHORTCUT", "USEFUL", "NOISE"):
            selected = [row for row in labels if row["kind"] == kind and row["label"] == label]
            table[kind][label] = {
                "before": len(selected),
                "after": sum(identity(row) in current for row in selected),
            }
    totals = {
        label: {
            "before": sum(row["label"] == label for row in labels),
            "after": sum(row["label"] == label and identity(row) in current for row in labels),
        }
        for label in ("SHORTCUT", "USEFUL", "NOISE")
    }
    return {
        "by_kind_and_label": table,
        "totals": totals,
        "actual_findings": {"before": len(before), "after": len(after)},
        "after_by_kind": dict(sorted(Counter(row["kind"] for row in after).items())),
        "new_unlabeled_keys": [list(key) for key in sorted(current - labeled)],
        "lost_nonnoise": [
            row for row in labels if row["label"] != "NOISE" and identity(row) not in current
        ],
        "gates": {
            "shortcut_kept": totals["SHORTCUT"]["after"] == totals["SHORTCUT"]["before"],
            "useful_kept": totals["USEFUL"]["after"] >= 14,
            "noise_removed": totals["NOISE"]["after"] <= totals["NOISE"]["before"] * 0.2,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("labels", type=Path)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = compare(
        [json.loads(line) for line in args.labels.read_text().splitlines()],
        [json.loads(line) for line in args.before.read_text().splitlines()],
        [json.loads(line) for line in args.after.read_text().splitlines()],
    )
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
