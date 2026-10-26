import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_commits as cc


@pytest.mark.parametrize(
    "message",
    [
        "feat(graph): resolve relative imports",
        "docs: add the approved proposal\n\nA body.\n",
        "fix(demo-repo)!: stop double counting restocks",
        "Merge branch 'graph/imports'",
        "# a comment git strips\nchore: tidy\n",
        "feat(api): " + "x" * 61,
    ],
)
def test_good_messages_pass(message: str) -> None:
    assert cc.message_errors(message) == []


@pytest.mark.parametrize(
    "message",
    ["update stuff", "Feat(graph): upper type", "feat(Graph): upper scope", "feat:x", ""],
)
def test_bad_subjects_fail(message: str) -> None:
    assert cc.message_errors(message)


def test_a_subject_over_72_characters_fails() -> None:
    errors = cc.message_errors("feat(api): " + "x" * 62)
    assert errors == ["subject has 73 characters; the limit is 72"]


def test_lockfiles_generated_files_fixtures_and_binaries_are_not_counted() -> None:
    numstat = "\n".join(
        [
            "10\t5\tbackend/src/oversight/app.py",
            "900\t0\tuv.lock",
            "900\t0\tfrontend/pnpm-lock.yaml",
            "900\t0\tcontracts/generated/contracts.ts",
            "900\t0\tfixtures/events/level1.jsonl",
            "-\t-\tfrontend/public/logo.png",
        ]
    )
    assert cc.counted_lines(numstat) == 15


def test_size_limit_and_its_exception_trailer() -> None:
    assert cc.size_errors(1000, "feat: x") == []
    assert cc.size_errors(1001, "feat: x")
    trailer = "feat: x\n\nCommit-Exception: the bootstrap cannot be split\n"
    assert cc.size_errors(1001, trailer) == []
    assert cc.size_errors(1001, "feat: x\n\nCommit-Exception:\n")


def run(repo: Path, *args: str) -> str:
    return subprocess.run(args, cwd=repo, check=True, capture_output=True, text=True).stdout


def test_git_variables_from_a_hook_do_not_reach_tests() -> None:
    assert not [name for name in os.environ if name.startswith("GIT_")]


def test_pushed_checks_every_new_commit_but_not_merges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run(repo, "git", "init", "-q", "-b", "main")
    run(repo, "git", "config", "user.email", "test@example.invalid")
    run(repo, "git", "config", "user.name", "Test")
    (repo / "a.txt").write_text("a\n")
    run(repo, "git", "add", ".")
    run(repo, "git", "commit", "-q", "--no-verify", "-m", "chore: start")
    base = run(repo, "git", "rev-parse", "HEAD").strip()
    (repo / "big.txt").write_text("line\n" * 1001)
    run(repo, "git", "add", ".")
    run(repo, "git", "commit", "-q", "--no-verify", "-m", "add a big file")
    monkeypatch.chdir(repo)
    errors = cc.check_pushed(base, "HEAD")
    assert len(errors) == 2
    assert "not a Conventional Commit" in errors[0]
    assert "1001 changed lines" in errors[1]
