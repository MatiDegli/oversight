"""Keep the approved Settings target alongside the supported editor behavior."""

from oversight.settings import REPO_ROOT


def test_settings_design_retains_approved_controls_and_save_rules() -> None:
    design = (REPO_ROOT / "DESIGN.md").read_text()
    section = design.split("### Settings tab\n", 1)[1].split("\n### ", 1)[0]
    section = " ".join(section.split())
    for target in (
        "threshold bar showing spent",
        "warnings at 50% and 80%",
        "at or above the brake",
        "Steps per task is a minus/value/plus stepper",
        "Change size and cone limits",
        "**Protected areas:**",
        "one neutral card per offered model",
        "**Real settings only:**",
        "Keys stay in .env",
        "configured share, 90% by default",
        "saved file's parsed state",
        "Restart to apply: File > Reload settings.",
        "Restart the server to apply.",
    ):
        assert target in section
    assert "A left navigation lists" not in section
    assert "below 768px" in design.split("**Decisions chip:**", 1)[1].split("- **Left rail**", 1)[0]


def test_settings_decisions_have_one_blank_line_between_entries() -> None:
    decisions = (REPO_ROOT / "DECISIONS.md").read_text()
    for heading in (
        "The person sets the spending brake's share",
        "Settings review: explicit clears and local save feedback",
    ):
        preceding = decisions.split(f"## 2026-11-04 — {heading}", 1)[0]
        assert preceding.endswith("\n\n")
        assert not preceding.endswith("\n\n\n")
