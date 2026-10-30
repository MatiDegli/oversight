"""Keep proof 2's frozen rules faithful to the approved reporting decisions."""

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "docs/plans/proof-2.md"
DECISION = "2026-10-30 — Proof 2's tasks and reporting rules are fixed before the run"


def section(heading: str) -> str:
    body = PLAN.read_text(encoding="utf-8").split(heading + "\n", 1)[1]
    return " ".join(body.split("\n## ", 1)[0].split())


def test_decision_records_rules_without_rewriting_main_history() -> None:
    document = (ROOT / "DECISIONS.md").read_bytes()
    before, separator, after = document.partition(f"\n## {DECISION}\n".encode())
    assert separator, "The frozen plan needs its own decision entry"
    # Exact DECISIONS.md bytes on main 1985dfc, including its final newline.
    assert hashlib.sha256(before).hexdigest() == (
        "677f2b4e2cc487c1c00938207084566b832abf59d2c2a6bcde73ee85ffea941f"
    )
    entry = f"## {DECISION}\n" + after.decode().split("\n## ", 1)[0]
    assert len(entry.strip().splitlines()) <= 30
    for rule in (
        "D1",
        "D2",
        "D3",
        "D4",
        "E1",
        "E2",
        "E3",
        "E4",
        "E5",
        "E6",
        "E7",
        "H1",
        "L1",
        "L2",
        "L3",
        "P1",
        "P2",
        "P3",
        "P4",
        "P5",
        "P6",
        "checks_unavailable",
        "review_card",
        "risky_area",
        "examples_before_code",
        "lead_plan",
        "follow_main",
        "Under 10",
        "queues the 16 tasks at once",
        "[plan](docs/plans/proof-2.md)",
    ):
        assert rule in entry, rule
    assert "#2026-10-30--proof-2s-tasks-and-reporting-rules-are-fixed-before-the-run" in section(
        "## Status"
    )


def test_approval_covers_only_proposals_defined_in_the_plan() -> None:
    status = section("## Status")
    approval = re.search(r"P1-P(\d+) were approved", status)
    assert approval is not None
    approved = {f"P{number}" for number in range(1, int(approval[1]) + 1)}
    defined = set(re.findall(r"^(P\d+), [^:\n]+:", PLAN.read_text(), re.MULTILINE))
    assert approved == defined == {"P1", "P2", "P3", "P4", "P5"}


def test_harness_pair_waits_for_both_passes_before_paying() -> None:
    harness = section("## The harness-pack pair (H1)")
    assert "The pair runs only after both passes meet these three conditions" in harness
    assert "at most four cases" in harness
    assert "both passes have the same count" in harness
    assert "whose tests pass in both passes flips between them" in harness
    assert "Bring the numbers to the maintainer before any paid call" in harness
    assert "Passing the gate does not itself" not in harness


def test_queuing_keeps_the_persons_sixteen_start_decisions() -> None:
    tasks = section("## Repository and tasks")
    assert "Task 15 runs after task 5; task 16 runs alone." in tasks
    assert "Queuing all tasks does not bypass these execution constraints" not in tasks
    run = section("## How the run goes")
    assert (
        "The person queues the 16 tasks at once, those starts count as the person's decisions"
        in run
    )


def test_finish_omissions_are_separate_from_derived_field_counts() -> None:
    reporting = section("## What the report measures")
    finish = reporting.split("**E7.**", 1)[1]
    assert "A claim omitted in finish is counted apart." in finish
    assert "Derived reporting detail:" in finish
    derived = finish.split("Derived reporting detail:", 1)[1]
    assert "count agreement and disagreement per field" in derived
    assert "Never parse prose with a model" in finish


def test_reliability_uses_a_grammatical_main_version_statement() -> None:
    reporting = section("## What the report measures")
    assert "Main still has contracts 1.7.0" in reporting
    assert "That main still has" not in reporting
