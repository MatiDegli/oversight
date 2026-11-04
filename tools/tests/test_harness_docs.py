"""The harness inventory must advertise the current default and navigation aids."""

from pathlib import Path


def test_harness_inventory_status_matches_detailed_description() -> None:
    document = (Path(__file__).resolve().parents[2] / "docs/additions.md").read_text()
    row = next(line for line in document.splitlines() if line.startswith("| 3 |"))
    status = row.split("|")[3]
    description = document.split("### 3. Agent harness pack\n", 1)[1].split("\n### ", 1)[0]
    for text in (status, description):
        assert "on by default" in text
        assert "diagnostics" in text
        assert "find_symbol" in text
