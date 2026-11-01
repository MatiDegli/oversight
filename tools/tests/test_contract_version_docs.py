"""Keep next-free delivery versions distinct from the frozen approval history."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_start_agent_comment_reserves_zones_for_creation_delivery() -> None:
    source = (ROOT / "backend/src/oversight/api/commands.py").read_text()
    assert (
        "request.zone: reserved and ignored until zones are delivered in a later contract version."
    ) in source


def test_proposal_assigns_creation_version_at_landing_after_approval() -> None:
    proposal = (ROOT / "docs/proposal.md").read_text()
    freeze = proposal.split("### 4.9 ", 1)[1].split("## 5.", 1)[0]
    assert "1.4.0 keeps `SaveFile.base_hash` a required string." in freeze
    assert (
        "The safe creation bundle makes it required and nullable only together with safe creation, "
        "zones and inventory, in a later contract version assigned at landing "
        "after its own approval."
    ) in " ".join(freeze.split())
    assert (
        "versions are assigned next free at landing; the lead runtime landed first and took 1.10.0 "
        "(6818b3b)."
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


def test_messages_reservation_is_history_superseded_by_landing_order() -> None:
    decisions = (ROOT / "DECISIONS.md").read_text()
    entry = decisions.split("## 2026-10-31 — Message text persists; privacy at export", 1)[1]
    entry = entry.split("\n## ", 1)[0]
    assert (
        "1.9.0 to 1.11.0, superseding its earlier reservations; 1.10.0 is approved for "
        "probe-inputs parameter-extension evidence."
    ) in " ".join(entry.split())
    assert (
        "Amended 2026-10-31 (Docs audit): versions are assigned next free at landing; "
        "the chain above is approval history."
    ) in " ".join(entry.split())
    audit = decisions.split("## 2026-10-31 — Docs audit: the record follows main", 1)[1]
    assert "The lead runtime took 1.10.0 when it landed first (6818b3b)" in audit
    assert "This supersedes the reservations in the version chain." in audit


def test_e3_version_reservation_is_superseded_without_rewriting_history() -> None:
    decisions = (ROOT / "DECISIONS.md").read_text()
    entry = decisions.split("## 2026-10-31 — Item 12 E1:", 1)[1].split("\n## ", 1)[0]
    assert "Q1 reserves the typed confirmation command for E3, contract 1.13.0" in entry
    assert (
        "E3's typed confirmation command takes a later contract version, "
        "assigned at landing after its own approval."
    ) in entry


def test_usage_version_records_landing_order_without_reserving_creation() -> None:
    decisions = (ROOT / "DECISIONS.md").read_text()
    entry = decisions.split("## Contracts 1.12.0 —", 1)[1].split("\n## ", 1)[0]
    assert (
        "The usage view took 1.12.0 at landing, next free after the lead runtime (1.10.0) "
        "and the parameter-extension evidence (1.11.0)."
    ) in " ".join(entry.split())
    assert (
        "Safe creation takes a later contract version assigned at landing after its own approval."
    ) in " ".join(entry.split())
