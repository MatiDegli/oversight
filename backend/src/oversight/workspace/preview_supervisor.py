"""Trusted absolute entry. Never imports worktree code or inherits backend credentials.

Backend death is EOF, not a parent-death kill of this supervisor: it must finish C cleanup.
The existing parent-death launcher instead connects supervisor death to unshare death.
"""

import asyncio
import contextlib
import json
import os
import secrets
import socket
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

from oversight.workspace.isolation import (
    cleanup_preview_namespace,
    confirm_preview_entry,
    preview_launch,
    read_preview_entry,
)
from oversight.workspace.locks import offload
from oversight.workspace.preview_registry import PreviewRegistry, identity_data
from oversight.workspace.preview_types import preserve_ownership
from oversight.workspace.runner import ProcessCollector, child_env

ENTRY_PATH = Path(__file__).resolve()


@dataclass
class Deadlines:
    lease: float
    absolute: float
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)

    def renew(self, deadline: float) -> None:
        now = self.clock()
        if now < min(self.lease, self.absolute):
            self.lease = min(deadline, self.absolute)

    def expired(self) -> str | None:
        now = self.clock()
        if now >= self.absolute:
            return "lifetime_expired"
        return "lease_expired" if now >= self.lease else None


def exit_reason(code: int | None) -> str:
    if code == 74:
        return "memory_limit"
    if code in (70, 71, 72, 73):
        return "launch_failed"
    return "app_exited"


async def control_reader(fd: int) -> tuple[asyncio.StreamReader, asyncio.ReadTransport]:
    reader = asyncio.StreamReader(limit=4096)
    protocol = asyncio.StreamReaderProtocol(reader)
    transport, _ = await asyncio.get_running_loop().connect_read_pipe(
        lambda: protocol, os.fdopen(fd, "rb", buffering=0)
    )
    return reader, transport


def report(fd: int, **fields: Any) -> None:
    os.write(fd, json.dumps(fields, separators=(",", ":")).encode() + b"\n")


def namespace_identity(unshare: int) -> dict[str, Any]:
    child = Path(f"/proc/{unshare}/task/{unshare}/children")
    return identity_data(int(child.read_text().strip()))


class Supervisor:
    """Owns only trusted control/report descriptors and C's authenticated process identities."""

    def __init__(self, config: dict[str, Any], control: int, output: int, listener: int):
        self.config = config
        self.registry = PreviewRegistry(Path(config["runtime"]))
        self.control, self.output, self.listener = control, output, listener
        self.transport: asyncio.SubprocessTransport | None = None
        self.collector = ProcessCollector(2048)
        self.init: int | None = None
        self.entry_read, self.entry_write = os.pipe2(os.O_CLOEXEC)
        try:
            self.ack_read, self.ack_write = os.pipe2(os.O_CLOEXEC)
        except BaseException:
            os.close(self.entry_read)
            os.close(self.entry_write)
            raise
        self.entry_task: asyncio.Task[bytes] | None = None
        self.nonce = secrets.token_hex(32)

    async def run(
        self,
        reader: asyncio.StreamReader | None = None,
        pipe: asyncio.ReadTransport | None = None,
        *,
        authorized: bool = False,
    ) -> None:
        if reader is None or pipe is None:
            reader, pipe = await control_reader(self.control)
        reason = "launch_failed"
        try:
            # Backend records this supervisor's birth durably before authorizing any child.
            if authorized or await asyncio.wait_for(reader.readline(), 10) == b"launch\n":
                await asyncio.wait_for(self.enter(), self.config["readiness_s"])
                report(self.output, kind="confirmed", nonce=self.nonce)
                reason = await self.execute(reader)
        except (Exception, asyncio.CancelledError):
            # Never expose child exception text or application output.
            reason = "launch_failed"
        finally:
            pipe.close()
            await self.cleanup(reason)

    async def enter(self) -> None:
        config = self.config
        argv = list(config["argv"])
        origin = argv.index("--oversight-origin") if "--oversight-origin" in argv else len(argv)
        argv[origin:origin] = ["--ready-nonce", self.nonce]
        command, descriptors = preview_launch(
            Path(config["runtime"]),
            argv,
            entry=self.entry_write,
            ack=self.ack_read,
            listener=self.listener,
            nonce=self.nonce,
            refusal_port=config["refusal_port"],
        )
        await preserve_ownership(self._spawn(command, descriptors))
        for name in ("listener", "entry_write", "ack_read"):
            os.close(getattr(self, name))
            setattr(self, name, -1)
        assert self.transport is not None
        pid = self.transport.get_pid()
        self.entry_task = asyncio.create_task(read_preview_entry(self.entry_read))
        self.entry_read = -1
        raw = await asyncio.wait_for(asyncio.shield(self.entry_task), config["readiness_s"])
        confirmation, self.init = await offload(
            lambda: confirm_preview_entry(raw, self.nonce, pid, config["refusal_port"])
        )

        def confirmed() -> None:
            namespace = namespace_identity(pid)
            self.registry.update(
                phase="confirmed", namespace=namespace, namespaces=confirmation["identities"]
            )

        await offload(confirmed)

    async def _spawn(self, command: list[str], descriptors: tuple[int, ...]) -> None:
        self.transport, _ = await asyncio.get_running_loop().subprocess_exec(
            lambda: self.collector,
            *command,
            cwd=self.config["scratch"],
            env=child_env(self.config["env"]),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            pass_fds=descriptors,
        )
        assert self.transport is not None
        pid = self.transport.get_pid()
        await offload(lambda: self.registry.update(phase="entering", unshare=identity_data(pid)))

    async def execute(self, reader: asyncio.StreamReader) -> str:
        config = self.config
        deadlines = Deadlines(config["lease_deadline"], config["absolute_deadline"])
        if await asyncio.wait_for(reader.readline(), config["readiness_s"]) != b"execute\n":
            return "shutdown"
        if reason := deadlines.expired():
            return reason
        # Confirmed durable ownership is mandatory before the sole execution acknowledgement.
        await offload(lambda: self.registry.update(phase="executing"))
        os.write(self.ack_write, b"1")
        os.close(self.ack_write)
        self.ack_write = -1
        report(self.output, kind="executing")
        return await watch(reader, self.collector, deadlines)

    async def cleanup(self, reason: str) -> None:
        if self.entry_task is not None:
            self.entry_task.cancel()
            await asyncio.gather(self.entry_task, return_exceptions=True)
        for name in ("listener", "entry_read", "entry_write", "ack_read", "ack_write"):
            fd = getattr(self, name)
            if fd >= 0:
                os.close(fd)
                setattr(self, name, -1)
        if self.transport is not None:
            try:
                await cleanup_preview_namespace(self.transport, self.collector, self.init)
            except BaseException:
                report(self.output, kind="unverified")
                os.close(self.output)
                return
        await offload(lambda: self.registry.update(phase="dead"))
        report(
            self.output,
            kind="dead",
            reason=reason,
            exit_code=self.transport.get_returncode() if self.transport else None,
            diagnostic=launcher_diagnostic(self.collector),
        )
        os.close(self.output)


async def supervise(runtime: Path, control: int, output: int, listener: int) -> None:
    """Read configuration only after the backend's durable ownership-write handoff.

    EOF before authorization cannot leave an unshare child: none has been created yet.
    Waiting here also avoids mistaking a concurrent ownership write for a partial registry.
    """
    reader, pipe = await control_reader(control)
    handed_over = False
    try:
        command = await asyncio.wait_for(reader.readline(), 10)
        registry = PreviewRegistry(runtime)
        data = await offload(registry.read)
        if data is None:
            raise ValueError("missing intent")
        if command != b"launch\n":
            await offload(lambda: registry.update(phase="dead"))
            report(output, kind="dead", reason="shutdown", exit_code=None, diagnostic="")
            return
        if "reviewed" in data["launch"]:
            from oversight.workspace._reviewed_preview import ReviewedExecution

            owner_socket = socket.socket(fileno=listener)
            handed_over = True
            try:
                await ReviewedExecution(
                    data["launch"],
                    registry,
                    reader,
                    owner_socket,
                    partial(report, output),
                ).run()
            finally:
                pipe.close()
                os.close(output)
            return
        owner = Supervisor(data["launch"], control, output, listener)
        handed_over = True
        await owner.run(reader, pipe, authorized=True)
    finally:
        if not handed_over:
            pipe.close()
            os.close(listener)
            os.close(output)


def launcher_diagnostic(collector: ProcessCollector, error: Exception | None = None) -> str:
    """Bounded allowlisted diagnostic facts, never arbitrary stdout/stderr fragments."""
    text = collector.stderr.text()
    for signature in (
        "Loopback setup failed",
        "PR_SET_PDEATHSIG failed",
        "PR_SET_NO_NEW_PRIVS failed",
        "unshare failed",
        "Operation not permitted",
    ):
        if signature in text:
            return signature
    if error is not None:
        # Exception messages may contain host paths, environment values or child output.
        # Only fixed facts cross the private report channel, just as for launcher stderr.
        prefix = ""
        if str(error).startswith("boundary entry refused:") and isinstance(
            error.__cause__, Exception
        ):
            prefix, error = "boundary entry refused: ", error.__cause__
        if isinstance(error, TimeoutError):
            return prefix + "launch deadline expired"
        if isinstance(error, OSError) and type(error.errno) is int and 0 < error.errno < 4096:
            return prefix + f"launch OS error (errno {error.errno})"
        for signature in (
            "reviewed build failed",
            "reviewed readiness expired",
            "private backend is unavailable",
            "boundary entry refused",
            "boundary exit is unknown",
            "boundary did not enter",
            "namespace evidence mismatch",
            "incorrect namespace parent",
            "incorrect namespace owner",
            "invalid boundary topology",
        ):
            if str(error) == signature or str(error).startswith(signature + ":"):
                return prefix + signature
        return prefix + "launch exception"
    return "launcher exited" if text else ""


async def watch(
    reader: asyncio.StreamReader, collector: ProcessCollector, deadlines: Deadlines
) -> str:
    line = asyncio.create_task(reader.readline())
    try:
        while True:
            if reason := deadlines.expired():
                return reason
            done, _ = await asyncio.wait(
                {line, collector.exited},
                timeout=max(0, min(deadlines.lease, deadlines.absolute) - deadlines.clock()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if collector.exited in done:
                return "app_exited"  # The private adapter code is classified by the backend.
            if line in done:
                raw = line.result()
                if not raw:
                    return "shutdown"
                if raw == b"stop\n":
                    return "closed"
                value = json.loads(raw)
                deadlines.renew(float(value["renew"]))
                line = asyncio.create_task(reader.readline())
    finally:
        line.cancel()
        await asyncio.gather(line, return_exceptions=True)


def main() -> None:
    if sys.argv[1] == "hold":
        asyncio.run(hold_listener(*map(int, sys.argv[2:5])))
        return
    control, output, listener = map(int, sys.argv[1:4])
    # Configuration is private server-authored JSON in the ownership directory, no model input.
    with contextlib.suppress(BrokenPipeError):
        asyncio.run(supervise(Path(sys.argv[4]), control, output, listener))


class ListenerGuardian:
    """Trusted reservation owner survives supervisor death, never enters T's namespaces."""

    def __init__(self, registry: PreviewRegistry) -> None:
        self.registry = registry
        self.channel: socket.socket | None = None
        self.transport: asyncio.SubprocessTransport | None = None
        self.collector = ProcessCollector(2048)
        self.pidfd: int | None = None

    async def start(self, listener: socket.socket) -> None:
        from oversight.workspace.isolation import open_pidfd

        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        parent.setblocking(False)
        self.channel = parent
        supervisor = open_pidfd(os.getpid())
        try:
            await preserve_ownership(self._spawn(child.fileno(), listener.fileno(), supervisor))
        finally:
            child.close()
            os.close(supervisor)

    async def _spawn(self, control: int, listener: int, supervisor: int) -> None:
        from oversight.workspace.isolation import open_pidfd

        self.transport, _ = await asyncio.get_running_loop().subprocess_exec(
            lambda: self.collector,
            sys.executable,
            "-I",
            "-B",
            str(ENTRY_PATH),
            "hold",
            str(control),
            str(listener),
            str(supervisor),
            env=child_env(None),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            pass_fds=(control, listener, supervisor),
        )
        pid = self.transport.get_pid()
        self.pidfd = open_pidfd(pid)
        await offload(lambda: self.registry.update(guardian=identity_data(pid)))

    async def track(self, pidfds: tuple[int, ...], births: dict[str, Any]) -> None:
        import array

        if self.channel is None:
            raise ValueError("missing listener guardian")
        payload = json.dumps(births, separators=(",", ":")).encode()
        rights = array.array("i", pidfds)
        if self.channel.sendmsg([payload], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, rights)]) != len(
            payload
        ):
            raise ValueError("incomplete listener ownership handoff")
        async with asyncio.timeout(10):
            if await asyncio.get_running_loop().sock_recv(self.channel, 32) != b"tracked":
                raise ValueError("listener ownership handoff refused")

    async def close(self) -> None:
        from oversight.workspace.isolation import verify_preview_namespace_death
        from oversight.workspace.runner import wait_done

        if self.channel is not None:
            self.channel.close()
            self.channel = None
        if self.transport is not None:
            if not await wait_done(self.collector.exited, 10):
                raise ValueError("listener guardian death is unverified")
            if self.pidfd is not None:
                fd, self.pidfd = self.pidfd, None
                await verify_preview_namespace_death(fd)
            self.transport.close()


async def guardian_packet(channel: socket.socket) -> tuple[bytes, list[int]]:
    import array

    loop = asyncio.get_running_loop()
    ready: asyncio.Future[None] = loop.create_future()
    loop.add_reader(channel.fileno(), lambda: ready.set_result(None) if not ready.done() else None)
    try:
        await ready
        raw, ancillary, flags, _ = channel.recvmsg(
            4096, socket.CMSG_SPACE(16 * 4), socket.MSG_CMSG_CLOEXEC
        )
        descriptors: list[int] = []
        for level, kind, data in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                values = array.array("i")
                values.frombytes(data[: len(data) - len(data) % values.itemsize])
                descriptors.extend(values)
        if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
            for fd in descriptors:
                os.close(fd)
            raise ValueError("truncated listener ownership")
        return raw, descriptors
    finally:
        loop.remove_reader(channel.fileno())


async def guardian_death(fd: int) -> None:
    loop = asyncio.get_running_loop()
    ready: asyncio.Future[None] = loop.create_future()
    loop.add_reader(fd, lambda: ready.set_result(None) if not ready.done() else None)
    try:
        await ready
    finally:
        loop.remove_reader(fd)


def guardian_verify(raw: bytes, descriptors: list[int]) -> None:
    from oversight.workspace.preview_registry import registered_identity

    data = json.loads(raw)
    if set(data) != {"outer", "setup", "init"} or len(descriptors) != 3:
        raise ValueError("incomplete listener ownership")
    for name, fd in zip(("outer", "setup", "init"), descriptors, strict=True):
        identity = registered_identity(data[name])
        info = dict(
            row.split(":", 1) for row in Path(f"/proc/self/fdinfo/{fd}").read_text().splitlines()
        )
        if int(info.get("Pid", "-1")) != identity.pid or not identity.alive():
            raise ValueError("stale listener ownership")


async def hold_listener(control: int, listener: int, supervisor: int) -> None:
    """On owner EOF, pinned PID 1 death precedes public-listener release."""
    from oversight.workspace.preview_registry import kill_pinned

    channel, public = socket.socket(fileno=control), socket.socket(fileno=listener)
    channel.setblocking(False)
    owned: list[int] = []
    death = asyncio.create_task(guardian_death(supervisor))
    packet: asyncio.Task[tuple[bytes, list[int]]] | None = None
    try:
        while True:
            packet = asyncio.create_task(guardian_packet(channel))
            done, _ = await asyncio.wait({packet, death}, return_when=asyncio.FIRST_COMPLETED)
            if death in done and packet not in done:
                break
            raw, incoming = await packet
            if not raw:
                for fd in incoming:
                    os.close(fd)
                break
            try:
                guardian_verify(raw, incoming)
                # A previous namespace must be dead before replacing its witnesses.
                await asyncio.gather(*(guardian_death(fd) for fd in owned))
                for fd in owned:
                    os.close(fd)
                owned = incoming
                incoming = []
                channel.send(b"tracked")
            finally:
                for fd in incoming:
                    os.close(fd)
    finally:
        death.cancel()
        if packet is not None:
            packet.cancel()
            await asyncio.gather(packet, return_exceptions=True)
        await asyncio.gather(death, return_exceptions=True)
        # EOF, malformed control or supervisor crash cannot strand a live namespace.
        if owned:
            with contextlib.suppress(ProcessLookupError):
                kill_pinned(owned[2])
            await asyncio.gather(*(guardian_death(fd) for fd in owned))
        for fd in owned:
            os.close(fd)
        public.close()
        channel.close()
        os.close(supervisor)


if __name__ == "__main__":
    main()
