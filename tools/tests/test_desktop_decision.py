"""Preserve the desktop license approval and neighboring decision boundaries."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HEADING = "## 2026-11-03 — Bounded native desktop preparation; executable deferred"


def test_desktop_grant_preserves_approved_scope_separately_from_locked_inventory() -> None:
    decisions = (ROOT / "DECISIONS.md").read_text(encoding="utf-8")
    entry = decisions.split(HEADING + "\n", 1)[1].split("\n## ", 1)[0]
    paragraphs = entry.split("\n\n")
    grant = (
        "Maintainer approved MPL-2.0, Unicode-3.0 and Zlib only for the desktop shell\u2019s "
        "Rust crates listed in license-report.md, used unmodified."
    )
    assert grant in paragraphs
    inventory = "The granted crates present in `desktop/Cargo.lock`, grouped by license, are:"
    assert paragraphs[paragraphs.index(grant) + 1] == inventory


@pytest.mark.parametrize("boundary", ["start", "end"])
def test_desktop_decision_has_one_blank_line_at_both_boundaries(boundary: str) -> None:
    decisions = (ROOT / "DECISIONS.md").read_text(encoding="utf-8")
    before, after = decisions.split(HEADING + "\n", 1)
    rollback, _ = after.split("2026-11-03 — Dormant views with references.", 1)
    preceding = before if boundary == "start" else rollback
    assert preceding.endswith("\n\n") and not preceding.endswith("\n\n\n")
