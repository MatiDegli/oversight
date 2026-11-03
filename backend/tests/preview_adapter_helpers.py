"""Bounded inherited-socket harness; process exit proves only local descriptor closure."""

import asyncio
import os
import shutil
import socket
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from oversight import preview_adapter as adapter

ADAPTER = Path(adapter.__file__).resolve()
ROOT = ADAPTER.parents[3]
ORIGIN = "http://localhost:8710"
READY = "a" * 64


class Process(asyncio.SubprocessProtocol):
    def __init__(self) -> None:
        self.done: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self.output = {1: bytearray(), 2: bytearray()}

    def pipe_data_received(self, fd: int, data: bytes) -> None:
        self.output[fd].extend(data[: max(0, 8192 - len(self.output[fd]))])

    def connection_lost(self, exc: Exception | None) -> None:
        if not self.done.done():
            self.done.set_result(None)


@dataclass
class Reply:
    status: int
    headers: dict[str, str]
    body: bytes

    def policy(self, origin: str = ORIGIN) -> None:
        assert self.headers["content-security-policy"] == (
            "default-src 'none'; form-action 'self'; base-uri 'none'; frame-ancestors "
            + origin
            + "; sandbox allow-forms"
        )
        assert self.headers["referrer-policy"] == "no-referrer"
        assert self.headers["cache-control"] == "no-store"
        assert "set-cookie" not in self.headers and "location" not in self.headers
        assert not any(key.startswith("access-control") for key in self.headers)


@dataclass
class Child:
    transport: asyncio.SubprocessTransport
    protocol: Process
    port: int
    origin: str = ORIGIN
    frame_host: str = "127.0.0.1"

    async def exit(self) -> int | None:
        await asyncio.wait_for(asyncio.shield(self.protocol.done), 8)
        assert not self.protocol.output[1] and not self.protocol.output[2]
        return self.transport.get_returncode()

    async def request(
        self,
        method: str = "GET",
        path: str = "/",
        body: bytes = b"",
        headers: list[tuple[str, str]] | None = None,
    ) -> Reply:
        fields = headers if headers is not None else [("Host", f"{self.frame_host}:{self.port}")]
        raw = f"{method} {path} HTTP/1.1\r\n".encode()
        raw += b"".join(f"{k}: {v}\r\n".encode() for k, v in fields) + b"\r\n" + body
        async with asyncio.timeout(6):
            reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
            try:
                writer.write(raw)
                await writer.drain()
                chunks = bytearray()
                while chunk := await reader.read(8192):
                    chunks.extend(chunk)
                    assert len(chunks) <= 100_000
                result = bytes(chunks)
            finally:
                writer.close()
                await writer.wait_closed()
        head, content = result.split(b"\r\n\r\n", 1)
        lines = head.decode().split("\r\n")
        reply = Reply(
            int(lines[0].split()[1]),
            dict(line.lower().split(": ", 1) for line in lines[1:]),
            content,
        )
        reply.policy(self.origin)
        return reply

    async def ready(self) -> None:
        async with asyncio.timeout(8):
            while True:
                try:
                    response = await self.request(path="/_oversight_ready")
                    assert response.body == ('{"nonce": "' + READY + '"}').encode()
                    return
                except (ConnectionError, OSError):
                    if self.transport.get_returncode() is not None:
                        raise AssertionError("adapter exited before readiness") from None
                    await asyncio.sleep(0.02)


def copy_shop(tmp_path: Path) -> Path:
    target = tmp_path / "candidate"
    shutil.copytree(ROOT / "demo-repo/shop", target / "shop")
    return target


@asynccontextmanager
async def launched(
    tmp_path: Path,
    *,
    root: Path | None = None,
    setup: str = "",
    listener: socket.socket | None = None,
    fd: int | None = None,
    extra: tuple[str, ...] = (),
    ready: bool = True,
    interpreter: str = sys.executable,
    origin: str = ORIGIN,
    frame_host: str = "127.0.0.1",
):
    tree = root or copy_shop(tmp_path)
    sock = listener or socket.socket()
    if listener is None:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
    port = (sock.getsockname()[1] or 12345) if sock.family == socket.AF_INET else 12345
    inherited = sock.fileno() if fd is None else fd
    argv = [interpreter, "-I", "-B", str(ADAPTER)]
    if setup:
        bootstrap = tmp_path / "bootstrap.py"
        bootstrap.write_text(
            "import runpy, sys, socket, os, resource, types\n"
            + setup
            + "\n"
            + f"runpy.run_path({str(ADAPTER)!r}, run_name='__main__')\n"
        )
        argv[-1] = str(bootstrap)
    argv += [
        "--listener-fd",
        str(inherited),
        "--port",
        str(port),
        "--worktree",
        str(tree),
        "--ready-nonce",
        READY,
        "--oversight-origin",
        origin,
        *extra,
    ]
    loop = asyncio.get_running_loop()
    transport, protocol = await asyncio.wait_for(
        loop.subprocess_exec(
            Process,
            *argv,
            pass_fds=(inherited,) if inherited >= 3 else (),
            cwd=tmp_path,
            env={"PATH": os.defpath, "HOME": str(tmp_path), "LANG": "C.UTF-8"},
        ),
        8,
    )
    sock.close()  # The child owns the only listening copy after handoff.
    child = Child(transport, protocol, port, origin, frame_host)
    try:
        if ready:
            await child.ready()
        yield child
    finally:
        if transport.get_returncode() is None:
            transport.terminate()
        try:
            await asyncio.wait_for(asyncio.shield(protocol.done), 2)
        except TimeoutError:
            transport.kill()
            await asyncio.wait_for(asyncio.shield(protocol.done), 2)
        transport.close()
