"""Bounded descriptor-relative metadata I/O; no Git and no symlink traversal."""

import errno
import os
import stat
import sys
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from uuid import uuid4

from oversight.workspace._platform import import_flags, is_link_like, replace_retry, unlink_retry
from oversight.workspace.integrity import IntegrityError, remaining

MAX_FILE = 4 * 1024 * 1024
MAX_INDEX = 64 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024
MAX_ENTRIES = 16384
DIRECTORY, READ, _ = import_flags(
    nt=sys.platform == "win32", binary=getattr(os, "O_BINARY", 0) if sys.platform == "win32" else 0
)


def nt_name(name: str, *, working: bool = False) -> None:
    """Validate a single enumerated name before any access; Git admin roots are authorized."""
    if (
        not name
        or name in {".", ".."}
        or any(ord(c) < 32 or c in '/\\:~<>"|?*' for c in name)
        or name.endswith((".", " "))
        or PureWindowsPath(name).is_reserved()
        or (working and (name.casefold() == ".git" or name.upper() == ".GIT"))
    ):
        raise IntegrityError("unsafe NT path component", name)


def nt_guard(path: Path, deadline: float, *, working: bool = False) -> os.stat_result:
    """Fresh lstat of every component. Demo race checks, not a filesystem sandbox."""
    if not path.is_absolute() or str(path).startswith(("\\\\", "//")):
        raise IntegrityError("noncanonical NT path", str(path))
    for part in path.parts[1:]:
        nt_name(part, working=working)
    remaining(deadline)
    current = Path(path.anchor)
    info = current.lstat()
    for part in (None, *path.parts[1:]):
        remaining(deadline)
        if part is not None:
            if not stat.S_ISDIR(info.st_mode):
                raise NotADirectoryError(errno.ENOTDIR, "non-directory NT parent", str(current))
            current /= part
            info = current.lstat()
        if is_link_like(info, nt=True):
            raise OSError(errno.ELOOP, "linked or reparse NT path", str(current))
    return info


def nt_token(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


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
def directory(path: Path, deadline: float) -> Generator[int | Path]:
    """Open every parent from / with O_NOFOLLOW, including the final directory."""
    if sys.platform == "win32":
        if not stat.S_ISDIR(nt_guard(path, deadline).st_mode):
            raise NotADirectoryError(errno.ENOTDIR, "non-directory NT path", str(path))
        yield path
        return
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
    fd: int | Path, name: str, path: Path, budget: Budget, *, kind: str = "Git metadata"
) -> Entry | None:
    remaining(budget.deadline)
    if isinstance(fd, Path):
        nt_name(name, working=kind != "Git metadata")
    try:
        info = (
            nt_guard(path, budget.deadline, working=kind != "Git metadata")
            if isinstance(fd, Path)
            else os.stat(name, dir_fd=fd, follow_symlinks=False)
        )
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
    opened_fd = (
        os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        if isinstance(fd, Path)
        else os.open(name, READ, dir_fd=fd)
    )
    with os.fdopen(opened_fd, "rb") as handle:
        opened = os.fstat(handle.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_mode, opened.st_nlink) != (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            1,
        ):
            raise IntegrityError(f"{kind} changed during open", str(path))
        if isinstance(fd, Path) and nt_token(
            nt_guard(path, budget.deadline, working=kind != "Git metadata")
        ) != nt_token(opened):
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
        if isinstance(fd, Path) and (
            size != after.st_size
            or nt_token(after) != nt_token(opened)
            or nt_token(nt_guard(path, budget.deadline, working=kind != "Git metadata"))
            != nt_token(after)
        ):
            raise IntegrityError(f"{kind} changed during read", str(path))
    budget.charge(size)
    return Entry(stat.S_IMODE(info.st_mode), b"".join(chunks))


def read_entry(path: Path, budget: Budget) -> Entry | None:
    if sys.platform == "win32":
        nt_name(path.name)
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


def _same(fd: int | Path, path: Path, expected: Entry | None, deadline: float) -> None:
    if read_at(fd, path.name, path, Budget(deadline)) != expected:
        raise IntegrityError("metadata compare-and-swap failed", str(path))


def replace_file(
    path: Path, before: Entry | None, wanted: Entry | None, deadline: float, *, cas: bool
) -> None:
    """Own an exclusive lock for refs; compare bytes again immediately before replacement.

    No existing lock is removed, including on failure. Only our own temporary inode is cleaned.
    The caller sanitizes ref names and desired ref contents before requesting CAS.
    """
    if sys.platform == "win32":
        _replace_nt(path, before, wanted, deadline, cas=cas)
        return
    with directory(path.parent, deadline) as fd:
        assert isinstance(fd, int)
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
    if sys.platform == "win32":
        nt_name(path.name)
        with directory(path.parent, deadline):
            os.mkdir(path, mode)
        with directory(path, deadline):
            pass
        return
    with directory(path.parent, deadline) as fd:
        assert isinstance(fd, int)
        os.mkdir(path.name, mode, dir_fd=fd)
    with directory(path, deadline) as fd:
        assert isinstance(fd, int)
        os.fchmod(fd, mode)


def remove_directory(path: Path, deadline: float) -> None:
    if sys.platform == "win32":
        with directory(path, deadline):
            os.rmdir(path)
        return
    with directory(path.parent, deadline) as fd:
        assert isinstance(fd, int)
        os.rmdir(path.name, dir_fd=fd)


def _replace_nt(
    path: Path, before: Entry | None, wanted: Entry | None, deadline: float, *, cas: bool
) -> None:
    nt_name(path.name)
    with directory(path.parent, deadline) as parent:
        assert isinstance(parent, Path)
        temp_path = parent / (path.name + ".lock" if cas else f".oversight-{uuid4().hex}")
        temp = os.open(
            temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600
        )
        owned = os.fstat(temp)
        try:
            with os.fdopen(temp, "wb") as handle:
                if wanted is not None:
                    assert wanted.data is not None
                    for offset in range(0, len(wanted.data), 65536):
                        remaining(deadline)
                        handle.write(wanted.data[offset : offset + 65536])
            _same(parent, path, before, deadline)
            current = nt_guard(temp_path, deadline)
            if (current.st_dev, current.st_ino, current.st_nlink) != (
                owned.st_dev,
                owned.st_ino,
                1,
            ):
                raise IntegrityError("metadata temporary identity changed", str(path))
            if wanted is None:
                unlink_retry(path, nt=True)
            else:
                os.chmod(temp_path, wanted.mode)
                replace_retry(temp_path, path, nt=True)
            _same(parent, path, wanted, deadline)
        finally:
            try:
                current = nt_guard(temp_path, float("inf"))
                if (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
                    unlink_retry(temp_path, nt=True)
            except FileNotFoundError:
                pass
