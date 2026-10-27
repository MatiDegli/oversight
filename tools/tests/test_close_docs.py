"""Close documentation must not report results whose records are absent from the repo.

These regressions cover the withdrawn level 2 claims. Reintroducing results requires
checking in their supporting records and updating these checks to validate that evidence.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read_section(filename: str, heading: str) -> str:
    text = (ROOT / filename).read_text()
    return text.split(heading, 1)[1].split("\n## ", 1)[0]


def assert_no_unverified_results(text: str) -> None:
    normalized = " ".join(text.split())
    patterns = (
        r"15:15|f5ea871",
        r"3/3|30 s(?:/run| per run)",
        r"`make e2e`[^.]*\b(?:passed|2 passed)\b",
        r"(?:three|3) real two-agent runs",
        r"US\$0\.016",
        r"both slots used Muse Spark|A and B both used Muse Spark",
    )
    unsupported = [pattern for pattern in patterns if re.search(pattern, normalized, re.I)]
    assert not unsupported, f"Close claims need repository evidence: {unsupported}"


def test_architecture_does_not_claim_observed_close_run_models() -> None:
    assert_no_unverified_results((ROOT / "ARCHITECTURE.md").read_text())


def test_readme_does_not_claim_unrecorded_close_results() -> None:
    assert_no_unverified_results(read_section("README.md", "## Built during the hackathon"))


def test_decision_does_not_claim_unrecorded_close_results() -> None:
    assert_no_unverified_results(read_section("DECISIONS.md", "## Level 2 closed on Tue Oct 27"))
