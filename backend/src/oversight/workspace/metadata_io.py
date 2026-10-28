"""Bounded descriptor-relative metadata I/O; no Git and no symlink traversal."""

import os
import stat
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from oversight.workspace.integrity import IntegrityError, remaining

MAX_FILE = 4 * 1024 * 1024
MAX_INDEX = 64 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024
MAX_ENTRIES = 16384
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


@dataclass(frozen=True)
class Entry:
    mode: int
    data: bytes | None  # None means a directory, not an absent file.


@dataclass
class Budget:
    deadline: float
    size: int = 0
    entries: int = 0

    def charge(self, size: int) -> None:
        remaining(self.deadline)
        self.size += size
        self.entries += 1
        if self.size > MAX_TOTAL or self.entries > MAX_ENTRIES:
            raise IntegrityError("metadata snapshot exceeds its budget", ".git")


@contextmanager
def directory(path: Path, deadline: float) -> Generator[int]:
    """Open every parent from / with O_NOFOLLOW, including the final directory."""
    if not path.is_absolute() or ".." in path.parts:
        raise IntegrityError("noncanonical metadata path", str(path))
    fd = os.open("/", DIRECTORY)
    try:
        for part in path.parts[1:]:
            remaining(deadline)
            child = os.open(part, DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def read_at(
    fd: int, name: str, path: Path, budget: Budget, *, kind: str = "Git metadata"
) -> Entry | None:
    remaining(budget.deadline)
    try:
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if stat.S_ISDIR(info.st_mode):
        budget.charge(0)
        return Entry(stat.S_IMODE(info.st_mode), None)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise IntegrityError(f"nonregular or linked {kind}", str(path))
    limit = MAX_INDEX if name == "index" and kind == "Git metadata" else MAX_FILE
    if info.st_size > limit:
        raise IntegrityError(f"oversized {kind}: read limit exceeded", str(path))
    with os.fdopen(os.open(name, READ, dir_fd=fd), "rb") as handle:
        opened = os.fstat(handle.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_mode, opened.st_nlink) != (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            1,
        ):
            raise IntegrityError(f"{kind} changed during open", str(path))
        chunks: list[bytes] = []
        size = 0
        while True:
            remaining(budget.deadline)
            chunk = handle.read(min(65536, limit + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise IntegrityError(f"oversized {kind}: read limit exceeded", str(path))
            chunks.append(chunk)
        after = os.fstat(handle.fileno())
        if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        ):
            raise IntegrityError(f"{kind} changed during read", str(path))
    budget.charge(size)
    return Entry(stat.S_IMODE(info.st_mode), b"".join(chunks))


def read_entry(path: Path, budget: Budget) -> Entry | None:
    try:
        with directory(path.parent, budget.deadline) as fd:
            return read_at(fd, path.name, path, budget)
    except FileNotFoundError:
        return None


def collect(
    path: Path, budget: Budget, entries: dict[Path, Entry], *, skip: frozenset[Path]
) -> None:
    pending = [path]
    while pending:
        current = pending.pop()
        entry = read_entry(current, budget)
        if entry is None:
            continue
        entries[current] = entry
        if entry.data is not None or current in skip:
            continue
        with directory(current, budget.deadline) as fd, os.scandir(fd) as children:
            for child in children:
                remaining(budget.deadline)
                pending.append(current / child.name)
                if budget.entries + len(pending) > MAX_ENTRIES:
                    raise IntegrityError("metadata snapshot exceeds its entry budget", ".git")


def _same(fd: int, path: Path, expected: Entry | None, deadline: float) -> None:
    if read_at(fd, path.name, path, Budget(deadline)) != expected:
        raise IntegrityError("metadata compare-and-swap failed", str(path))


def replace_file(
    path: Path, before: Entry | None, wanted: Entry | None, deadline: float, *, cas: bool
) -> None:
    """Own an exclusive lock for refs; compare bytes again immediately before replacement.

    No existing lock is removed, including on failure. Only our own temporary inode is cleaned.
    The caller sanitizes ref names and desired ref contents before requesting CAS.
    """
    with directory(path.parent, deadline) as fd:
        name = path.name + ".lock" if cas else f".oversight-{uuid4().hex}"
        temp = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        owned = os.fstat(temp)
        try:
            with os.fdopen(temp, "wb") as handle:
                if wanted is not None:
                    assert wanted.data is not None
                    for offset in range(0, len(wanted.data), 65536):
                        remaining(deadline)
                        handle.write(wanted.data[offset : offset + 65536])
                    os.fchmod(handle.fileno(), wanted.mode)
            _same(fd, path, before, deadline)
            if wanted is None:
                os.unlink(path.name, dir_fd=fd)
            else:
                os.replace(name, path.name, src_dir_fd=fd, dst_dir_fd=fd)
            _same(fd, path, wanted, deadline)
        finally:
            try:
                current = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
                    os.unlink(name, dir_fd=fd)
            except FileNotFoundError:
                pass


def make_directory(path: Path, mode: int, deadline: float) -> None:
    with directory(path.parent, deadline) as fd:
        os.mkdir(path.name, mode, dir_fd=fd)
    with directory(path, deadline) as fd:
        os.fchmod(fd, mode)


def remove_directory(path: Path, deadline: float) -> None:
    with directory(path.parent, deadline) as fd:
        os.rmdir(path.name, dir_fd=fd)
