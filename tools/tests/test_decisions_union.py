"""Exercise the repository's union attribute using only temporary Git repositories."""

import os
import subprocess
from pathlib import Path

import pytest

BASE = "# Decisions\n\n## Existing decision\n\nUse the agreed default.\n"
ATTRIBUTES = Path(__file__).resolve().parents[2] / ".gitattributes"


def git(repo: Path, *args: str) -> str:
    # Ignore host configuration and hook-exported paths; every Git command stays in tmp_path.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env |= {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    result = subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def commit_decisions(repo: Path, content: str, subject: str) -> None:
    (repo / "DECISIONS.md").write_text(content, encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", subject)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q", "-b", "main", "--template=")
    (tmp_path / ".gitattributes").write_text(ATTRIBUTES.read_text(), encoding="utf-8")
    commit_decisions(tmp_path, BASE, "chore: add base decision")
    return tmp_path


def merge_branches(repo: Path, method: str, left: str, right: str) -> str:
    git(repo, "checkout", "-q", "-b", "front-a", "main")
    commit_decisions(repo, left, "docs: record front a decision")
    git(repo, "checkout", "-q", "-b", "front-b", "main")
    commit_decisions(repo, right, "docs: record front b decision")
    git(repo, "checkout", "-q", "front-a")
    if method == "merge-tree":
        tree = git(repo, "merge-tree", "--write-tree", "front-a", "front-b").strip()
        return git(repo, "show", f"{tree}:DECISIONS.md")
    git(repo, "merge", "--no-ff", "-m", "chore: merge front b", "front-b")
    assert git(repo, "ls-files", "--unmerged") == ""
    return (repo / "DECISIONS.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("method", ["merge", "merge-tree"])
def test_parallel_appends_merge_without_conflict(repo: Path, method: str) -> None:
    entry_a = "\n## Front A\n\nKeep the first appended decision.\n"
    entry_b = "\n## Front B\n\nKeep the second appended decision.\n"
    merged = merge_branches(repo, method, BASE + entry_a, BASE + entry_b)
    assert merged.startswith(BASE)
    assert merged.count(entry_a) == 1
    assert merged.count(entry_b) == 1
    assert "<<<<<<<" not in merged


@pytest.mark.parametrize("method", ["merge", "merge-tree"])
def test_same_line_edits_keep_both_versions_for_review(repo: Path, method: str) -> None:
    """Union reports success even here: reviewers must catch edits to existing decisions."""
    old = "Use the agreed default.\n"
    left = "Use front A's default.\n"
    right = "Use front B's default.\n"
    merged = merge_branches(repo, method, BASE.replace(old, left), BASE.replace(old, right))
    assert old not in merged
    assert merged.count(left) == 1
    assert merged.count(right) == 1
    assert "<<<<<<<" not in merged
