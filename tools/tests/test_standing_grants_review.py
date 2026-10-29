"""Guard the reviewed authority boundaries while standing grants remain a proposal.

These inspect the plan, not runtime behavior: no grants implementation is authorized yet.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLAN = "docs/plans/standing-grants.md"
DECISIONS = "DECISIONS.md"


def section(path: str, heading: str) -> str:
    document = (ROOT / path).read_text(encoding="utf-8")
    body = document.split(heading + "\n", 1)[1].split("\n## ", 1)[0]
    return " ".join(body.split())


def test_small_cone_waiver_requires_complete_bounded_observation() -> None:
    layers = section(PLAN, "## Three layers, each in one direction")
    for discriminator in (
        "`observed` is present",
        "`0 <= nodes <= 5`",
        "`omitted_by_depth == 0`",
        "`omitted_by_limit == 0`",
        "`parse_errors == 0`",
        "`unresolved_removed == 0`",
        "measured positive possible-callers count",
        "`observed=None` always waits for the person",
        "unmeasured caller count also waits",
    ):
        assert discriminator in layers


@pytest.mark.parametrize(
    "validator", ["ChangeResolved._rules_decision", "validate_resolution_intent"]
)
def test_contract_proposal_covers_pure_resolution_checks(validator: str) -> None:
    contract = section(PLAN, "## Contract change (proposed contracts 1.7.0)")
    assert validator in contract
    for evidence in (
        "GrantUse.authorization",
        "reason, scope, creation event seq and timestamp",
        "run/session ids",
        "checked_at",
        "active_at_effect",
        "change_id, agent_id, revision and head_commit",
        "WriteEvidence.grants",
        "validate_resolution_evidence",
        "without reading mutable grant state",
        "after effects",
        "revocation cannot invalidate a historical receipt",
    ):
        assert evidence in contract


def test_shots_use_existing_inbox_and_settings_homes() -> None:
    shots = section(PLAN, "## Shots the maintainer approves first")
    assert shots.index("decisions chip") < shots.index("2. ")
    assert shots.index("Settings tab") < shots.index("2. ")
    assert "focus panel" in shots
    assert "DESIGN.md" in shots


def test_grants_decision_retains_confirmed_free_text_and_expiry_override() -> None:
    decision = section(
        DECISIONS, "## 2026-10-29 — Standing grants: Oversight remembers what the person let go"
    )
    for clause in (
        "Free-text grants may come later",
        "structured grant he confirms",
        "unless he chooses otherwise",
    ):
        assert clause in decision


def test_small_cone_decision_discloses_replaced_audit_entry() -> None:
    decision = section(DECISIONS, "## 2026-10-29 — small_cone does not pass what it cannot see")
    assert "Small cones require no unobserved possible callers" in decision
    assert "13c08bc" in decision
    assert "replaced" in decision
    assert "not a new maintainer decision" in decision


def test_queue_names_remaining_inbox_work_instead_of_landed_rule() -> None:
    order = section(PLAN, "## Order and what it displaces")
    assert "already on main" in order
    assert "caller-listing inbox entry" in order
    assert "remains" in order
    assert "running now" not in order


def test_replay_filters_ended_grants_without_erasing_historical_uses() -> None:
    contract = section(PLAN, "## Contract change (proposed contracts 1.7.0)")
    for boundary in (
        "grant.expired",
        "run_ended",
        "session_ended",
        "replay event clock",
        "run ended",
        "session ended",
        "filters expired or revoked grants",
        "Historical card lines and use counters remain",
    ):
        assert boundary in contract
