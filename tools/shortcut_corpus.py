"""Scan non-merge Python changes in a read-only Git range; emit findings as JSONL.

Usage: python tools/shortcut_corpus.py REPO --revision HEAD --output /tmp/findings.jsonl
Git show supplies full blob IDs, then one cat-file batch process reads both versions.
Renames are deletion plus addition. Git I/O has timeouts.
This standalone CLI owns no application event loop.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import select
import subprocess
import time
import tokenize
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import TypedDict

from oversight.changes import detect_shortcuts

type Change = tuple[str, str | None, str | None]


class FileChange(TypedDict):
    path: str
    old: str
    new: str


def parse_history(raw: bytes) -> Iterator[tuple[str, list[FileChange]]]:
    """Parse NUL-delimited raw Git output, including unusual filenames."""
    tokens = iter(raw.split(b"\0"))
    commit = ""
    changes: list[FileChange] = []
    for token in tokens:
        token = token.lstrip(b"\n")
        if token == b"COMMIT":
            if commit:
                yield commit, changes
            commit = next(tokens).decode("ascii")
            changes = []
        elif token.startswith(b":"):
            fields = token.split()
            path = next(tokens).decode("utf-8", errors="surrogateescape")
            if path.endswith(".py"):
                changes.append({"path": path, "old": fields[2].decode(), "new": fields[3].decode()})
    if commit:
        yield commit, changes


def git(repo: Path, *args: str) -> bytes:
    with subprocess.Popen(
        ["git", "-C", str(repo), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as proc:
        try:
            out, err = proc.communicate(timeout=120)
        except BaseException:
            proc.kill()
            proc.communicate()
            raise
        if proc.returncode:
            raise RuntimeError(err.decode(errors="replace"))
        return out


class Blobs:
    """One read-only batch reader; the enclosing command has a deadline."""

    def __init__(self, proc: subprocess.Popen[bytes]) -> None:
        self.proc = proc
        self.buffer = bytearray()

    def read(self, sha: str) -> str | None:
        if set(sha) == {"0"}:
            return None
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.proc.stdin.write(sha.encode() + b"\n")
        self.proc.stdin.flush()
        fields = self._take(None).split()
        if len(fields) != 3 or fields[1] != b"blob":
            raise RuntimeError(f"Not a blob: {fields!r}")
        size = int(fields[2])
        data = self._take(size)
        if self._take(1) != b"\n":
            raise RuntimeError("Truncated Git blob")
        try:
            encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
            return data.decode(encoding)
        except (SyntaxError, UnicodeError, LookupError):
            return data.decode("utf-8", errors="replace")

    def _take(self, size: int | None) -> bytes:
        assert self.proc.stdout is not None
        deadline = time.monotonic() + 30
        while True:
            end = self.buffer.find(b"\n") + 1 if size is None else size
            if end > 0 and len(self.buffer) >= end:
                result = bytes(self.buffer[:end])
                del self.buffer[:end]
                return result
            if size == 0:
                return b""
            ready, _, _ = select.select(
                [self.proc.stdout], [], [], max(0, deadline - time.monotonic())
            )
            if not ready:
                raise TimeoutError("Git blob read exceeded 30 seconds")
            data = os.read(self.proc.stdout.fileno(), 65536)
            if not data:
                raise RuntimeError("Truncated Git batch response")
            self.buffer.extend(data)


def changes_for(blobs: Blobs, files: list[FileChange]) -> list[Change]:
    return [(f["path"], blobs.read(f["old"]), blobs.read(f["new"])) for f in files]


def _close(proc: subprocess.Popen[bytes]) -> None:
    proc.kill()
    proc.communicate(timeout=5)


def scan(repo: Path, revision: str, output: Path) -> dict[str, object]:
    repo = repo.resolve()
    commits_raw = git(repo, "rev-list", "--no-merges", "--reverse", revision)
    commits_list = commits_raw.decode("ascii").splitlines()
    # Bound argv length even for long histories; preserve commit order.
    records: list[bytes] = []
    for start in range(0, len(commits_list), 1000):
        records.append(
            git(
                repo,
                "show",
                "--no-walk=unsorted",
                "--format=%x00COMMIT%x00%H",
                "--raw",
                "-z",
                "--no-renames",
                "--no-abbrev",
                "--root",
                *commits_list[start : start + 1000],
            )
        )
    proc = subprocess.Popen(
        ["git", "-C", str(repo), "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    counts: Counter[str] = Counter()
    commits = python_commits = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        blobs = Blobs(proc)
        with output.open("w") as stream:
            for commit, files in parse_history(b"".join(records)):
                commits += 1
                if not files:
                    continue
                python_commits += 1
                for finding in detect_shortcuts(changes_for(blobs, files)):
                    counts[finding.kind] += 1
                    stream.write(
                        json.dumps(
                            {
                                "repo": str(repo),
                                "commit": commit,
                                "path": finding.path,
                                "line": finding.line,
                                "kind": finding.kind,
                                "message": finding.message,
                            },
                            sort_keys=True,
                        )
                        + "\n"
                    )
    finally:
        _close(proc)
    summary: dict[str, object] = {
        "repo": str(repo),
        "revision": revision,
        "commits": commits,
        "python_commits": python_commits,
        "findings": dict(sorted(counts.items())),
    }
    output.with_suffix(".summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", type=Path)
    parser.add_argument("--revision", default="HEAD")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(scan(args.repo, args.revision, args.output), indent=2))


if __name__ == "__main__":
    main()
