"""Keep the creation delivery target distinct from the frozen approval history."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_start_agent_comment_reserves_zones_for_creation_delivery() -> None:
    source = (ROOT / "backend/src/oversight/api/commands.py").read_text()
    assert "request.zone is reserved and ignored until zones are delivered in 1.12.0." in source


def test_proposal_schedules_nullable_save_file_with_creation_in_1_12() -> None:
    proposal = (ROOT / "docs/proposal.md").read_text()
    freeze = proposal.split("### 4.9 ", 1)[1].split("## 5.", 1)[0]
    assert "1.4.0 keeps `SaveFile.base_hash` a required string." in freeze
    assert (
        "The separately delivered 1.12.0 makes it required and nullable only together with safe "
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


def test_messages_note_reserves_creation_after_probe_input_evidence() -> None:
    decisions = (ROOT / "DECISIONS.md").read_text()
    entry = decisions.split("## 2026-10-31 — Message text persists; privacy at export", 1)[1]
    assert (
        "1.9.0 to 1.11.0, superseding its earlier reservations; 1.10.0 is approved for "
        "probe-inputs parameter-extension evidence."
    ) in " ".join(entry.split())


def test_usage_view_takes_1_11_and_creation_moves_to_1_12() -> None:
    proposal = " ".join((ROOT / "docs/proposal.md").read_text().split())
    decisions = " ".join((ROOT / "DECISIONS.md").read_text().split())
    assert (
        "Later that day the usage view took 1.11.0, moving safe creation to 1.12.0."
    ) in proposal
    assert "The reserved safe creation bundle moves from 1.11.0 to 1.12.0." in decisions
