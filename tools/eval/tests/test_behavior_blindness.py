"""Catch construction clues in tasks and added comments without changing frozen artifacts."""

import json
import re
from dataclasses import replace

import pytest
from tools.eval.behavior import catalog

PATCHES = catalog.load_patches()
BENCH = [patch for patch in PATCHES if patch.set == "bench"]
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


def assert_no_construction_hint(text: str, row: dict) -> None:
    shared = {
        token
        for token in distinctive_tokens(row)
        if re.search(r"(?<!\w)" + re.escape(token) + r"(?!\w)", text.casefold())
    }
    assert not shared, f"{row['id']}: public text reveals construction clues: {sorted(shared)}"


@pytest.mark.parametrize(
    ("patch", "source"),
    [(patch, "task") for patch in BENCH] + [(patch, "comments") for patch in PATCHES],
    ids=[f"{patch.id}-task" for patch in BENCH] + [f"{patch.id}-comments" for patch in PATCHES],
)
def test_public_text_does_not_reveal_construction_clues(patch, source):
    texts = (
        [patch.task]
        if source == "task"
        else [
            line[1:].lstrip()
            for line in patch.diff.splitlines()
            if line.startswith("+") and line[1:].lstrip().startswith("#")
        ]
    )
    for text in texts:
        assert_no_construction_hint(text, CONSTRUCTION[patch.id])


@pytest.mark.parametrize("hint", ["rounding", "599", "coupon-lookups.txt", "Sample"])
@pytest.mark.parametrize("indent", ["", "    ", "\t"])
def test_blindness_guard_rejects_hints_in_added_comments(hint, indent):
    row = next(row for row in CONSTRUCTION.values() if hint.casefold() in distinctive_tokens(row))
    patch = next(patch for patch in BENCH if patch.id == row["id"])
    changed = replace(patch, diff=patch.diff + f"+{indent}# Reviewer hint: {hint}.\n")
    with pytest.raises(AssertionError, match="reveals construction clues"):
        test_public_text_does_not_reveal_construction_clues(changed, "comments")


@pytest.mark.parametrize("hint", ["rounding", "599", "coupon-lookups.txt", "Sample"])
def test_blindness_guard_rejects_planted_hints(hint):
    row = next(row for row in CONSTRUCTION.values() if hint.casefold() in distinctive_tokens(row))
    patch = next(patch for patch in BENCH if patch.id == row["id"])
    with pytest.raises(AssertionError, match="reveals construction clues"):
        assert_no_construction_hint(patch.task + f"\nReviewer hint: {hint}.\n", row)
