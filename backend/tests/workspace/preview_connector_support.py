"""Independent Linux U0/N0/U1 witness, with no tested-tree code or host listener."""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import os
import socket
import struct
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from oversight.workspace.isolation import open_pidfd
from oversight.workspace.runner import ProcessCollector, child_env, run_process, wait_done

SUPPORT = Path(__file__).resolve()


def fd_table() -> Counter[str]:
    descriptors: Counter[str] = Counter()
    for name in os.listdir("/proc/self/fd"):
        with contextlib.suppress(OSError):
            descriptors[os.readlink(f"/proc/self/fd/{name}")] += 1
    return descriptors


async def fd_baseline() -> Counter[str]:
    # Settle unrelated cleanup before the code under test can open any descriptors.
    before = fd_table()
    for _ in range(40):
        await asyncio.sleep(0.025)
        current = fd_table()
        if current == before:
            return current
        before = current
    return before


def new_descriptors(before: Counter[str]) -> Counter[str]:
    return fd_table() - before


def assert_no_new_descriptors(before: Counter[str]) -> None:
    leaked = new_descriptors(before)
    assert not leaked, f"descriptors left open: {dict(leaked)}"


def identities() -> dict[str, str]:
    return {name: os.readlink(f"/proc/self/ns/{name}") for name in ("user", "net", "pid", "mnt")}


def loopback(up: bool) -> None:
    with socket.socket() as control:
        flags = struct.unpack(
            "16sH22x", fcntl.ioctl(control, 0x8913, struct.pack("16sH22x", b"lo", 0))
        )[1]
        fcntl.ioctl(control, 0x8914, struct.pack("16sH22x", b"lo", flags | 1 if up else flags & ~1))


async def u1(descriptors: dict[str, str]) -> None:
    before = identities()
    try:
        loopback(False)
    except OSError as error:
        down = {"errno": error.errno, "error": str(error)}
    else:
        down = {"errno": 0, "error": "loopback modification succeeded"}
    route = await run_process(
        ["/usr/sbin/ip", "route", "add", "198.51.100.0/24", "dev", "lo"],
        cwd=Path("/tmp"),
        timeout_s=3,
    )
    print(
        json.dumps(
            {
                "before": before,
                "after": identities(),
                "down": down,
                "route_code": route.returncode,
                "route_error": route.stderr,
                "descriptors": descriptors,
            }
        ),
        flush=True,
    )


async def outer() -> None:
    os.unshare(os.CLONE_NEWNET)
    loopback(True)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        listener.setblocking(False)
        loop = asyncio.get_running_loop()
        # A private connection exists before the U1 tamper attempts.
        with socket.socket() as initial:
            initial.setblocking(False)
            await loop.sock_connect(initial, listener.getsockname())
            peer, _ = await loop.sock_accept(listener)
            peer.close()
        result = await run_process(
            [
                "/usr/bin/unshare",
                "--user",
                "--map-root-user",
                "--mount",
                "--pid",
                "--fork",
                "--kill-child=SIGKILL",
                "--mount-proc",
                sys.executable,
                "-I",
                "-B",
                str(SUPPORT),
                "u1",
            ],
            cwd=Path("/tmp"),
            timeout_s=5,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr)
        print(
            json.dumps(
                {
                    "outer": identities(),
                    "u1": json.loads(result.stdout),
                    "port": listener.getsockname()[1],
                }
            ),
            flush=True,
        )
        async with asyncio.timeout(45):
            while True:
                peer, _ = await loop.sock_accept(listener)
                with peer:
                    await loop.sock_sendall(peer, b"private-api")


class WitnessCollector(ProcessCollector):
    def __init__(self) -> None:
        super().__init__(8192)
        self.witness: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self.buffer = bytearray()

    def pipe_data_received(self, fd: int, data: bytes) -> None:
        super().pipe_data_received(fd, data)
        if fd == 1 and not self.witness.done():
            self.buffer.extend(data)
            if b"\n" in self.buffer:
                try:
                    self.witness.set_result(json.loads(self.buffer.split(b"\n")[0]))
                except ValueError as error:
                    self.witness.set_exception(error)


class Harness:
    def __init__(self) -> None:
        self.collector = WitnessCollector()
        self.transport: asyncio.SubprocessTransport | None = None
        self.pidfd = -1
        self.witness: dict[str, Any] = {}

    async def start(self) -> Harness:
        self.transport, _ = await asyncio.get_running_loop().subprocess_exec(
            lambda: self.collector,
            "/usr/bin/unshare",
            "--user",
            "--map-root-user",
            sys.executable,
            "-I",
            "-B",
            str(SUPPORT),
            "outer",
            cwd=SUPPORT.parent,
            env=child_env(None),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            pass_fds=(),
        )
        done, _ = await asyncio.wait(
            {self.collector.witness, self.collector.exited},
            timeout=12,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if self.collector.witness not in done:
            raise RuntimeError("U0/N0/U1 gate unavailable: " + self.collector.stderr.text())
        self.witness = self.collector.witness.result()
        self.pidfd = open_pidfd(self.transport.get_pid())
        return self

    async def stop(self) -> None:
        if self.pidfd >= 0:
            os.close(self.pidfd)
            self.pidfd = -1
        if self.transport is not None:
            if self.transport.get_returncode() is None:
                with contextlib.suppress(ProcessLookupError):
                    self.transport.kill()
            assert await wait_done(self.collector.exited, 5), "harness death unverified"
            self.transport.close()


def helper_launch(
    monkeypatch, mode: str, *, delay_spawn=False, hold_spawn=False, connect_port=None
):
    loop = asyncio.get_running_loop()
    original = loop.subprocess_exec
    transports = []
    spawning = asyncio.Event()
    release = asyncio.Event()
    started = asyncio.Event()

    async def spawn(factory, *argv, **kwargs):
        spawning.set()
        if delay_spawn:
            await release.wait()
        arguments = list(argv[-4:])
        if connect_port is not None:
            arguments[2] = str(connect_port)
        transport, protocol = await original(
            factory,
            argv[0],
            "-I",
            "-B",
            str(SUPPORT),
            "failure-helper",
            mode,
            *arguments,
            **kwargs,
        )
        transports.append(transport)
        started.set()
        if hold_spawn:
            await release.wait()
        return transport, protocol

    monkeypatch.setattr(loop, "subprocess_exec", spawn)
    return transports, spawning, release, started


def inherited_descriptors() -> dict[str, str]:
    # Inspect before asyncio creates its own wakeup socketpair.
    descriptors: dict[str, str] = {}
    for name in os.listdir("/proc/self/fd"):
        with contextlib.suppress(FileNotFoundError):
            descriptors[name] = os.readlink(f"/proc/self/fd/{name}")
    return descriptors


async def inherited_command(mode: str, descriptors: tuple[int, ...]) -> tuple[int | None, str, str]:
    collector = ProcessCollector(8192)
    transport, _ = await asyncio.get_running_loop().subprocess_exec(
        lambda: collector,
        sys.executable,
        "-I",
        "-B",
        str(SUPPORT),
        mode,
        *(str(fd) for fd in descriptors),
        cwd=SUPPORT.parent,
        env=child_env(None),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        pass_fds=descriptors,
    )
    try:
        if not await wait_done(collector.exited, 5):
            raise TimeoutError("witness process did not exit")
        await wait_done(collector.closed, 2)
        return transport.get_returncode(), collector.stdout.text(), collector.stderr.text()
    finally:
        if transport.get_returncode() is None:
            transport.kill()
            assert await wait_done(collector.exited, 5)
        transport.close()


def reversed_entry(fd: int) -> None:
    before = identities()
    try:
        os.setns(fd, os.CLONE_NEWNET)
    except OSError as error:
        print(
            json.dumps(
                {"before": before, "after": identities(), "errno": error.errno, "error": str(error)}
            ),
            flush=True,
        )
    else:
        raise AssertionError("N0 before U0 unexpectedly succeeded")


async def failure_helper(mode: str, args: list[str]) -> None:
    import array

    _, _, port, fd = map(int, args)
    with socket.socket(fileno=fd) as channel:
        if mode == "death":
            return
        if mode == "setup":
            raise RuntimeError("injected setup failure")
        if mode == "hang":
            await asyncio.Event().wait()
        membership = {"before": identities(), "after": identities()}
        if mode.startswith("wrong-") and mode != "wrong-port":
            membership["after"][mode.removeprefix("wrong-")] = "changed"
        witness = json.dumps(membership).encode()
        with socket.socket() as client, socket.socket() as extra:
            if mode in ("extra", "malformed", "wrong-port", "hang-after", "fail-after"):
                client.setblocking(False)
                await asyncio.get_running_loop().sock_connect(client, ("127.0.0.1", port))
            if mode == "listener":
                client.bind(("127.0.0.1", 0))
                client.listen()
            if mode == "unix":
                client.close()
                client = socket.socket(socket.AF_UNIX)
            file_fd = -1
            if mode == "file":
                file_fd = os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC)
            descriptor = file_fd if file_fd >= 0 else client.fileno()
            values = [descriptor] * (32 if mode == "overflow" else 1)
            if mode == "extra":
                values.append(extra.fileno())
            payload = b"invalid-json" if mode == "malformed" else witness
            if mode == "bad-shape":
                payload = b"{}"
            if mode == "truncated":
                payload = b"x" * 8192
            ancillary = (
                []
                if mode == "no-fd"
                else [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", values))]
            )
            try:
                channel.sendmsg([payload], ancillary)
                if mode == "hang-after":
                    await asyncio.Event().wait()
                if mode == "fail-after":
                    raise RuntimeError("injected failure after handoff")
            finally:
                if file_fd >= 0:
                    os.close(file_fd)
                client.close()


def dispatch() -> None:
    inherited = inherited_descriptors()
    mode = sys.argv[1]
    if mode == "reverse":
        reversed_entry(int(sys.argv[2]))
    elif mode == "failure-helper":
        asyncio.run(failure_helper(sys.argv[2], sys.argv[3:]))
    else:
        asyncio.run(outer() if mode == "outer" else u1(inherited))


if __name__ == "__main__":
    dispatch()
