"""Line counts and file statuses for a change's files, and git's name-status output."""

from difflib import SequenceMatcher

from oversight_contracts.common import FileStatus


def count_lines(original: str | None, modified: str | None) -> tuple[int, int]:
    """Lines added and removed from `original` to `modified`; `None` is a missing file."""
    old = original.splitlines() if original else []
    new = modified.splitlines() if modified else []
    added = removed = 0
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return added, removed


def file_status(original: str | None, modified: str | None) -> FileStatus | None:
    """Added, deleted or modified; `None` when the file exists in neither version."""
    if original is None:
        return None if modified is None else "added"
    return "deleted" if modified is None else "modified"


def parse_name_status(output: str) -> dict[str, FileStatus]:
    """`git diff --name-status --no-renames -z`: a status letter, then a path, each NUL-ended."""
    fields = output.split("\0")
    found: dict[str, FileStatus] = {}
    for letter, path in zip(fields[0::2], fields[1::2], strict=False):
        if not letter or not path:
            continue
        found[path] = "added" if letter == "A" else "deleted" if letter == "D" else "modified"
    return found
