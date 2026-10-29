"""Regression checks for the proposed grant authority and its reviewable scope."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLAN = "docs/plans/standing-grants.md"


def section(path: str, heading: str) -> str:
    document = (ROOT / path).read_text(encoding="utf-8")
    body = document.split(heading + "\n", 1)[1].split("\n## ", 1)[0]
    return " ".join(body.split())


@pytest.mark.parametrize(
    ("gate", "sources"),
    [
        ("pending_policy", ("_rules.py", "combined_tests")),
        ("merges_alone", ("_policy.py", "_transaction.py", "_candidate.py")),
    ],
)
def test_both_authorization_gates_accept_grants_without_rewriting_results(
    gate: str, sources: tuple[str, ...]
) -> None:
    layers = section(PLAN, "## Three layers, each in one direction")
    assert "never turns a rule's recorded result into `pass`" in layers
    assert "`pass`, or `unknown`" in layers
    assert "entire wait reason is covered by an active grant" in layers
    assert "any `fail` still block" in layers
    assert f"`{gate}`" in layers
    assert all(source in layers for source in sources)
    smallest = section(PLAN, "## Smallest version that helps proof 2")
    assert f"`{gate}`" in smallest


def test_grant_activity_is_rechecked_until_the_merge_effect() -> None:
    smallest = section(PLAN, "## Smallest version that helps proof 2")
    for requirement in (
        "admission, the candidate re-measure, and every revalidation barrier",
        "current server state",
        "revoke, expiry or the run ending",
        "stops the in-flight rules merge",
        "`authority_suspended`",
        "merge effect boundary",
    ):
        assert requirement in smallest


def test_grant_reasons_map_to_exact_rule_ids() -> None:
    contract = section(PLAN, "## Contract change (proposed contracts 1.7.0)")
    assert "| `possible_callers` | `small_cone` |" in contract
    assert "| `protected_path` | `protected_paths` |" in contract
    assert "one-to-one reason-to-rule mapping" in contract


def test_queue_distinguishes_the_grants_proposal_from_the_oct_28_set() -> None:
    additions = (ROOT / "docs/additions.md").read_text(encoding="utf-8")
    row = next(line for line in additions.splitlines() if "| [Standing grants]" in line)
    proposal_cell = row.split("|")[-2].strip()
    assert proposal_cell.startswith("Separate 1.7.0 proposal (2026-10-29)")
    assert "not in the Oct 28 set" in proposal_cell


@pytest.mark.parametrize(
    ("path", "heading"),
    [
        (PLAN, "## Why now"),
        ("DECISIONS.md", "## 2026-10-29 — small_cone does not pass what it cannot see"),
    ],
)
def test_unpublished_counts_are_estimates_not_repository_evidence(path: str, heading: str) -> None:
    rationale = section(path, heading)
    assert "446 of 898" in rationale
    assert "5 of the 16" in rationale
    assert "planning estimates" in rationale
    assert "not reproducible from this repository" in rationale
    assert "not `tools/eval/tasks.json`" in rationale


def test_displaced_work_is_defined_without_external_front_labels() -> None:
    order = section(PLAN, "## Order and what it displaces")
    assert "F5" not in order
    assert "changes/nits2" not in order
    assert "[item 9](../additions.md#9-a-change-card-with-three-states)" in order
    assert "decision-veto wiring into rules admission" in order
    assert "follow-ups are not enumerated in this repository" in order
    assert "before scheduling them" in order
