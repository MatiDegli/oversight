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


@pytest.mark.parametrize("nt", [False, True])
def test_link_classification_and_posix_no_stat(nt):
    import stat
    from types import SimpleNamespace
    from typing import cast
    from unittest.mock import Mock

    child = SimpleNamespace(
        is_symlink=Mock(return_value=False),
        stat=Mock(return_value=SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)),
    )
    assert platform.is_link_like(cast(os.DirEntry[str], child), nt=nt) == nt
    assert child.stat.call_count == int(nt)
    for mode, attributes in [(stat.S_IFLNK, 0), (stat.S_IFDIR, 0x400)]:
        assert platform.is_link_like(
            cast(os.stat_result, SimpleNamespace(st_mode=mode, st_file_attributes=attributes)),
            nt=True,
        )


@pytest.mark.parametrize(
    "number,nt,retries", [(5, True, 7), (32, True, 7), (33, True, 7), (5, False, 1), (2, True, 1)]
)
def test_retry_exhaustion_preserves_first_error(monkeypatch, number, nt, retries):
    from unittest.mock import Mock

    first = PermissionError("first failure")
    first.__dict__["winerror"] = number
    errors = [first] + [PermissionError("later") for _ in range(6)]
    for error in errors:
        error.__dict__["winerror"] = number
    operation = Mock(side_effect=errors)
    sleep = Mock()
    import time

    monkeypatch.setattr(time, "sleep", sleep)
    with pytest.raises(OSError) as exc:
        platform.retry_nt(operation, nt=nt)
    assert exc.value is first and operation.call_count == retries
    assert sum(call.args[0] for call in sleep.call_args_list) <= 1
