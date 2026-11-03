"""Trusted absolute entry. Never imports worktree code or inherits backend credentials.

Backend death is EOF, not a parent-death kill of this supervisor: it must finish C cleanup.
The existing parent-death launcher instead connects supervisor death to unshare death.
"""

import asyncio
import contextlib
import json
import os
import secrets
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
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
        argv = [*config["argv"], "--ready-nonce", self.nonce]
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
        owner = Supervisor(data["launch"], control, output, listener)
        handed_over = True
        await owner.run(reader, pipe, authorized=True)
    finally:
        if not handed_over:
            pipe.close()
            os.close(listener)
            os.close(output)


def launcher_diagnostic(collector: ProcessCollector) -> str:
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
    control, output, listener = map(int, sys.argv[1:4])
    # Configuration is private server-authored JSON in the ownership directory, no model input.
    with contextlib.suppress(BrokenPipeError):
        asyncio.run(supervise(Path(sys.argv[4]), control, output, listener))


if __name__ == "__main__":
    main()
