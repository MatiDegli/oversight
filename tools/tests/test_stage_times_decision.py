"""Preserve the typing-only exception in the recorded stage contract approval."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_stage_times_approval_quotes_the_typing_only_ruling() -> None:
    decisions = (ROOT / "DECISIONS.md").read_text(encoding="utf-8")
    entry = decisions.split("## 2026-10-31 — Time per stage for each change\n", 1)[1]
    entry = " ".join(entry.split("\n## ", 1)[0].split())
    assert (
        '"Candidate 2 contract (`ChangeView.stages`, module stages.py): APPROVED, '
        "text exactly as in packet.md, except one typing-only spelling ruled "
        "2026-11-01 00:33Z: `test_phases` uses `default_factory=list[SuitePhaseTime]` "
        "(the repo's pattern, commands.py:171, review.py:264) so Pyright passes; "
        'the generated schema must not change."'
    ) in entry
