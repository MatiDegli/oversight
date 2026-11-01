"""Typed OS boundaries for runtime ownership and native Windows process lifetime."""

import errno
import os
import sys
from pathlib import Path
from typing import Protocol

from oversight.workspace.errors import WorkspaceBusy

if sys.platform != "win32":
    import fcntl


class Locking(Protocol):
    @property
    def LK_NBLCK(self) -> int: ...

    @property
    def LK_UNLCK(self) -> int: ...

    def locking(self, fd: int, mode: int, length: int, /) -> object: ...


def import_flags(*, nt: bool, binary: int = 0) -> tuple[int, int, int]:
    """DIRECTORY, READ and tree's directory flags; POSIX expressions stay exact."""
    if nt:
        directory = os.O_RDONLY | binary
        read = os.O_RDONLY | binary
        return directory, read, directory
    if sys.platform == "win32":
        raise OSError("POSIX flags are unavailable on Windows")
    directory = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    read = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    tree = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    return directory, read, tree


def _locking() -> Locking:
    if sys.platform == "win32":
        import msvcrt

        return msvcrt
    raise OSError("Windows locking is unavailable")


def acquire_lock(path: Path, *, msvcrt_api: Locking | None = None) -> int:
    nt = sys.platform == "win32" or msvcrt_api is not None
    if sys.platform == "win32":
        flags = os.O_RDWR | os.O_CREAT | os.O_BINARY
    elif nt:
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
    else:
        flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
    fd = os.open(path, flags, 0o600)
    try:
        if nt:
            os.set_inheritable(fd, False)
            os.lseek(fd, 0, os.SEEK_SET)
            api = msvcrt_api or _locking()
            try:
                api.locking(fd, api.LK_NBLCK, 1)
            except OSError as error:
                if error.errno not in (errno.EACCES, errno.EDEADLK):
                    raise
                raise WorkspaceBusy(
                    "another Oversight server is using this runtime directory"
                ) from None
        elif sys.platform != "win32":
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise WorkspaceBusy(
                    "another Oversight server is using this runtime directory"
                ) from None
    except BaseException:
        os.close(fd)
        raise
    return fd


def release_lock(fd: int, *, msvcrt_api: Locking | None = None) -> None:
    try:
        if sys.platform == "win32" or msvcrt_api is not None:
            os.lseek(fd, 0, os.SEEK_SET)
            api = msvcrt_api or _locking()
            api.locking(fd, api.LK_UNLCK, 1)
    finally:
        os.close(fd)
