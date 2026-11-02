"""Bounded directory listing; descriptors prevent following replaced symlinks."""

import asyncio
import errno
import hashlib
import os
import stat
import sys
import time
from contextlib import closing
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from oversight.workspace._platform import import_flags
from oversight.workspace.integrity import IntegrityError
from oversight.workspace.locks import offload
from oversight.workspace.metadata_io import nt_guard, nt_name, nt_token
from oversight_contracts.commands import TreeFile

if TYPE_CHECKING:
    from oversight.workspace.core import Tree


class _Scan(Protocol):
    def __next__(self) -> os.DirEntry[str]: ...

    def close(self) -> None: ...


ENTRY_CAP = 2000
HASH_FILE_BYTES = 1024 * 1024
HASH_TOTAL_BYTES = 8 * 1024 * 1024
TREE_TIMEOUT_S = 2.0
_, _READ_FLAGS, _DIR_FLAGS = import_flags(
    nt=sys.platform == "win32", binary=getattr(os, "O_BINARY", 0) if sys.platform == "win32" else 0
)


def _check_time(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise TimeoutError("tree listing exceeded its time bound")


def _hash(parent: int | Path, name: str, budget: list[int], deadline: float) -> str | None:
    try:
        info = None
        if isinstance(parent, Path):
            nt_name(name, working=True)
            info = nt_guard(parent / name, deadline, working=True)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                return None
            fd = os.open(parent / name, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        else:
            flags = _READ_FLAGS
            fd = os.open(name, flags, dir_fd=parent)
        with os.fdopen(fd, "rb", buffering=0) as handle:
            before = os.fstat(handle.fileno())
            if (
                info is not None
                and isinstance(parent, Path)
                and (
                    nt_token(info) != nt_token(before)
                    or nt_token(nt_guard(parent / name, deadline, working=True)) != nt_token(before)
                )
            ):
                return None
            if not stat.S_ISREG(before.st_mode) or before.st_size > min(HASH_FILE_BYTES, budget[0]):
                return None
            digest = hashlib.sha256()
            read = 0
            while True:
                _check_time(deadline)
                # Read no more than either byte budget, even if a file grows during the read.
                size = min(65536, HASH_FILE_BYTES - read, budget[0])
                if size == 0:
                    if read != before.st_size:
                        return None
                    break
                chunk = handle.read(size)
                if not chunk:
                    break
                read += len(chunk)
                budget[0] -= len(chunk)
                digest.update(chunk)
            after = os.fstat(handle.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ) or read != after.st_size:
                return None
            if isinstance(parent, Path) and (
                nt_token(before) != nt_token(after)
                or nt_token(nt_guard(parent / name, deadline, working=True)) != nt_token(after)
            ):
                return None
            return digest.hexdigest()
    except (OSError, IntegrityError):
        return None


def walk_tree(root: Path, deadline: float) -> tuple[list[str], list[TreeFile], bool]:
    """Return sorted unique lists. Only the entry cap sets truncated; timeout fails the read.

    Hash at most 1 MiB per file and 8 MiB in total; incomplete hashes are null. Traversal uses
    an explicit stack, so deep directories cannot exhaust Python's call stack.
    """
    if sys.platform == "win32":
        try:
            return _walk_nt(root, deadline)
        except IntegrityError:
            _check_time(deadline)
            raise
    directories: list[str] = []
    files: list[TreeFile] = []
    budget = [HASH_TOTAL_BYTES]
    # Each frame owns both its descriptor and iterator, including when the cap is reached.
    stack: list[tuple[int, _Scan, str]] = []

    def descend(parent: int | None, name: str, prefix: str) -> None:
        fd = os.open(name, _DIR_FLAGS, dir_fd=parent)
        try:
            stack.append((fd, os.scandir(fd), prefix))
        except BaseException:
            os.close(fd)
            raise

    truncated = False
    try:
        descend(None, str(root), "")
        while stack:
            _check_time(deadline)
            fd, iterator, prefix = stack[-1]
            entry = next(iterator, None)
            if entry is None:
                iterator.close()
                os.close(fd)
                stack.pop()
                continue
            if entry.name == ".git":
                continue
            path = f"{prefix}/{entry.name}" if prefix else entry.name
            try:
                mode = entry.stat(follow_symlinks=False).st_mode
                directory = stat.S_ISDIR(mode)
                if not directory and not stat.S_ISREG(mode):
                    continue
                if len(directories) + len(files) == ENTRY_CAP:
                    truncated = True
                    break
                if directory:
                    try:
                        descend(fd, entry.name, path)
                    except PermissionError:
                        continue  # Skip unreadable subtrees while preserving readable siblings.
                    except OSError as error:
                        if error.errno not in (errno.ELOOP, errno.ENOTDIR):
                            raise
                        # Replaced by a symlink or non-directory after stat; nothing was followed.
                        continue
                    directories.append(path)
                else:
                    files.append(TreeFile(path=path, hash=_hash(fd, entry.name, budget, deadline)))
            except FileNotFoundError:
                continue  # A concurrently removed entry is no longer eligible.
        _check_time(deadline)
    finally:
        for fd, iterator, _ in stack:
            with closing(iterator):
                os.close(fd)
    return sorted(directories), sorted(files, key=lambda file: file.path), truncated


def _walk_nt(root: Path, deadline: float) -> tuple[list[str], list[TreeFile], bool]:
    directories: list[str] = []
    files: list[TreeFile] = []
    budget = [HASH_TOTAL_BYTES]
    stack: list[tuple[Path, _Scan, str]] = []

    def descend(path: Path, prefix: str) -> None:
        _check_time(deadline)
        if not stat.S_ISDIR(nt_guard(path, deadline, working=True).st_mode):
            raise NotADirectoryError(errno.ENOTDIR, "tree directory replaced", str(path))
        stack.append((path, os.scandir(path), prefix))

    truncated = False
    try:
        descend(root, "")
        while stack:
            _check_time(deadline)
            parent, iterator, prefix = stack[-1]
            entry = next(iterator, None)
            if entry is None:
                iterator.close()
                stack.pop()
                continue
            try:
                nt_name(entry.name, working=True)
            except IntegrityError:
                continue
            path = f"{prefix}/{entry.name}" if prefix else entry.name
            try:
                info = nt_guard(parent / entry.name, deadline, working=True)
                directory = stat.S_ISDIR(info.st_mode)
                if not directory and not stat.S_ISREG(info.st_mode):
                    continue
                if len(directories) + len(files) == ENTRY_CAP:
                    truncated = True
                    break
                if directory:
                    descend(parent / entry.name, path)
                    directories.append(path)
                else:
                    files.append(
                        TreeFile(path=path, hash=_hash(parent, entry.name, budget, deadline))
                    )
            except (FileNotFoundError, PermissionError):
                continue
            except OSError as error:
                if error.errno not in (errno.ELOOP, errno.ENOTDIR):
                    raise
        _check_time(deadline)
    finally:
        for _, iterator, _ in stack:
            iterator.close()
    return sorted(directories), sorted(files, key=lambda file: file.path), truncated


async def list_tree(tree: "Tree") -> tuple[list[str], list[TreeFile], bool]:
    """Bound lock acquisition and disk work; join cancelled work before releasing the lock."""
    deadline = time.monotonic() + TREE_TIMEOUT_S
    async with asyncio.timeout(TREE_TIMEOUT_S), tree.section(locked=True):
        return await offload(partial(walk_tree, tree.root, deadline))
