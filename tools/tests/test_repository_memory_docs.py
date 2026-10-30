"""Keep repository memory's reviewed scope consistent across its documentation."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_text_pin_loading_includes_possible_calls_without_false_merge_contrast() -> None:
    additions = (ROOT / "docs/additions.md").read_text(encoding="utf-8")
    memory = additions.split("### Repository memory\n", 1)[1].split("\n### ", 1)[0]
    loading = memory.split("Text pins load deterministically,", 1)[1].split("\n\n", 1)[0]
    loading = " ".join(loading.split())
    assert (
        "including possible calls, because for context a false positive costs only tokens"
        in loading
    )
    assert "unlike merging" not in loading
    assert "only exact calls count" not in loading


def test_memory_decision_defers_run_discovered_facts_with_command_evidence() -> None:
    decisions = (ROOT / "DECISIONS.md").read_text(encoding="utf-8")
    memory = decisions.split(
        "## 2026-10-29 — Repository memory is a candidate: only what search cannot find\n", 1
    )[1].split("\n## ", 1)[0]
    memory = " ".join(memory.split())
    assert (
        "Facts from running something wait for a later version, with command output as evidence."
        in memory
    )
