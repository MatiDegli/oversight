"""Keep the creation delivery target distinct from the frozen approval history."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_start_agent_comment_reserves_zones_for_creation_delivery() -> None:
    source = (ROOT / "backend/src/oversight/api/commands.py").read_text()
    assert "request.zone is reserved and ignored until zones are delivered in 1.6.0." in source


def test_proposal_schedules_nullable_save_file_with_creation_in_1_6() -> None:
    proposal = (ROOT / "docs/proposal.md").read_text()
    freeze = proposal.split("### 4.9 ", 1)[1].split("## 5.", 1)[0]
    assert "1.4.0 keeps `SaveFile.base_hash` a required string." in freeze
    assert (
        "The separately delivered 1.6.0 makes it required and nullable only together with safe "
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
