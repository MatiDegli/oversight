"""Catch copied construction clues in task text without changing frozen artifacts."""

import json
import re

import pytest
from tools.eval.behavior import catalog

BENCH = [patch for patch in catalog.load_patches() if patch.set == "bench"]
CONSTRUCTION = {
    row["id"]: row
    for row in json.loads((catalog.HERE / "construction-key.json").read_bytes())["patches"]
}


def distinctive_tokens(row: dict) -> set[str]:
    """Check location details and concrete values, not required public API names or prose."""
    location, _, detail = row["where"].partition(",")
    # Generic quantity/percentage values and ordinary descriptive words are not answer clues.
    ordinary = {"and", "for", "to", "whole", "amount", "empty"}
    numbers = set(re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])", row["witness"]))
    numbers -= {"-1", "0", "1", "100"}
    quoted = {
        value.strip().casefold()
        for value in re.findall(r'"([^"]*)"', row["witness"])
        if len(value.strip()) >= 3
    }
    filenames = set(re.findall(r"[\w-]+\.(?:txt|json|csv)\b", row["witness"]))
    details = set(re.findall(r"[a-z][a-z_-]*", detail.casefold())) - ordinary
    # A qualified source location is distinctive; the API name by itself is legitimate task text.
    return {location.split()[0].casefold()} | details | numbers | quoted | filenames


def assert_no_construction_hint(task: str, row: dict) -> None:
    shared = {
        token
        for token in distinctive_tokens(row)
        if re.search(r"(?<!\w)" + re.escape(token) + r"(?!\w)", task.casefold())
    }
    assert not shared, f"{row['id']}: task reveals construction clues: {sorted(shared)}"


@pytest.mark.parametrize("patch", BENCH, ids=lambda patch: patch.id)
def test_bench_tasks_do_not_reveal_construction_clues(patch):
    assert_no_construction_hint(patch.task, CONSTRUCTION[patch.id])


@pytest.mark.parametrize("hint", ["rounding", "599", "coupon-lookups.txt", "Sample"])
def test_blindness_guard_rejects_planted_hints(hint):
    row = next(row for row in CONSTRUCTION.values() if hint.casefold() in distinctive_tokens(row))
    patch = next(patch for patch in BENCH if patch.id == row["id"])
    with pytest.raises(AssertionError, match="task reveals construction clues"):
        assert_no_construction_hint(patch.task + f"\nReviewer hint: {hint}.\n", row)
