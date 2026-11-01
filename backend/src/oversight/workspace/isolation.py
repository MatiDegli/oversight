"""Linux network/PID isolation, with private entry evidence and bounded cleanup.

The server authenticates entry before releasing the helper to exec pytest. A pidfd pins namespace
init until cleanup finishes: its exit follows the kernel's teardown of every namespace member,
including children that left the process session. This does not isolate the filesystem.
"""

import asyncio
import contextlib
import ctypes
import json
import os
import shlex
import shutil
import signal
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from oversight.workspace.errors import WorkspaceBusy
from oversight.workspace.isolation_entry import FLAGS, HELPER, LAUNCHER, MARKER
from oversight.workspace.runner import (
    DEFAULT_OUTPUT_CAP,
    KILL_WAIT_S,
    PIPE_DRAIN_S,
    TERM_GRACE_S,
    ProcessCollector,
    ProcessResult,
    child_env,
    settle_cleanup,
    wait_done,
)


class IsolationUnavailable(OSError):
    """No trustworthy entry or complete namespace cleanup; never retry with network access."""


def unshare_binary() -> str:
    if sys.platform == "win32":
        raise FileNotFoundError("network isolation C is Linux-only")
    binary = shutil.which("unshare")
    if binary is None:
        raise FileNotFoundError("unshare is not installed")
    return str(Path(binary).resolve())


def signature(runtime: Path, binary: str) -> list[str]:
    return [binary, *FLAGS, sys.executable, "-I", "-S", "-c", HELPER]


def refuse_orphans(runtime: Path, *, proc: Path = Path("/proc")) -> None:
    """Called under the runtime lock, before reset. Report exact matches; never kill them."""
    if sys.platform == "win32":
        return
    marker = MARKER + str(runtime.resolve())
    for entry in proc.iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            argv = (
                (entry / "cmdline").read_bytes().decode(errors="replace").rstrip("\0").split("\0")
            )
        except (FileNotFoundError, ProcessLookupError):
            continue
        if not argv or not Path(argv[0]).is_absolute() or Path(argv[0]).name != "unshare":
            continue
        prefix = [argv[0], *FLAGS]
        helper = ["-I", "-S", "-c", HELPER]
        offset = len(prefix) + 1  # Skip the interpreter path, which may differ across builds.
        if (
            argv[: len(prefix)] == prefix
            and argv[offset : offset + len(helper)] == helper
            and marker in argv[offset + len(helper) :]
        ):
            raise WorkspaceBusy(
                f"orphan isolated runner PID {entry.name}; stop this PID before restarting: "
                + shlex.join(argv)
            )


def _launch(binary: str, command: Sequence[str]) -> list[str]:
    return [sys.executable, "-I", "-S", "-c", LAUNCHER, str(os.getpid()), binary, *command]


async def _read_entry(fd: int) -> bytes:
    reader = asyncio.StreamReader(limit=4096)
    protocol = asyncio.StreamReaderProtocol(reader)
    pipe = os.fdopen(fd, "rb", buffering=0)
    transport, _ = await asyncio.get_running_loop().connect_read_pipe(lambda: protocol, pipe)
    try:
        return await reader.readuntil(b"\n")
    finally:
        transport.close()


def open_pidfd(pid: int) -> int:
    """Some CPython builds omit os.pidfd_open; Linux exposes the same syscall directly."""
    if sys.platform == "win32":
        raise IsolationUnavailable("network isolation C is Linux-only")
    if hasattr(os, "pidfd_open"):
        return os.pidfd_open(pid)
    libc = ctypes.CDLL(None, use_errno=True)
    fd = int(libc.syscall(434, pid, 0))
    if fd < 0:
        raise OSError(ctypes.get_errno(), "pidfd_open failed")
    return fd


def _confirm(raw: bytes, nonce: str, supervisor: int, port: int) -> tuple[dict[str, object], int]:
    report = cast(dict[str, object], json.loads(raw))
    identities = report["identities"]
    if not isinstance(identities, dict):
        raise ValueError("missing identities")
    if (
        report.get("nonce") != nonce
        or report.get("pid") != 1
        or report.get("private_proc") is not True
        or report.get("server_hidden") is not True
        or report.get("procdev") == os.stat("/proc").st_dev
        or (port and report.get("refused") is not True)
    ):
        raise ValueError("invalid namespace confirmation")
    identities = cast(dict[str, object], identities)
    children = Path(f"/proc/{supervisor}/task/{supervisor}/children").read_text().split()
    if len(children) != 1:
        raise ValueError("ambiguous namespace init")
    child = int(children[0])
    pidfd = open_pidfd(child)
    try:
        for name in ("net", "pid", "mnt"):
            actual = os.readlink(f"/proc/{child}/ns/{name}")
            if identities.get(name) != actual or actual == os.readlink(f"/proc/self/ns/{name}"):
                raise ValueError("namespace identity mismatch")
        status = Path(f"/proc/{child}/status").read_text().splitlines()
        if not any(
            row.startswith("NSpid:") and row.split()[1:] == [str(child), "1"] for row in status
        ):
            raise ValueError("helper is not namespace init")
    except BaseException:
        os.close(pidfd)
        raise
    return report, pidfd


def _signal(pid: int, sig: signal.Signals) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, sig)


async def _namespace_gone(pidfd: int) -> bool:
    loop = asyncio.get_running_loop()
    exited: asyncio.Future[None] = loop.create_future()
    loop.add_reader(pidfd, lambda: None if exited.done() else exited.set_result(None))
    try:
        if not await wait_done(exited, KILL_WAIT_S):
            raise IsolationUnavailable("namespace init did not exit after SIGKILL")
        return True
    finally:
        loop.remove_reader(pidfd)
        os.close(pidfd)


async def _cleanup(
    transport: asyncio.SubprocessTransport, collector: ProcessCollector, init: int | None
) -> None:
    if sys.platform == "win32":
        raise IsolationUnavailable("network isolation C is Linux-only")
    pid = transport.get_pid()
    try:
        if not collector.exited.done():
            _signal(pid, signal.SIGTERM)
            await wait_done(collector.exited, TERM_GRACE_S)
        # unshare's kill-child handles PID 1, which may ignore SIGTERM.
        if not collector.exited.done():
            _signal(pid, signal.SIGKILL)
        waits = [wait_done(collector.exited, KILL_WAIT_S)]
        if init is not None:
            waits.append(_namespace_gone(init))
        reaped = (await asyncio.gather(*waits))[0]
        if not reaped:
            raise IsolationUnavailable("unshare did not exit after SIGKILL")
        await wait_done(collector.closed, PIPE_DRAIN_S)
    finally:
        transport.close()


class IsolatedRunner:
    """A Spawn implementation selected by C3 or injected for local acceptance/tests."""

    def __init__(self, runtime: Path, *, port: int = 0) -> None:
        self.runtime = runtime.resolve()
        self.port = port
        self.entry: dict[str, object] | None = None
        self._ready = False
        self.cleanup_complete = True
        self.failure: str | None = None

    async def version(self, cwd: Path) -> str:
        result = await self._run(["--version"], cwd, 5, None, 1000, probe=True)
        if result.returncode != 0 or result.timed_out:
            raise IsolationUnavailable("could not read util-linux version")
        return result.stdout.strip()

    async def preflight(self) -> None:
        try:
            result = await self._run(
                ["true"], self.runtime, 5, None, DEFAULT_OUTPUT_CAP, probe=True
            )
        except OSError as error:
            raise IsolationUnavailable(f"isolation preflight unavailable: {error}") from error
        if result.returncode != 0 or result.timed_out:
            raise IsolationUnavailable("isolation preflight unavailable: " + result.stderr[-500:])
        self._ready = True

    async def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout_s: float,
        env: Mapping[str, str] | None = None,
        output_cap: int = DEFAULT_OUTPUT_CAP,
    ) -> ProcessResult:
        self.entry = None
        try:
            if not self._ready:
                await self.preflight()
            return await self._run(argv, cwd, timeout_s, env, output_cap)
        except OSError as error:
            self.failure = str(error)
            raise IsolationUnavailable(str(error)) from error

    async def _run(
        self,
        argv: Sequence[str],
        cwd: Path,
        timeout_s: float,
        env: Mapping[str, str] | None,
        cap: int,
        *,
        probe: bool = False,
    ) -> ProcessResult:
        abort = asyncio.Event()
        task = asyncio.create_task(self._execute(argv, cwd, timeout_s, env, cap, abort, probe))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            abort.set()
            await settle_cleanup(task)
            task.result()  # A failed cleanup must stay unavailable, including during cancellation.
            raise

    async def _execute(
        self,
        argv: Sequence[str],
        cwd: Path,
        timeout_s: float,
        env: Mapping[str, str] | None,
        cap: int,
        abort: asyncio.Event,
        probe: bool,
    ) -> ProcessResult:
        started = time.monotonic()
        binary = unshare_binary()
        collector = ProcessCollector(cap)
        if sys.platform == "win32":
            raise IsolationUnavailable("network isolation C is Linux-only")
        readfd, writefd = os.pipe2(os.O_CLOEXEC)
        ackread, ackwrite = os.pipe2(os.O_CLOEXEC)
        nonce = uuid.uuid4().hex
        args = [str(writefd), str(ackread), nonce, str(os.getpid())]
        args += [os.readlink(f"/proc/self/ns/{name}") for name in ("net", "pid", "mnt")]
        args += [str(os.stat("/proc").st_dev), MARKER + str(self.runtime), str(self.port)]
        command = [*FLAGS, *argv] if probe else [*signature(self.runtime, binary)[1:], *args, *argv]
        self.cleanup_complete = False
        try:
            transport, _ = await asyncio.get_running_loop().subprocess_exec(
                lambda: collector,
                *_launch(binary, command),
                cwd=cwd,
                env=child_env(env),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                pass_fds=(writefd, ackread),
            )
        except BaseException:
            self.cleanup_complete = True  # No process was launched.
            os.close(readfd)
            os.close(ackwrite)
            raise
        finally:
            os.close(writefd)
            os.close(ackread)
        return await self._monitor(
            transport, collector, readfd, ackwrite, nonce, timeout_s, abort, probe, started
        )

    async def _monitor(
        self,
        transport: asyncio.SubprocessTransport,
        collector: ProcessCollector,
        readfd: int,
        ackwrite: int,
        nonce: str,
        timeout_s: float,
        abort: asyncio.Event,
        probe: bool,
        started: float,
    ) -> ProcessResult:
        entry = asyncio.create_task(_read_entry(readfd))
        watcher = asyncio.create_task(abort.wait())
        init = None
        confirmed = probe
        timed_out = False
        try:
            async with asyncio.timeout(timeout_s):
                if not probe:
                    done, _ = await asyncio.wait(
                        {entry, collector.exited, watcher}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if entry in done:
                        try:
                            self.entry, init = _confirm(
                                entry.result(), nonce, transport.get_pid(), self.port
                            )
                        except (
                            ValueError,
                            TypeError,
                            KeyError,
                            asyncio.LimitOverrunError,
                            OSError,
                            asyncio.IncompleteReadError,
                        ) as error:
                            raise IsolationUnavailable(
                                "entry confirmation unavailable: " + collector.stderr.text()[-1000:]
                            ) from error
                        confirmed = True
                        os.write(ackwrite, b"1")
                    elif not abort.is_set():
                        raise IsolationUnavailable("helper exited without confirmation")
                await asyncio.wait({collector.exited, watcher}, return_when=asyncio.FIRST_COMPLETED)
        except TimeoutError:
            timed_out = True
        finally:
            os.close(ackwrite)
            watcher.cancel()
            entry.cancel()
            await asyncio.gather(entry, watcher, return_exceptions=True)
            await _cleanup(transport, collector, init)
            self.cleanup_complete = True
        if not confirmed and not abort.is_set():
            raise IsolationUnavailable("helper did not confirm entry")
        return ProcessResult(
            transport.get_returncode(),
            collector.stdout.text(),
            collector.stderr.text(),
            timed_out,
            collector.truncated,
            round((time.monotonic() - started) * 1000),
        )
