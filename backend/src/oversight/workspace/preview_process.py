"""Async backend handoff, inherited listener, bounded private HTTP readiness and death proof."""

import asyncio
import contextlib
import json
import os
import re
import socket
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from oversight.workspace.isolation import (
    open_pidfd,
    verify_preview_namespace_death,
)
from oversight.workspace.locks import offload
from oversight.workspace.preview_profile import PreviewProfile
from oversight.workspace.preview_registry import (
    PreviewRegistry,
    identity_data,
    registered_identity,
)
from oversight.workspace.preview_types import PreviewError, preserve_ownership
from oversight.workspace.runner import ProcessCollector, child_env, wait_done

SUPERVISOR_PATH = Path(__file__).resolve().with_name("preview_supervisor.py")


def bind_listener(ports: tuple[int, ...]) -> socket.socket:
    """Unknown listeners remove availability; never find or kill their owners."""
    for port in ports:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", port))
            listener.listen(16)
            return listener
        except OSError:
            listener.close()
    raise PreviewError("Live preview has no available configured port.")


def launch_configuration(
    profile: PreviewProfile,
    runtime: Path,
    worktree: Path,
    scratch: Path,
    listener: socket.socket,
    refusal: socket.socket,
    now: float,
    *,
    parent_authority: str | None = None,
) -> dict[str, Any]:
    argv: list[str] = []
    if profile.reviewed_app is None:
        argv = [
            str(profile.interpreter),
            "-I",
            "-B",
            str(profile.adapter),
            "--listener-fd",
            str(listener.fileno()),
            "--port",
            str(listener.getsockname()[1]),
            "--worktree",
            str(worktree),
            "--memory-limit-bytes",
            str(profile.memory_limit_bytes),
        ]
        origins = profile.origins
        if parent_authority is not None:
            from oversight.workspace._reviewed_preview import opposite_host

            frame = opposite_host(parent_authority.split(":")[0])
            argv.extend(("--frame-host", frame))
            origins = ("http://" + parent_authority,)
        for origin in origins:
            argv.extend(("--oversight-origin", origin))
    return {
        "runtime": str(runtime),
        **({"argv": argv} if argv else {}),
        "scratch": str(scratch),
        "env": {key: str(scratch) for key in ("HOME", "TMP", "TEMP")},
        "refusal_port": refusal.getsockname()[1],
        "readiness_s": profile.readiness_s,
        "lease_deadline": now + profile.lease_s,
        "absolute_deadline": now + profile.lifetime_s,
    }


async def probe_ready(
    port: int, nonce: str, authority: str | None = None, *, reviewed: bool = False
) -> bool:
    """No Origin or token; no redirects, proxy discovery, compression or unbounded bodies."""
    writer: asyncio.StreamWriter | None = None
    try:
        async with asyncio.timeout(1):
            reader, writer = await asyncio.open_connection("127.0.0.1", port, limit=2048)
            writer.write(
                f"GET /_oversight_ready HTTP/1.1\r\nHost: {authority or f'127.0.0.1:{port}'}\r\n"
                "Connection: close\r\n\r\n".encode()
            )
            await writer.drain()
            header = await reader.readuntil(b"\r\n\r\n")
            lines = header.decode("ascii").split("\r\n")
            expected = (
                {"HTTP/1.1 200 Response"} if reviewed else {"HTTP/1.1 200 OK", "HTTP/1.0 200 OK"}
            )
            if lines[0] not in expected:
                return False
            fields = [line.split(":", 1) for line in lines[1:] if line]
            names = [key.lower() for key, _ in fields]
            if len(set(names)) != len(names):
                return False
            headers = {key.lower(): value.strip() for key, value in fields}
            if "transfer-encoding" in headers or "content-encoding" in headers:
                return False
            length = int(headers.get("content-length", "-1"))
            if headers.get("content-type") != "application/json" or not 0 < length <= 256:
                return False
            body = await reader.readexactly(length)
            return json.loads(body, object_pairs_hook=list) == [("nonce", nonce)]
    except (
        OSError,
        ValueError,
        TimeoutError,
        asyncio.IncompleteReadError,
        asyncio.LimitOverrunError,
    ):
        return False
    finally:
        if writer is not None:
            writer.close()
            with contextlib.suppress(OSError):
                async with asyncio.timeout(1):
                    await writer.wait_closed()


class PreviewProcess:
    """Backend descriptors never reach the adapter. Application output is never consumed here."""

    def __init__(self, registry: PreviewRegistry, port: int) -> None:
        self.registry = registry
        self.port = port
        self.public_authority = f"127.0.0.1:{port}"
        self.reviewed = False
        self.stage = "build"
        self.control = -1
        self.pidfd: int | None = None
        self.transport: asyncio.SubprocessTransport | None = None
        self.collector = ProcessCollector(0)
        self.reader: asyncio.StreamReader | None = None
        self.pipe: asyncio.ReadTransport | None = None
        self.nonce: str | None = None
        self.dead = False
        self.exit_code: int | None = None
        self.reason = "launch_failed"
        self.diagnostic = ""
        self.reported_dead = False
        self.authorize_execution: Callable[[], None] = lambda: None

    async def launch(self, listener: socket.socket, profile: PreviewProfile) -> None:
        self.reviewed = profile.reviewed_app is not None
        allowance = profile.readiness_s
        if profile.reviewed_app is not None:
            allowance += sum(c.timeout_s for c in profile.reviewed_app.launch.build)
        try:
            async with asyncio.timeout(allowance):
                await self._handoff(listener, profile)
        except TimeoutError:
            if not self.reviewed:
                raise
            raise PreviewError("Live preview launch failed.") from None

    async def _handoff(self, listener: socket.socket, profile: PreviewProfile) -> None:
        read_control = read_report = write_report = -1
        try:
            read_control, self.control = os.pipe2(os.O_CLOEXEC)
            os.set_blocking(self.control, False)
            read_report, write_report = os.pipe2(os.O_CLOEXEC)
            await preserve_ownership(self._spawn(read_control, write_report, listener.fileno()))
            os.close(read_control)
            read_control = -1
            os.close(write_report)
            write_report = -1
            listener.close()
            self.send(b"launch\n")
            # Import locally to keep the trusted supervisor entry independent of this client.
            from oversight.workspace.preview_supervisor import control_reader

            self.reader, self.pipe = await control_reader(read_report)
            read_report = -1
            allowance = profile.readiness_s
            if profile.reviewed_app is not None:
                allowance += sum(c.timeout_s for c in profile.reviewed_app.launch.build)
            async with asyncio.timeout(allowance):
                confirmed = await self.message()
                if confirmed.get("kind") != "confirmed":
                    self.accept_death(confirmed)
                    raise PreviewError("Live preview launch failed.")
                nonce = confirmed.get("nonce")
                if self.reviewed and nonce is not None:
                    raise PreviewError("Live preview readiness was released before probing.")
                if not self.reviewed and (
                    not isinstance(nonce, str) or re.fullmatch(r"[0-9a-f]{64}", nonce) is None
                ):
                    raise PreviewError("Live preview launch failed.")
                self.nonce = nonce if isinstance(nonce, str) else None
                data = await offload(self.registry.read)
                if data is None:
                    raise PreviewError()
                identity = registered_identity(data["namespace"])
                self.pidfd = open_pidfd(identity.pid)
                if not await self.confirmed_live():
                    raise PreviewError("Live preview launch failed.")
                self.authorize_execution()
                self.send(b"execute\n")
                executing = await self.message()
                if executing.get("kind") != "executing":
                    self.accept_death(executing)
                    raise PreviewError("Live preview launch failed.")
        except BaseException:
            if self.control >= 0:
                os.close(self.control)
                self.control = -1
            raise
        finally:
            listener.close()
            for fd in (read_control, write_report, read_report):
                if fd >= 0:
                    os.close(fd)

    async def _spawn(self, control: int, report: int, listener: int) -> None:
        self.transport, _ = await asyncio.get_running_loop().subprocess_exec(
            lambda: self.collector,
            sys.executable,
            "-I",
            "-B",
            str(SUPERVISOR_PATH),
            str(control),
            str(report),
            str(listener),
            str(self.registry.runtime),
            cwd=self.registry.directory,
            env=child_env(None),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            pass_fds=(control, report, listener),
        )
        assert self.transport is not None
        pid = self.transport.get_pid()
        await offload(lambda: self.registry.update(supervisor=identity_data(pid), phase="owned"))

    def send(self, message: bytes) -> None:
        if self.control < 0:
            raise PreviewError("Live preview owner is unavailable.")
        try:
            if os.write(self.control, message) != len(message):
                raise OSError("incomplete control write")
        except OSError:
            raise PreviewError("Live preview owner is unavailable.") from None

    async def message(self) -> dict[str, Any]:
        if self.reader is None:
            raise PreviewError("Live preview owner is unavailable.")
        raw = await self.reader.readline()
        if not raw or len(raw) > 4096:
            raise PreviewError("Live preview owner is unavailable.")
        return json.loads(raw)

    async def confirmed_live(self) -> bool:
        data = await offload(self.registry.read)
        if data is None or (data["phase"] != "executing" and data["phase"] != "confirmed"):
            return False
        for key in ("supervisor", "unshare", "namespace"):
            if not registered_identity(data[key]).alive():
                return False
        if data.get("boundary"):
            if not data.get("guardian") or not registered_identity(data["guardian"]).alive():
                return False
            if not all(
                registered_identity(raw).alive() for raw in data["boundary"]["processes"].values()
            ):
                return False
        pid = data["namespace"]["pid"]
        return all(
            os.readlink(f"/proc/{pid}/ns/{name}") == value
            for name, value in data["namespaces"].items()
        )

    async def ready(self, within_s: float) -> bool:
        if self.reviewed:
            return await self._reviewed_ready(within_s)
        deadline = time.monotonic() + within_s
        try:
            async with asyncio.timeout(within_s):
                while time.monotonic() < deadline:
                    if not await self.confirmed_live():
                        return False
                    if self.nonce and await probe_ready(
                        self.port, self.nonce, self.public_authority
                    ):
                        return await self.confirmed_live()
                    await asyncio.sleep(min(0.05, max(0, deadline - time.monotonic())))
        except TimeoutError:
            return False
        return False

    async def _reviewed_ready(self, within_s: float) -> bool:
        try:
            async with asyncio.timeout(within_s):
                message = await self.message()
                while message.get("kind") == "probing":
                    self.stage = "readiness"
                    message = await self.message()
                if message.get("kind") != "ready":
                    self.accept_death(message)
                    return False
                nonce = message.get("nonce")
                if not isinstance(nonce, str) or re.fullmatch(r"[0-9a-f]{64}", nonce) is None:
                    return False
                self.nonce = nonce
                return await self.confirmed_live() and await probe_ready(
                    self.port, nonce, self.public_authority, reviewed=True
                )
        except TimeoutError:
            return False

    def accept_death(self, message: dict[str, Any]) -> None:
        if message.get("kind") != "dead":
            raise PreviewError("Live preview cleanup is unverified; restart is required.")
        self.reason = message["reason"]
        self.exit_code = message.get("exit_code")
        self.diagnostic = message.get("diagnostic", "")
        self.reported_dead = True

    async def stop(self) -> None:
        if self.dead:
            return
        if self.control >= 0:
            with contextlib.suppress(OSError, PreviewError):
                self.send(b"stop\n")
            os.close(self.control)
            self.control = -1
        try:
            async with asyncio.timeout(15):
                if self.reader is not None and not self.reported_dead:
                    # Cancellation can interrupt either handoff acknowledgement. The
                    # launch reader has settled before stop takes sole channel ownership.
                    while not self.reported_dead:
                        message = await self.message()
                        if message.get("kind") not in {
                            "confirmed",
                            "executing",
                            "probing",
                            "ready",
                        }:
                            self.accept_death(message)
                if self.transport is not None and not await wait_done(self.collector.exited, 8):
                    raise PreviewError()
                if self.pidfd is not None:
                    fd, self.pidfd = self.pidfd, None
                    await verify_preview_namespace_death(fd)
                data = await offload(self.registry.read)
                if data is not None and self.transport is None and data["phase"] == "intent":
                    await offload(lambda: self.registry.update(phase="dead"))
                    data = await offload(self.registry.read)
                if data is None or data["phase"] != "dead":
                    raise PreviewError()
                for key in ("namespace", "unshare", "supervisor", "guardian"):
                    if data.get(key) and registered_identity(data[key]).alive():
                        raise PreviewError()
                self.dead = True
        finally:
            if self.pipe is not None:
                self.pipe.close()
            if self.transport is not None:
                self.transport.close()
