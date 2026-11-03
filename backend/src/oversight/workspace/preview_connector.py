"""Unwired supervisor-owned connector to a fixed private preview API.

Targets borrow trusted live pidfds. Each call duplicates them and returns ownership of
exactly one connected nonblocking TCP socket. Namespace provenance is the caller's duty.
"""

from __future__ import annotations

import array
import asyncio
import contextlib
import json
import math
import os
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from oversight.workspace.preview_connector_entry import namespaces, target_identity
from oversight.workspace.runner import ProcessCollector, child_env, wait_done

ENTRY = Path(__file__).with_name("preview_connector_entry.py")


class _CleanupError(OSError):
    """A helper could not be proved dead; connection evidence must stay unavailable."""


@dataclass(frozen=True, slots=True)
class ConnectorTarget:
    u0_pidfd: int
    n0_pidfd: int
    api_port: int
    identity: tuple[str, str] = field(init=False, repr=False)
    handles: tuple[tuple[int, int], tuple[int, int]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.api_port) is not int or not 1 <= self.api_port <= 65535:
            raise ValueError("API port must be a non-boolean integer in 1..65535")
        object.__setattr__(self, "identity", target_identity(self.u0_pidfd, self.n0_pidfd))
        object.__setattr__(
            self, "handles", (_fd_identity(self.u0_pidfd), _fd_identity(self.n0_pidfd))
        )


def _fd_identity(fd: int) -> tuple[int, int]:
    info = os.fstat(fd)
    return info.st_dev, info.st_ino


def _duplicates(target: ConnectorTarget) -> tuple[int, int]:
    if type(target) is not ConnectorTarget:
        raise TypeError("expected ConnectorTarget")
    try:
        borrowed_u0, borrowed_n0 = target.u0_pidfd, target.n0_pidfd
        identity, handles, port = target.identity, target.handles, target.api_port
    except AttributeError as error:
        raise TypeError("malformed ConnectorTarget") from error
    u0 = os.dup(borrowed_u0)
    try:
        n0 = os.dup(borrowed_n0)
        try:
            if (_fd_identity(u0), _fd_identity(n0)) != handles:
                raise ValueError("borrowed pidfd identity changed")
            if target_identity(u0, n0) != identity:
                raise ValueError("target namespace identity changed")
            if type(port) is not int or not 1 <= port <= 65535:
                raise ValueError("invalid API port")
            return u0, n0
        except BaseException:
            os.close(n0)
            raise
    except BaseException:
        os.close(u0)
        raise


def _namespace_witness(data: bytes) -> tuple[dict[str, str], dict[str, str]]:
    raw: object = json.loads(data)
    if not isinstance(raw, dict):
        raise ValueError("malformed namespace witness")
    witness = cast(dict[str, object], raw)
    if set(witness) != {"before", "after"}:
        raise ValueError("malformed namespace witness")
    memberships: list[dict[str, str]] = []
    for name in ("before", "after"):
        raw_membership = witness[name]
        if not isinstance(raw_membership, dict):
            raise ValueError("malformed namespace membership")
        membership = cast(dict[str, object], raw_membership)
        if set(membership) != {"user", "net", "pid", "mnt"} or any(
            type(value) is not str for value in membership.values()
        ):
            raise ValueError("malformed namespace membership")
        memberships.append(cast(dict[str, str], membership))
    return memberships[0], memberships[1]


def _receive(channel: socket.socket, target: ConnectorTarget) -> socket.socket:
    """One datagram and exactly one connected socket; close all rejected rights."""
    data, ancillary, flags, _ = channel.recvmsg(
        4096, socket.CMSG_SPACE(16 * 4), socket.MSG_CMSG_CLOEXEC
    )
    rights: list[int] = []
    client: socket.socket | None = None
    try:
        malformed = False
        for level, kind, payload in ancillary:
            if (level, kind) != (socket.SOL_SOCKET, socket.SCM_RIGHTS):
                malformed = True
                continue
            values = array.array("i")
            whole = len(payload) - len(payload) % values.itemsize
            values.frombytes(payload[:whole])
            rights.extend(values)
            malformed |= whole != len(payload)
        if malformed or flags & (socket.MSG_CTRUNC | socket.MSG_TRUNC) or len(rights) != 1:
            raise ValueError("expected exactly one socket handoff")
        before, after = _namespace_witness(data)
        current = namespaces()
        if before != current or (after["user"], after["net"]) != target.identity:
            raise ValueError("unverified connector namespace membership")
        if any(after[name] != current[name] for name in ("pid", "mnt")):
            raise ValueError("connector crossed PID or mount boundary")
        client = socket.socket(fileno=rights[-1])
        rights.pop()
        if (
            client.family != socket.AF_INET
            or client.type != socket.SOCK_STREAM
            or client.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
            or client.getpeername() != ("127.0.0.1", target.api_port)
            or client.getsockname()[0] != "127.0.0.1"
        ):
            raise ValueError("handoff is not the fixed private API connection")
        client.setblocking(False)
        result, client = client, None
        return result
    finally:
        if client is not None:
            client.close()
        for fd in rights:
            os.close(fd)


async def _handoff(channel: socket.socket, target: ConnectorTarget) -> socket.socket:
    loop = asyncio.get_running_loop()
    ready: asyncio.Future[None] = loop.create_future()

    def readable() -> None:
        if not ready.done():
            ready.set_result(None)

    loop.add_reader(channel.fileno(), readable)
    try:
        await ready
        return _receive(channel, target)
    finally:
        loop.remove_reader(channel.fileno())


async def _settled(task: asyncio.Task[object]) -> None:
    while not task.done():
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait({task})


async def _exchange(
    channel: socket.socket,
    target: ConnectorTarget,
    abort: asyncio.Event,
    transport: asyncio.SubprocessTransport,
    collector: ProcessCollector,
) -> socket.socket:
    handoff = asyncio.create_task(_handoff(channel, target))
    cancelled = asyncio.create_task(abort.wait())
    result: socket.socket | None = None
    try:
        done, _ = await asyncio.wait(
            {handoff, cancelled, collector.exited}, return_when=asyncio.FIRST_COMPLETED
        )
        if abort.is_set():
            raise TimeoutError("connector aborted")
        if handoff not in done and collector.exited in done and transport.get_returncode() != 0:
            raise OSError("connector entry failed: " + collector.stderr.text())
        client = await handoff
        await asyncio.wait({collector.exited, cancelled}, return_when=asyncio.FIRST_COMPLETED)
        if abort.is_set():
            raise TimeoutError("connector aborted after handoff")
        if transport.get_returncode() != 0:
            raise OSError("connector did not exit successfully")
        result = client
        return result
    finally:
        handoff.cancel()
        cancelled.cancel()
        await asyncio.gather(handoff, cancelled, return_exceptions=True)
        if not handoff.cancelled() and handoff.exception() is None:
            received = handoff.result()
            if received is not result:
                received.close()


async def _reap(transport: asyncio.SubprocessTransport, collector: ProcessCollector) -> None:
    try:
        if transport.get_returncode() is None:
            with contextlib.suppress(ProcessLookupError):
                transport.kill()
        if not await wait_done(collector.exited, 5):
            raise OSError("helper death did not complete")
        if not await wait_done(collector.closed, 5):
            raise OSError("helper pipes did not close")
    except Exception as error:
        raise _CleanupError("connector cleanup is unverified: " + str(error)) from error
    finally:
        transport.close()


async def _attempt(target: ConnectorTarget, abort: asyncio.Event) -> socket.socket:
    collector = ProcessCollector(4096)
    u0, n0 = _duplicates(target)
    transport: asyncio.SubprocessTransport | None = None
    channel: socket.socket | None = None
    peer: socket.socket | None = None
    result: socket.socket | None = None
    try:
        channel, peer = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        channel.setblocking(False)
        transport, _ = await asyncio.get_running_loop().subprocess_exec(
            lambda: collector,
            sys.executable,
            "-I",
            "-B",
            str(ENTRY),
            str(u0),
            str(n0),
            str(target.api_port),
            str(peer.fileno()),
            cwd=ENTRY.parent,
            env=child_env(None),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            pass_fds=(u0, n0, peer.fileno()),
        )
        peer.close()
        result = await _exchange(channel, target, abort, transport, collector)
        return result
    finally:
        if channel is not None:
            channel.close()
        if peer is not None:
            peer.close()
        os.close(u0)
        os.close(n0)
        if transport is not None:
            try:
                await _reap(transport, collector)
            except BaseException:
                if result is not None:
                    result.close()
                raise


async def connect_api(target: ConnectorTarget, *, timeout_s: float) -> socket.socket:
    """Connect through trusted U0 then N0; never attempt a host-network connection."""
    if type(target) is not ConnectorTarget:
        raise TypeError("expected ConnectorTarget")
    if (
        isinstance(timeout_s, bool)
        or type(timeout_s) not in (int, float)
        or not math.isfinite(timeout_s)
        or timeout_s <= 0
    ):
        raise ValueError("timeout must be finite and positive")
    abort = asyncio.Event()
    task = asyncio.create_task(_attempt(target, abort))
    try:
        async with asyncio.timeout(timeout_s):
            return await asyncio.shield(task)
    except (asyncio.CancelledError, TimeoutError):
        abort.set()
        await _settled(task)
        if not task.cancelled() and task.exception() is None:
            task.result().close()
        elif not task.cancelled():
            error = task.exception()
            if isinstance(error, _CleanupError):
                raise error from None
        raise
