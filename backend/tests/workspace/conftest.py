"""Shared helpers for the workspace tests: the event loop backend, FIFO gates, process checks."""

import asyncio
import ctypes
import errno
import os
import select
from collections.abc import Callable
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


class Gate:
    """A one-shot handshake between a test and a child process, over a FIFO. The child blocks in
    `child_wait` until the test calls `open_gate`; the child's `child_signal` ends `wait_signal`."""

    def __init__(self, directory: Path, name: str) -> None:
        self.go = directory / f"{name}.go"
        self.signal = directory / f"{name}.signal"
        os.mkfifo(self.go)
        os.mkfifo(self.signal)

    @property
    def child_wait(self) -> str:
        return f"open({str(self.go)!r}).read()"

    @property
    def child_signal(self) -> str:
        return f"open({str(self.signal)!r}, 'w').write(str(os.getpid()))"

    async def open_gate(self) -> None:
        def write() -> None:
            with open(self.go, "w") as handle:
                handle.write("go")

        await asyncio.to_thread(write)

    async def wait_signal(self) -> str:
        def read() -> str:
            with open(self.signal) as handle:
                return handle.read()

        return await asyncio.to_thread(read)


@pytest.fixture
def gate_factory(tmp_path: Path) -> Callable[[str], Gate]:
    def make(name: str) -> Gate:
        return Gate(tmp_path, name)

    return make


def _pidfd_open(pid: int) -> int | None:
    """None when the process is already gone. Some Python builds lack os.pidfd_open, so it falls
    back to the system call, number 434 on every architecture Linux gained it on."""
    if hasattr(os, "pidfd_open"):
        try:
            return os.pidfd_open(pid)
        except ProcessLookupError:
            return None
    libc = ctypes.CDLL(None, use_errno=True)
    fd = int(libc.syscall(434, pid, 0))
    if fd >= 0:
        return fd
    if ctypes.get_errno() == errno.ESRCH:
        return None
    raise OSError(ctypes.get_errno(), "pidfd_open failed")


def _wait_gone(pid: int, deadline_s: float = 10.0) -> None:
    """Wait on a pidfd, not on a sleep: it is readable once the process has terminated."""
    fd = _pidfd_open(pid)
    if fd is None:
        return
    try:
        ready, _, _ = select.select([fd], [], [], deadline_s)
        assert ready, f"process {pid} is still alive"
    finally:
        os.close(fd)


@pytest.fixture
def wait_gone() -> Callable[[int], None]:
    return _wait_gone
