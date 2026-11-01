"""Console-attached Windows supervision; only public W1 workspace primitives are used.

The outer kill-on-close handle is deliberately never closed during this process's life.
Native console exit evidence is required: the candidate SIGBREAK status below is not a
claim of Windows acceptance. Linux-injected tests exercise policy, not the NT kernel.
"""

import asyncio
import errno
import os
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import smoke
from demo_check_io import STOP_DEADLINE
from oversight.workspace import (
    assign_job,
    close_job,
    create_kill_job,
    job_pids,
    resume_suspended,
    terminate_job,
)

CREATE_FLAGS = 0x00000004 | 0x00000200  # CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP
CTRL_BREAK = 1  # CTRL_BREAK_EVENT; no CREATE_NO_WINDOW and no signal handler in the server.
NT_GRACEFUL_EXITS = (0xC000013A,)  # Candidate, must be pinned/confirmed by native console proof.
POLL = 0.02


@dataclass(frozen=True)
class Jobs:
    """Inject public job operations without importing private kernel types."""

    create: Callable[[], int] = create_kill_job
    assign: Callable[[int, int], None] = assign_job
    resume: Callable[[int], None] = resume_suspended
    pids: Callable[[int], tuple[int, ...]] = job_pids
    terminate: Callable[[int], None] = terminate_job
    close: Callable[[int], None] = close_job


JOBS = Jobs()
_outer: int | None = None


def retain_outer(jobs: Jobs = JOBS) -> int:
    """Refuse incompatible ancestors before launching; never expose ownership of the handle."""
    global _outer
    if _outer is None:
        job = jobs.create()
        try:
            jobs.assign(job, os.getpid())
        except BaseException:
            jobs.close(job)  # no process was assigned; safe to close this failed job
            raise
        _outer = job  # non-inherited W1 handle; OS teardown alone releases it
    return _outer


async def settled[T](operation: Awaitable[T]) -> T:
    """Retain cleanup task/handles even if the caller is cancelled repeatedly."""
    task = asyncio.ensure_future(operation)
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    return result


async def wait_members(
    job: int, jobs: Jobs, expected: tuple[int, ...] = (), deadline_s: float = STOP_DEADLINE
) -> None:
    end = time.monotonic() + max(deadline_s, 0)
    while True:
        members = jobs.pids(job)
        if set(members) == set(expected):
            return
        if time.monotonic() >= end:
            raise smoke.SmokeError(f"job {job} still contains {members}; expected {expected}")
        await asyncio.sleep(POLL)


@dataclass
class Server:
    job: int
    log: Path
    jobs: Jobs
    child: subprocess.Popen[Any] | None = None

    async def dispose(self) -> None:
        """Retain the handle until both launcher and all descendants are gone.

        A failed empty-job query/wait leaves the handle open until process teardown.
        Cleanup failure is never a successful shutdown or permission to remove scratch.
        """
        try:
            self.jobs.terminate(self.job)
        finally:
            if self.child is not None:
                if self.child.poll() is None:
                    self.child.kill()  # also covers assignment failure before membership
                await asyncio.to_thread(self.child.wait, timeout=STOP_DEADLINE)
        await wait_members(self.job, self.jobs)
        self.jobs.close(self.job)

    async def stop(
        self, deadline_s: float = STOP_DEADLINE, send: Callable[[int, int], None] = os.kill
    ) -> None:
        assert self.child is not None
        try:
            if self.child.poll() is not None:
                raise smoke.SmokeError("server exited before CTRL_BREAK")
            send(self.child.pid, CTRL_BREAK)
            end = time.monotonic() + max(deadline_s, 0)
            while self.child.poll() is None:
                if time.monotonic() >= end:
                    raise smoke.SmokeError("CTRL_BREAK leader deadline exceeded")
                await asyncio.sleep(POLL)
            await wait_members(self.job, self.jobs, deadline_s=deadline_s)
            text = self.log.read_text(encoding="utf-8", errors="replace")
            code = self.child.returncode
            print(f"NT shutdown: loop={type(asyncio.get_running_loop()).__name__} code={code}")
            if code not in NT_GRACEFUL_EXITS or "Application shutdown complete" not in text:
                raise smoke.SmokeError(f"NT shutdown log/code verdict failed: code={code}\n{text}")
        except BaseException:
            await settled(self.dispose())
            raise
        self.jobs.close(self.job)


async def start_server(
    argv: list[str],
    env: dict[str, str],
    log: Path,
    *,
    jobs: Jobs = JOBS,
    spawn: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
) -> Server:
    """Assign before resume, killing/reaping even a child refused by an ancestor job."""
    server = Server(jobs.create(), log, jobs)
    try:
        with log.open("wb") as output:
            server.child = spawn(
                argv,
                cwd=smoke.REPO_ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                creationflags=CREATE_FLAGS,
            )
        jobs.assign(server.job, server.child.pid)
        jobs.resume(server.child.pid)
        return server
    except BaseException:
        await settled(server.dispose())
        raise


class Locking(Protocol):
    @property
    def LK_NBLCK(self) -> int: ...

    @property
    def LK_UNLCK(self) -> int: ...

    def locking(self, fd: int, mode: int, size: int, /) -> object: ...


async def probe_lock(
    runtime: Path, deadline_s: float = STOP_DEADLINE, *, api: Locking | None = None
) -> None:
    if api is None:
        if sys.platform != "win32":
            raise smoke.SmokeError("lock probe requires native Windows byte locking")
        import msvcrt

        api = msvcrt
    fd = os.open(runtime / "server.lock", os.O_RDWR)
    try:
        os.set_inheritable(fd, False)
        end = time.monotonic() + deadline_s
        while True:
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                api.locking(fd, api.LK_NBLCK, 1)
                break
            except OSError as error:
                if error.errno not in {errno.EACCES, errno.EDEADLK}:
                    raise
                if time.monotonic() >= end:
                    raise smoke.SmokeError("runtime lock probe deadline exceeded") from error
                await asyncio.sleep(POLL)
        os.lseek(fd, 0, os.SEEK_SET)
        api.locking(fd, api.LK_UNLCK, 1)
    finally:
        os.close(fd)


def cleanup(scratch: Path) -> None:
    """Bounded sharing retries; read-only files may be removed, locked paths must fail."""

    def writable_remove(
        operation: Callable[[str], object], path: str, error: BaseException
    ) -> None:
        if not isinstance(error, PermissionError):
            raise error
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        operation(path)

    for delay in (0.02, 0.04, 0.08, 0.16, 0.32, 0.32, None):
        try:
            shutil.rmtree(scratch, onexc=writable_remove)
            return
        except OSError as error:
            if getattr(error, "winerror", None) not in {5, 32, 33} or delay is None:
                raise
            time.sleep(delay)
