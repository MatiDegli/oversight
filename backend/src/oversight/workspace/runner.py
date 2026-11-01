"""The one place in the product that starts processes (docs/proposal.md, section 7, rule 8).

Never a shell, never a blocking call: the child runs in its own process group, has no stdin, gets an
environment built from an allowlist, and is killed with its whole group on a timeout or a
cancellation. Each stream keeps its first `cap` bytes and the last `TAIL_CAP` bytes; what lies
between is counted, not kept, and the pipes are always drained.
"""

import asyncio
import contextlib
import os
import signal
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from oversight.workspace._platform import (
    NT_CREATE_FLAGS,
    NT_KILL_CODE,
    assign_job,
    close_job,
    create_kill_job,
    job_pids,
    resume_suspended,
    terminate_job,
    terminate_suspended,
)

TERM_GRACE_S = 2.0
"""How long a group gets after SIGTERM before SIGKILL."""
KILL_WAIT_S = 5.0
"""How long to wait for the leader to exit after SIGKILL."""
PIPE_DRAIN_S = 1.0
"""How long to wait for the pipes to reach EOF before closing them."""
DEFAULT_OUTPUT_CAP = 262_144
TAIL_CAP = 16_384
"""How much of the end of a stream is kept once the head is full: a test run's summary is last."""


@dataclass(frozen=True)
class ProcessResult:
    returncode: int | None
    """None when the process could not be reaped; negative for a signal."""
    stdout: str
    stderr: str
    timed_out: bool
    truncated: bool
    """True when bytes from the middle of a stream were dropped; the text then names how many."""
    duration_ms: int


class Spawn(Protocol):
    """The shape of `run_process`, so a caller can be handed a stand-in in tests."""

    async def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout_s: float,
        env: Mapping[str, str] | None = None,
        output_cap: int = ...,
    ) -> ProcessResult: ...


def _resolve(future: "asyncio.Future[None]") -> None:
    if not future.done():
        future.set_result(None)


def omitted_marker(count: int) -> str:
    return f"\n[... {count} bytes omitted ...]\n"


class _Stream:
    """The first `cap` bytes of a stream and the last `tail_cap` bytes of what came after."""

    def __init__(self, cap: int, tail_cap: int) -> None:
        self.cap = cap
        self.tail_cap = tail_cap
        self.head = bytearray()
        self.tail = bytearray()
        self.omitted = 0

    def feed(self, data: bytes) -> None:
        room = self.cap - len(self.head)
        if room > 0:
            self.head.extend(data[:room])
            data = data[room:]
        if not data:
            return
        self.tail.extend(data)
        excess = len(self.tail) - self.tail_cap
        if excess > 0:
            del self.tail[:excess]
            self.omitted += excess

    def text(self) -> str:
        gap = omitted_marker(self.omitted).encode() if self.omitted else b""
        return (bytes(self.head) + gap + bytes(self.tail)).decode("utf-8", errors="replace")


class ProcessCollector(asyncio.SubprocessProtocol):
    """Keeps the head and the tail of each stream, drains the rest, and reports exit and EOF."""

    def __init__(self, cap: int) -> None:
        loop = asyncio.get_running_loop()
        self.stdout = _Stream(cap, TAIL_CAP)
        self.stderr = _Stream(cap, TAIL_CAP)
        self.exited: asyncio.Future[None] = loop.create_future()
        self.closed: asyncio.Future[None] = loop.create_future()

    @property
    def truncated(self) -> bool:
        return bool(self.stdout.omitted or self.stderr.omitted)

    def pipe_data_received(self, fd: int, data: bytes) -> None:
        (self.stdout if fd == 1 else self.stderr).feed(data)

    def process_exited(self) -> None:
        _resolve(self.exited)

    def connection_lost(self, exc: Exception | None) -> None:
        _resolve(self.closed)


def child_env(extra: Mapping[str, str] | None) -> dict[str, str]:
    """An allowlist, never `os.environ`: no API key reaches a child. HOME comes from the caller."""
    base = {
        "PATH": os.environ.get("PATH", os.defpath),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
    }
    return base | dict(extra or {})


def _killpg(pgid: int, sig: signal.Signals) -> None:
    if sys.platform == "win32":
        raise OSError("POSIX process groups are unavailable on Windows")
    # The group may already be gone, even after a normal exit: cleanup is idempotent.
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pgid, sig)


async def wait_done(future: "asyncio.Future[None]", within_s: float) -> bool:
    """Wait for a future without cancelling it; True when it finished within the timeout."""
    finished, _ = await asyncio.wait({future}, timeout=within_s)
    return bool(finished)


async def _reap(
    transport: asyncio.SubprocessTransport,
    collector: ProcessCollector,
    pgid: int,
    *,
    terminate: bool,
) -> None:
    """End the group and bound every wait. Pipes are closed even if a descendant holds them."""
    if sys.platform == "win32":
        raise OSError("POSIX reaping is unavailable on Windows")
    if terminate:
        _killpg(pgid, signal.SIGTERM)
        await wait_done(collector.exited, TERM_GRACE_S)
    _killpg(pgid, signal.SIGKILL)
    if await wait_done(collector.exited, KILL_WAIT_S):
        await wait_done(collector.closed, PIPE_DRAIN_S)
    transport.close()


async def _reap_nt(
    transport: asyncio.SubprocessTransport,
    collector: ProcessCollector,
    job: int,
    *,
    launch_failed: bool = False,
) -> None:
    """Refuse evidence unless both the leader and every job member are gone before return."""
    try:
        if launch_failed:
            handle = getattr(transport.get_extra_info("subprocess"), "_handle", None)
            terminate_suspended(transport.get_pid(), process_handle=int(handle) if handle else None)
        terminate_job(job)
        deadline = time.monotonic() + KILL_WAIT_S
        while not collector.exited.done() or job_pids(job):
            if time.monotonic() >= deadline:
                raise OSError("Windows job cleanup did not complete before its deadline")
            await asyncio.sleep(0.01)
        if not await wait_done(collector.closed, PIPE_DRAIN_S):
            raise OSError("Windows job pipes did not drain")
    finally:
        # Closing the job is the final kill fallback even when termination/querying failed.
        try:
            close_job(job)
        finally:
            await wait_done(collector.exited, KILL_WAIT_S)
            transport.close()


async def _execute_nt(
    argv: Sequence[str],
    cwd: Path,
    timeout_s: float,
    env: Mapping[str, str] | None,
    cap: int,
    abort: asyncio.Event,
) -> ProcessResult:
    started = time.monotonic()
    collector = ProcessCollector(cap)
    job = create_kill_job()
    try:
        transport, _ = await asyncio.get_running_loop().subprocess_exec(
            lambda: collector,
            *argv,
            cwd=cwd,
            env=child_env(env),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=NT_CREATE_FLAGS,
        )
    except BaseException:
        close_job(job)
        raise
    try:
        assign_job(job, transport.get_pid())
        resume_suspended(transport.get_pid())
    except BaseException:
        await _reap_nt(transport, collector, job, launch_failed=True)
        raise
    watcher = asyncio.ensure_future(abort.wait())
    try:
        finished, _ = await asyncio.wait(
            {collector.exited, watcher}, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        watcher.cancel()
    stopped = abort.is_set() or not finished or not collector.exited.done()
    natural_code = transport.get_returncode() if not stopped else None
    await _reap_nt(transport, collector, job)
    return ProcessResult(
        natural_code if natural_code is not None else NT_KILL_CODE,
        collector.stdout.text(),
        collector.stderr.text(),
        not finished,
        collector.truncated,
        round((time.monotonic() - started) * 1000),
    )


async def probe_job_support(runtime: Path) -> None:
    """Before reset, prove that inherited ancestor jobs permit the per-run nested job."""
    result = await run_process(
        [sys.executable, "-I", "-S", "-c", "pass"],
        cwd=runtime,
        timeout_s=5,
        env={"HOME": str(runtime)},
    )
    if result.returncode != 0 or result.timed_out:
        raise OSError("ancestor Windows job is incompatible with suspended nested jobs")


async def _execute(
    argv: Sequence[str],
    cwd: Path,
    timeout_s: float,
    env: Mapping[str, str] | None,
    cap: int,
    abort: asyncio.Event,
) -> ProcessResult:
    if sys.platform == "win32":
        return await _execute_nt(argv, cwd, timeout_s, env, cap, abort)
    started = time.monotonic()
    collector = ProcessCollector(cap)
    transport, _ = await asyncio.get_running_loop().subprocess_exec(
        lambda: collector,
        *argv,
        cwd=cwd,
        env=child_env(env),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    pgid = transport.get_pid()
    watcher = asyncio.ensure_future(abort.wait())
    try:
        finished, _ = await asyncio.wait(
            {collector.exited, watcher}, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        watcher.cancel()
    exited = collector.exited.done()
    await _reap(transport, collector, pgid, terminate=not exited)
    return ProcessResult(
        returncode=transport.get_returncode(),
        stdout=collector.stdout.text(),
        stderr=collector.stderr.text(),
        timed_out=not finished,
        truncated=collector.truncated,
        duration_ms=round((time.monotonic() - started) * 1000),
    )


async def settle_cleanup(task: "asyncio.Future[ProcessResult]") -> None:
    """Wait for the cleanup to end; further cancellations are absorbed until it does."""
    while not task.done():
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait({task})
    if not task.cancelled():
        task.exception()


async def run_process(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout_s: float,
    env: Mapping[str, str] | None = None,
    output_cap: int = DEFAULT_OUTPUT_CAP,
) -> ProcessResult:
    """Run `argv` to its end or its timeout. On a timeout or a cancellation the whole process group
    is killed first (SIGTERM, then SIGKILL) and the cleanup is bounded; a cancellation is re-raised
    only after the cleanup ends, even if it arrives while the process is still launching."""
    abort = asyncio.Event()
    task = asyncio.ensure_future(_execute(argv, cwd, timeout_s, env, output_cap, abort))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        abort.set()
        await settle_cleanup(task)
        if sys.platform == "win32":
            task.result()  # Cleanup failures refuse evidence, including during cancellation.
        raise
