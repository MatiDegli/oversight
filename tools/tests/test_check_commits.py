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


def test_a_subject_starting_with_merge_gets_no_exemption() -> None:
    assert cc.message_errors("Merge basura")
    assert cc.message_errors("Merge branch 'graph/imports'")


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


@pytest.mark.parametrize(
    "message",
    [
        "feat: x\n\nWhy.\n\nCommit-Exception: the bootstrap cannot be split\n",
        "feat: x\n\nCommit-Exception: why\nCo-Authored-By: A <a@example.invalid>\n",
        "feat: x\n\nCommit-Exception: why\n  and a second line of the reason\n",
        "feat: x\n\nCommit-Exception: why\n# Please enter the commit message\n",
    ],
)
def test_the_exception_is_accepted_as_a_trailer_in_the_last_paragraph(message: str) -> None:
    assert cc.size_errors(1001, message) == []


@pytest.mark.parametrize(
    "message",
    [
        "feat: x\n\nCommit-Exception: why\n\nA closing paragraph.\n",
        "feat: x\n\nCommit-Exception: why\nbut prose follows\n",
        "feat: x\nCommit-Exception: why\n",
        "feat: x\n\n# Commit-Exception: why\n",
        "Commit-Exception: why\n",
        "feat: x\n\nCommit-Exception:\n",
    ],
)
def test_the_exception_is_refused_anywhere_else(message: str) -> None:
    assert cc.size_errors(1001, message)


def run(repo: Path, *args: str) -> str:
    return subprocess.run(args, cwd=repo, check=True, capture_output=True, text=True).stdout


def test_git_variables_from_a_hook_do_not_reach_tests() -> None:
    assert not [name for name in os.environ if name.startswith("GIT_")]


def init_repo(repo: Path) -> None:
    repo.mkdir()
    run(repo, "git", "init", "-q", "-b", "main")
    run(repo, "git", "config", "user.email", "test@example.invalid")
    run(repo, "git", "config", "user.name", "Test")


def commit(repo: Path, name: str, subject: str, content: str = "x\n") -> str:
    (repo / name).write_text(content)
    run(repo, "git", "add", ".")
    run(repo, "git", "commit", "-q", "--no-verify", "-m", subject)
    return run(repo, "git", "rev-parse", "HEAD").strip()


def test_pushed_checks_every_new_commit_but_not_merges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    base = commit(repo, "a.txt", "chore: start")
    big = commit(repo, "big.txt", "add a big file", "line\n" * 1001)
    run(repo, "git", "checkout", "-q", "-b", "side", base)
    side = commit(repo, "side.txt", "feat: side")
    run(repo, "git", "checkout", "-q", "main")
    run(repo, "git", "merge", "-q", "--no-ff", "--no-verify", "-m", "Merge anything", side)
    monkeypatch.chdir(repo)
    errors = cc.check_pushed(base, "HEAD")
    assert len(errors) == 2
    assert all(error.startswith(big[:10]) for error in errors)
    assert "not a Conventional Commit" in errors[0]
    assert "1001 changed lines" in errors[1]


def test_a_first_push_checks_exactly_its_new_commits_without_origin_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    root = commit(repo, "a.txt", "start")
    run(repo, "git", "checkout", "-q", "-b", "feature")
    tip = commit(repo, "b.txt", "wip")
    run(repo, "git", "checkout", "-q", "-b", "scratch", root)
    commit(repo, "c.txt", "wip elsewhere")  # a local branch nobody is pushing
    run(repo, "git", "checkout", "-q", "main")
    monkeypatch.chdir(repo)
    # No remote at all: the root and the feature commit are new, and nothing else is checked.
    for from_ref in ("", cc.ZERO):
        errors = cc.check_pushed(from_ref, "feature")
        assert sorted(error[:10] for error in errors) == sorted([root[:10], tip[:10]])
    # With a remote-tracking ref, only what it lacks is checked: the root is already published.
    run(repo, "git", "update-ref", "refs/remotes/origin/other", root)
    errors = cc.check_pushed("", "feature")
    assert [error[:10] for error in errors] == [tip[:10]]


def test_the_pushed_range_is_checked_and_nothing_before_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    commit(repo, "a.txt", "chore: start")
    old = commit(repo, "old.txt", "already published, badly worded")
    bad = commit(repo, "bad.txt", "wip")
    commit(repo, "good.txt", "feat: good")
    monkeypatch.chdir(repo)
    errors = cc.check_pushed(old, "HEAD")
    assert [error[:10] for error in errors] == [bad[:10]]
    assert "not a Conventional Commit" in errors[0]


def test_a_bad_commit_on_a_branch_nobody_pushes_does_not_block_a_good_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    base = commit(repo, "a.txt", "chore: start")
    run(repo, "git", "update-ref", "refs/remotes/origin/main", base)
    run(repo, "git", "checkout", "-q", "-b", "wip")
    commit(repo, "wip.txt", "wip, not for pushing")
    run(repo, "git", "checkout", "-q", "-b", "good", base)
    commit(repo, "good.txt", "feat: good")
    monkeypatch.chdir(repo)
    assert cc.check_pushed(base, "good") == []
    assert cc.check_pushed("", "good") == []


def test_pushed_main_reads_the_pushed_ref_when_pre_commit_gives_no_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    base = commit(repo, "a.txt", "chore: start")
    run(repo, "git", "update-ref", "refs/remotes/origin/main", base)
    run(repo, "git", "checkout", "-q", "--detach")
    tagged = commit(repo, "t.txt", "wip tagged")
    run(repo, "git", "tag", "v1")
    run(repo, "git", "checkout", "-q", "main")  # HEAD and every branch lack the tagged commit
    monkeypatch.chdir(repo)
    for name in ("PRE_COMMIT_FROM_REF", "PRE_COMMIT_TO_REF"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PRE_COMMIT_LOCAL_BRANCH", "refs/tags/v1")
    assert cc.main(["pushed"]) == 1
    assert tagged[:10] in capsys.readouterr().err


COMMIT_MSG_HOOK = '#!/bin/sh\nexec "{python}" "{script}" message "$1"\n'


def repo_with_the_message_hook(tmp_path: Path) -> Path:
    repo = tmp_path / "hooked"
    init_repo(repo)
    hook = repo / ".git" / "hooks" / "commit-msg"
    hook.write_text(COMMIT_MSG_HOOK.format(python=sys.executable, script=Path(cc.__file__)))
    hook.chmod(0o755)
    commit(repo, "a.txt", "chore: start")
    return repo


def try_commit(repo: Path, *paragraphs: str) -> subprocess.CompletedProcess[str]:
    args = ["git", "commit", "-q"]
    for paragraph in paragraphs:
        args += ["-m", paragraph]
    return subprocess.run(args, cwd=repo, capture_output=True, text=True)


def test_commit_msg_skips_a_real_merge_but_not_a_commit_that_says_merge(tmp_path: Path) -> None:
    repo = repo_with_the_message_hook(tmp_path)
    run(repo, "git", "checkout", "-q", "-b", "side")
    (repo / "side.txt").write_text("s\n")
    run(repo, "git", "add", ".")
    assert try_commit(repo, "feat: side").returncode == 0
    run(repo, "git", "checkout", "-q", "main")
    (repo / "main.txt").write_text("m\n")
    run(repo, "git", "add", ".")
    assert try_commit(repo, "feat: main").returncode == 0
    # A real merge keeps its free subject: MERGE_HEAD exists while its message is checked.
    merged = subprocess.run(
        ["git", "merge", "--no-ff", "-m", "Combine the side work", "side"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert merged.returncode == 0, merged.stderr
    # The same words in an ordinary commit get no exemption.
    (repo / "x.txt").write_text("x\n")
    run(repo, "git", "add", ".")
    fake = try_commit(repo, "Merge basura")
    assert fake.returncode != 0
    assert "not a Conventional Commit" in fake.stderr


def test_commit_msg_accepts_the_exception_only_as_a_trailing_trailer(tmp_path: Path) -> None:
    repo = repo_with_the_message_hook(tmp_path)
    (repo / "big.txt").write_text("line\n" * 1001)
    run(repo, "git", "add", ".")
    assert "1001 changed lines" in try_commit(repo, "feat: big").stderr
    buried = try_commit(repo, "feat: big", "Commit-Exception: why", "A closing paragraph.")
    assert "1001 changed lines" in buried.stderr
    assert try_commit(repo, "feat: big", "Commit-Exception: cannot be split").returncode == 0
