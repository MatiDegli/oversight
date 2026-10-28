"""Line counts and file statuses for a change's files, and git's name-status output."""

import asyncio
from collections import OrderedDict
from difflib import SequenceMatcher
from hashlib import sha256
from threading import Lock

from oversight_contracts.common import FileStatus

_CACHE_LIMIT = 128
_counts: OrderedDict[tuple[bytes | None, bytes | None], tuple[int, int]] = OrderedDict()
_cache_lock = Lock()


async def count_lines_async(original: str | None, modified: str | None) -> tuple[int, int]:
    """Keep hashing and uncached matching off the event loop, including on cache misses."""
    return await asyncio.to_thread(count_lines, original, modified)


def count_lines(original: str | None, modified: str | None) -> tuple[int, int]:
    """Cached counts for worker-thread callers; retain hashes and counts, never file contents."""
    key = (_digest(original), _digest(modified))
    with _cache_lock:
        cached = _counts.get(key)
        if cached is not None:
            _counts.move_to_end(key)
            return cached
    result = _count_lines(original, modified)
    with _cache_lock:
        _counts[key] = result
        _counts.move_to_end(key)
        if len(_counts) > _CACHE_LIMIT:
            _counts.popitem(last=False)
    return result


def _digest(content: str | None) -> bytes | None:
    if content is None:
        return None
    return sha256(content.encode("utf-8", errors="surrogatepass")).digest()


def _count_lines(original: str | None, modified: str | None) -> tuple[int, int]:
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
