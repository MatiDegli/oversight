"""Keep the creation delivery target distinct from the frozen approval history."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_start_agent_comment_reserves_zones_for_creation_delivery() -> None:
    source = (ROOT / "backend/src/oversight/api/commands.py").read_text()
    assert "request.zone is reserved and ignored until zones are delivered in 1.8.0." in source


def test_proposal_schedules_nullable_save_file_with_creation_in_1_8() -> None:
    proposal = (ROOT / "docs/proposal.md").read_text()
    freeze = proposal.split("### 4.9 ", 1)[1].split("## 5.", 1)[0]
    assert "1.4.0 keeps `SaveFile.base_hash` a required string." in freeze
    assert (
        "The separately delivered 1.8.0 makes it required and nullable only together with safe "
        "creation, zones and inventory."
    ) in " ".join(freeze.split())


def test_api_guard_history_is_preserved_and_superseded_in_1_5_entry() -> None:
    decisions = (ROOT / "DECISIONS.md").read_text()
    guard = decisions.split("## API guard —", 1)[1].split("## Shared byte limits", 1)[0]
    assert (
        "- **G6:** Use the already approved `forbidden` code from contracts 1.4.0; this front "
        "changes no contracts. 1.5.0 remains reserved for safe creation, zones and inventory."
    ) in " ".join(guard.split())
    delivery = decisions.split("## Contracts 1.5.0 —", 1)[1]
    assert (
        "safe creation, nullable SaveFile.base_hash, zones and inventory move to 1.6.0."
    ) in " ".join(delivery.split())


def test_grants_take_1_7_and_creation_moves_to_1_8() -> None:
    decisions = (ROOT / "DECISIONS.md").read_text()
    earlier = decisions.split("## 2026-10-28 — Contracts 1.6.0 —", 1)[1]
    assert "bundle to 1.7.0 because they are not designed yet" in " ".join(earlier.split())
    grants = decisions.split("## 2026-10-29 — Standing grants approved as contracts 1.7.0", 1)[1]
    grants = grants.split("\n## ", 1)[0]
    assert (
        "safe creation, nullable SaveFile.base_hash, zones and inventory move to 1.8.0."
    ) in " ".join(grants.split())


@pytest.mark.parametrize("document", ["docs/proposal.md", "ARCHITECTURE.md"])
def test_grants_route_deviation_stays_in_decisions(document: str) -> None:
    """This backend front records deviations without rewriting approved API documents."""
    route = "POST /grants/{grant_id}/revoke"
    assert route not in (ROOT / document).read_text(encoding="utf-8")
    decisions = (ROOT / "DECISIONS.md").read_text(encoding="utf-8")
    grants = decisions.split("## 2026-10-29 — Contracts 1.7.0 —", 1)[1].split("\n## ", 1)[0]
    assert route in grants
