"""Keep configuration attribution's public scope and rendering claims accurate."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INTERNAL_COORDINATION = re.compile(r"\b(?:packet|front|branch)\b|\bplan revision\b", re.I)


def test_inventory_describes_current_configuration_note_rendering() -> None:
    additions = (ROOT / "docs/additions.md").read_text(encoding="utf-8")
    facts = additions.split("### 1. Facts and a summary from events\n", 1)[1]
    attribution = facts.lstrip().split("\n\n", 1)[0]
    normalized = " ".join(attribution.split())
    assert (
        "The review facts render each note as its own line, apart from “Incomplete:”, "
        'which still joins the other analysis limitations with "; ".'
    ) in normalized
    assert (
        "The stable `configuration attribution: ` prefix already enables this separate rendering."
    ) in normalized


def test_configuration_decision_describes_current_note_rendering() -> None:
    decisions = (ROOT / "DECISIONS.md").read_text(encoding="utf-8")
    entry = decisions.split(
        "## 2026-11-03 — Configuration attribution stays advisory "
        "and uses the current change base\n",
        1,
    )[1].split("\n## ", 1)[0]
    assert (
        "The current frontend renders each note as its own line, apart from “Incomplete:”, "
        'which still joins the other analysis limitations with "; ".'
    ) in " ".join(entry.split())


def test_configuration_decision_records_authorized_scope_without_coordination_names() -> None:
    decisions = (ROOT / "DECISIONS.md").read_text(encoding="utf-8")
    entry = decisions.split(
        "## 2026-11-03 — Configuration attribution stays advisory "
        "and uses the current change base\n",
        1,
    )[1].split("\n## ", 1)[0]
    assert INTERNAL_COORDINATION.search(entry) is None
    assert (
        "The maintainer authorized advisory configuration attribution for "
        "demo/Boltons agent suites and proposal review."
    ) in " ".join(entry.split())


def test_configuration_devlog_describes_work_without_coordination_names() -> None:
    devlog = (ROOT / "DEVLOG.md").read_text(encoding="utf-8")
    entry = devlog.split("\n- 2026-11-03 —", 1)[1].split("\n- ", 1)[0]
    addition = next(
        paragraph
        for paragraph in re.split(r"\n(?=  \S)", entry)
        if "backend configuration attribution" in paragraph
    )
    assert INTERNAL_COORDINATION.search(addition) is None
    assert addition.startswith(
        "  Added backend configuration attribution from captured Git objects"
    )
