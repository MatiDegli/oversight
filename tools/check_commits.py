"""Check commit messages and sizes (docs/proposal.md, rules 1 and 12).

    check_commits.py message <file>   commit-msg hook: the message and the staged change
    check_commits.py pushed           pre-push hook: every commit being pushed, again

A message is a Conventional Commit whose subject fits in 72 characters. A commit changes at most
1,000 lines, added plus removed, not counting lockfiles, generated contracts and fixtures, unless a
`Commit-Exception: <reason>` trailer, in the message's last paragraph, says why it cannot be split.

Merges are exempt, by state and not by text: commit-msg skips both checks only while a merge is in
progress (MERGE_HEAD exists), and the pre-push check skips commits with several parents.

pre-commit runs a pre-push hook once per push, with one range: the first pushed ref that has new
commits (see hook_impl.py in pre-commit). A push of several refs would publish the rest unchecked,
so `pushed` checks that range and also every local branch commit that no remote has.
"""

import os
import re
import subprocess
import sys

MAX_SUBJECT = 72
MAX_LINES = 1000
SUBJECT = re.compile(
    r"^(build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test)"
    r"(\([a-z0-9][a-z0-9-]*\))?!?: \S"
)
TRAILER = re.compile(r"^[A-Za-z][A-Za-z0-9-]*: \S")
EXCEPTION = re.compile(r"^Commit-Exception: \S")
LOCKFILES = ("uv.lock", "pnpm-lock.yaml")
NOT_COUNTED = ("contracts/generated/", "fixtures/")
ZERO = "0" * 40


def message_errors(message: str) -> list[str]:
    lines = [line for line in message.splitlines() if not line.startswith("#")]
    subject = next((line for line in lines if line.strip()), "")
    errors: list[str] = []
    if not SUBJECT.match(subject):
        errors.append(f"subject is not a Conventional Commit, like 'feat(graph): ...': {subject!r}")
    if len(subject) > MAX_SUBJECT:
        errors.append(f"subject has {len(subject)} characters; the limit is {MAX_SUBJECT}")
    return errors


def counted_lines(numstat: str) -> int:
    """Sum added and removed lines from `git diff --numstat`; binary files count as zero."""
    total = 0
    for row in numstat.splitlines():
        added, removed, path = row.split("\t", 2)
        if path.rsplit("/", 1)[-1] in LOCKFILES or path.startswith(NOT_COUNTED) or added == "-":
            continue
        total += int(added) + int(removed)
    return total


def has_exception(message: str) -> bool:
    """True when the last paragraph, not the subject's, is trailers and one is Commit-Exception."""
    lines = [line.rstrip() for line in message.splitlines() if not line.startswith("#")]
    paragraphs = re.split(r"\n[ \t]*\n", "\n".join(lines).strip())
    if len(paragraphs) < 2:
        return False
    trailers = paragraphs[-1].splitlines()
    if not all(
        TRAILER.match(line) or (i > 0 and line[:1].isspace()) for i, line in enumerate(trailers)
    ):
        return False
    return any(EXCEPTION.match(line) for line in trailers)


def size_errors(lines: int, message: str) -> list[str]:
    if lines <= MAX_LINES or has_exception(message):
        return []
    return [
        f"{lines} changed lines; the limit is {MAX_LINES} without lockfiles, generated contracts "
        "and fixtures. Split the commit, or add a 'Commit-Exception: <reason>' trailer."
    ]


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def check_message_file(path: str) -> list[str]:
    if os.path.exists(git("rev-parse", "--git-path", "MERGE_HEAD").strip()):
        return []
    with open(path, encoding="utf-8") as handle:
        message = handle.read()
    numstat = git("diff", "--cached", "--numstat", "--no-renames")
    return message_errors(message) + size_errors(counted_lines(numstat), message)


def pushed_commits(from_ref: str, to_ref: str) -> list[str]:
    """Non-merge commits this push may publish: its range, and every one no remote has.

    Without a usable FROM ref (a first push), the range is what `to_ref` has and no remote-tracking
    ref does, so it needs no origin/main. The second part covers the refs of a multi-ref push that
    pre-commit never reports; it also covers local branches that are not being pushed.
    """
    ranges = [[to_ref, "--branches", "--not", "--remotes"]]
    if from_ref and from_ref != ZERO:
        ranges.append([f"{from_ref}..{to_ref}"])
    shas = [sha for r in ranges for sha in git("rev-list", "--no-merges", *r).split()]
    return list(dict.fromkeys(shas))


def check_pushed(from_ref: str, to_ref: str) -> list[str]:
    errors: list[str] = []
    for sha in pushed_commits(from_ref, to_ref):
        message = git("log", "-1", "--format=%B", sha)
        numstat = git("diff-tree", "--root", "--no-commit-id", "-r", "--numstat", sha)
        found = message_errors(message) + size_errors(counted_lines(numstat), message)
        errors += [f"{sha[:10]}: {error}" for error in found]
    return errors


def main(argv: list[str]) -> int:
    if argv[:1] == ["message"] and len(argv) == 2:
        errors = check_message_file(argv[1])
    elif argv == ["pushed"]:
        # A push of a whole history has no FROM and TO refs, only the pushed branch.
        to_ref = os.environ.get("PRE_COMMIT_TO_REF") or os.environ.get("PRE_COMMIT_LOCAL_BRANCH")
        errors = check_pushed(os.environ.get("PRE_COMMIT_FROM_REF", ""), to_ref or "HEAD")
    else:
        print(__doc__, file=sys.stderr)
        return 2
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
