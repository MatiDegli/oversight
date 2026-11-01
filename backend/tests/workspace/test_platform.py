"""Windows boundaries exercised on Linux with injected CRT and OS inputs."""

import errno
import os
import sys

import pytest

from oversight.workspace import WorkspaceBusy
from oversight.workspace import _platform as platform
from oversight.workspace.metadata_io import DIRECTORY, READ
from oversight.workspace.tree import _DIR_FLAGS


def test_import_flags_pin_posix_and_nt():
    if sys.platform == "win32":
        pytest.skip("POSIX flag golden")
    directory = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    read = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    assert (directory, read, directory) == (DIRECTORY, READ, _DIR_FLAGS)
    assert platform.import_flags(nt=False) == (directory, read, directory)
    assert platform.import_flags(nt=True, binary=0x8000) == (0x8000,) * 3


class CRT:
    LK_NBLCK: int = 2
    LK_UNLCK: int = 0

    def __init__(self, failure=None):
        self.calls = []
        self.failure = failure

    def locking(self, fd, mode, length):
        self.calls.append((fd, mode, length, os.lseek(fd, 0, os.SEEK_CUR)))
        assert not os.get_inheritable(fd)
        if self.failure is not None:
            raise self.failure


def test_nt_lock_seeks_locks_unlocks_and_closes(tmp_path):
    crt = CRT()
    fd = platform.acquire_lock(tmp_path / "server.lock", msvcrt_api=crt)
    os.write(fd, b"nonempty lock")
    platform.release_lock(fd, msvcrt_api=crt)
    assert crt.calls == [(fd, 2, 1, 0), (fd, 0, 1, 0)]
    with pytest.raises(OSError):
        os.fstat(fd)


@pytest.mark.parametrize("number", [errno.EACCES, errno.EDEADLK, errno.EINVAL, errno.EIO])
def test_nt_lock_maps_only_busy_and_closes(tmp_path, number):
    error = OSError(number, "CRT failure")
    crt = CRT(error)
    with pytest.raises(
        WorkspaceBusy if number in (errno.EACCES, errno.EDEADLK) else OSError
    ) as exc:
        platform.acquire_lock(tmp_path / "server.lock", msvcrt_api=crt)
    if number not in (errno.EACCES, errno.EDEADLK):
        assert exc.value is error
    with pytest.raises(OSError):
        os.fstat(crt.calls[0][0])


def test_nt_unlock_closes_even_on_error(tmp_path):
    crt = CRT()
    fd = platform.acquire_lock(tmp_path / "server.lock", msvcrt_api=crt)
    crt.failure = OSError(errno.EIO, "unlock failed")
    with pytest.raises(OSError, match="unlock failed"):
        platform.release_lock(fd, msvcrt_api=crt)
    with pytest.raises(OSError):
        os.fstat(fd)
