"""Keep the review packet's required evidence available to its reviewer."""

import re
from pathlib import Path


def test_git_truncated_handoff_contains_required_evidence() -> None:
    report = Path(__file__).resolve().parents[2] / "docs/reviews/git-truncated.md"
    assert report.is_file(), "The truncated Git review needs its Spanish hand-back"
    text = report.read_text(encoding="utf-8")
    assert len(text.splitlines()) <= 8
    assert re.search(r"Commits:.*`[0-9a-f]{7,40}`", text)
    assert "BLOCKING REVIEW.diff:1" in text
    for caller in (
        "Tree.head/commit_all",
        "Workspace.reset/create_worktree/_remove_worktree",
        "ChangeContext.git",
        "lead_probe.demo_graph",
        "yes_no/merge-base",
    ):
        assert caller in text, f"Missing caller review: {caller}"
    assert re.search(r"Pruebas: [1-9]\d* regresiones", text)
    assert "sin pipes" in text
    assert re.search(r"make check`: exit 0; [1-9]\d* pytest.*[1-9]\d* Vitest", text)
    assert re.search(r"make demo-check`: exit 0; 3/3", text)
    assert "core.py: 503" in text
    assert "fuera de alcance" in text
    assert "CONTESTED: none" in text
