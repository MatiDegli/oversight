"""Trusted single-connection entry. No tested-tree code or configurable destination."""

from __future__ import annotations

import array
import asyncio
import fcntl
import json
import os
import select
import socket
import sys
from pathlib import Path

NS_GET_USERNS = 0xB701
NAMES = ("user", "net", "pid", "mnt")


def namespaces(pid: int | str = "self") -> dict[str, str]:
    return {name: os.readlink(f"/proc/{pid}/ns/{name}") for name in NAMES}


def pidfd_pid(fd: int) -> int:
    """Reject ordinary descriptors and exited processes, including unreaped zombies."""
    if type(fd) is not int or fd < 0:
        raise ValueError("expected a live pidfd")
    if os.readlink(f"/proc/self/fd/{fd}") != "anon_inode:[pidfd]":
        raise ValueError("expected a pidfd")
    rows = Path(f"/proc/self/fdinfo/{fd}").read_text().splitlines()
    pid = next(int(row.split()[1]) for row in rows if row.startswith("Pid:"))
    if pid <= 0:
        raise ValueError("stale pidfd")
    # pidfd_send_signal(0) can succeed for a zombie; readable pidfds are dead.
    poll = select.poll()
    poll.register(fd, select.POLLIN)
    if poll.poll(0):
        raise ValueError("exited pidfd")
    return pid


def target_identity(u0: int, n0: int) -> tuple[str, str]:
    """Pin the U0/N0 relationship; N0 must be owned by this distinct U0."""
    upid, npid = pidfd_pid(u0), pidfd_pid(n0)
    user = os.open(f"/proc/{upid}/ns/user", os.O_RDONLY | os.O_CLOEXEC)
    try:
        net = os.open(f"/proc/{npid}/ns/net", os.O_RDONLY | os.O_CLOEXEC)
        try:
            owner = fcntl.ioctl(net, NS_GET_USERNS)
            try:
                if (os.fstat(owner).st_dev, os.fstat(owner).st_ino) != (
                    os.fstat(user).st_dev,
                    os.fstat(user).st_ino,
                ):
                    raise ValueError("N0 is not owned by U0")
            finally:
                os.close(owner)
            identity = (os.readlink(f"/proc/self/fd/{user}"), os.readlink(f"/proc/self/fd/{net}"))
        finally:
            os.close(net)
        current = namespaces()
        if identity[0] == current["user"] or identity[1] == current["net"]:
            raise ValueError("target must use private U0 and N0")
        if namespaces(npid)["user"] != identity[0]:
            raise ValueError("N0 process must remain outside U1")
        pidfd_pid(u0)
        pidfd_pid(n0)
        return identity
    finally:
        os.close(user)


def enter(u0: int, n0: int, identity: tuple[str, str]) -> dict[str, dict[str, str]]:
    before = namespaces()
    os.setns(u0, os.CLONE_NEWUSER)
    os.setns(n0, os.CLONE_NEWNET)
    after = namespaces()
    if (after["user"], after["net"]) != identity:
        raise ValueError("namespace identities changed during entry")
    if any(after[name] != before[name] for name in ("pid", "mnt")):
        raise ValueError("connector entered an unexpected PID or mount namespace")
    return {"before": before, "after": after}


async def deliver(control: int, port: int, witness: dict[str, dict[str, str]]) -> None:
    with socket.socket(fileno=control) as channel, socket.socket() as client:
        channel.setblocking(False)
        client.setblocking(False)
        loop = asyncio.get_running_loop()
        async with asyncio.timeout(5):
            await loop.sock_connect(client, ("127.0.0.1", port))
            payload = json.dumps(witness, separators=(",", ":")).encode()
            ready: asyncio.Future[None] = loop.create_future()

            def writable() -> None:
                if not ready.done():
                    ready.set_result(None)

            loop.add_writer(channel.fileno(), writable)
            try:
                await ready
                sent = channel.sendmsg(
                    [payload],
                    [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [client.fileno()]))],
                )
                if sent != len(payload):
                    raise OSError("incomplete socket handoff")
            finally:
                loop.remove_writer(channel.fileno())


def main() -> None:
    if len(sys.argv) != 5:
        raise ValueError("expected only U0/N0 pidfds, API port and handoff socket")
    u0, n0, port, control = map(int, sys.argv[1:])
    if not 1 <= port <= 65535:
        raise ValueError("invalid API port")
    identity = target_identity(u0, n0)
    witness = enter(u0, n0, identity)
    os.close(u0)
    os.close(n0)
    asyncio.run(deliver(control, port, witness))


if __name__ == "__main__":
    main()
