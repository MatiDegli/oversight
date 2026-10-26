"""Check commit messages and sizes (docs/proposal.md, rules 1 and 12).

    check_commits.py message <file>   commit-msg hook: the message and the staged change
    check_commits.py pushed           pre-push hook: every commit being pushed, again

A message is a Conventional Commit whose subject fits in 72 characters. A commit changes at most
1,000 lines, added plus removed, not counting lockfiles, generated contracts and fixtures, unless a
`Commit-Exception: <reason>` trailer says why it cannot be split. Merge commits are exempt.
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
EXCEPTION = re.compile(r"^Commit-Exception: \S", re.MULTILINE)
LOCKFILES = ("uv.lock", "pnpm-lock.yaml")
NOT_COUNTED = ("contracts/generated/", "fixtures/")
ZERO = "0" * 40


def message_errors(message: str) -> list[str]:
    lines = [line for line in message.splitlines() if not line.startswith("#")]
    subject = next((line for line in lines if line.strip()), "")
    if subject.startswith("Merge "):
        return []
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


def size_errors(lines: int, message: str) -> list[str]:
    if lines <= MAX_LINES or EXCEPTION.search(message):
        return []
    return [
        f"{lines} changed lines; the limit is {MAX_LINES} without lockfiles, generated contracts "
        "and fixtures. Split the commit, or add a 'Commit-Exception: <reason>' trailer."
    ]


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def check_message_file(path: str) -> list[str]:
    with open(path, encoding="utf-8") as handle:
        message = handle.read()
    errors = message_errors(message)
    merging = os.path.exists(git("rev-parse", "--git-path", "MERGE_HEAD").strip())
    if not merging:
        numstat = git("diff", "--cached", "--numstat", "--no-renames")
        errors += size_errors(counted_lines(numstat), message)
    return errors


def check_pushed(from_ref: str, to_ref: str) -> list[str]:
    if not from_ref or from_ref == ZERO:
        from_ref = git("merge-base", "origin/main", to_ref).strip()
    errors: list[str] = []
    for sha in git("rev-list", "--no-merges", f"{from_ref}..{to_ref}").split():
        message = git("log", "-1", "--format=%B", sha)
        numstat = git("diff-tree", "--root", "--no-commit-id", "-r", "--numstat", sha)
        found = message_errors(message) + size_errors(counted_lines(numstat), message)
        errors += [f"{sha[:10]}: {error}" for error in found]
    return errors


def main(argv: list[str]) -> int:
    if argv[:1] == ["message"] and len(argv) == 2:
        errors = check_message_file(argv[1])
    elif argv == ["pushed"]:
        to_ref = os.environ.get("PRE_COMMIT_TO_REF", "HEAD")
        errors = check_pushed(os.environ.get("PRE_COMMIT_FROM_REF", ""), to_ref)
    else:
        print(__doc__, file=sys.stderr)
        return 2
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
