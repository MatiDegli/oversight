"""Connector refusal, ownership and real namespace gates."""

import asyncio
import dataclasses
import os
import socket
from collections import Counter
from typing import Any, cast

import pytest

from oversight.workspace.isolation import open_pidfd
from oversight.workspace.preview_connector import ConnectorTarget, connect_api

from .preview_connector_support import (
    assert_no_new_descriptors,
    fd_baseline,
    helper_launch,
    new_descriptors,
)

pytestmark = pytest.mark.anyio


async def test_connector_target_identity_and_destination_refusals() -> None:
    with socket.socket() as wrong:
        for port in (True, False, 0, -1, 65536, 1.5, "80", None):
            with pytest.raises((TypeError, ValueError, OSError)):
                ConnectorTarget(wrong.fileno(), wrong.fileno(), cast(Any, port))
        with pytest.raises((ValueError, OSError)):
            ConnectorTarget(wrong.fileno(), wrong.fileno(), 8080)
    with pytest.raises((ValueError, OSError)):
        ConnectorTarget(-1, -1, 8080)
    for key in ("hostname", "url", "route", "namespace_handle"):
        with pytest.raises(TypeError):
            cast(Any, ConnectorTarget)(1, 2, 80, **{key: "untrusted"})
    with pytest.raises(TypeError):
        await connect_api(cast(Any, object()), timeout_s=1)
    with pytest.raises(TypeError, match="malformed"):
        await connect_api(object.__new__(ConnectorTarget), timeout_s=1)
    assert [field.name for field in dataclasses.fields(ConnectorTarget) if field.init] == [
        "u0_pidfd",
        "n0_pidfd",
        "api_port",
    ]
    fd = open_pidfd(os.getpid())
    try:
        with pytest.raises((ValueError, OSError)):
            ConnectorTarget(fd, fd, 8080)
    finally:
        os.close(fd)


@pytest.fixture
async def harness():
    from .preview_connector_support import Harness

    instance = Harness()
    try:
        try:
            await instance.start()
        except RuntimeError as error:
            pytest.skip(str(error))
        yield instance
    finally:
        await instance.stop()


async def test_connector_real_unprivileged_setns_order_and_membership(harness, monkeypatch) -> None:
    from oversight.workspace import preview_connector as connector
    from oversight.workspace.preview_connector_entry import namespaces, pidfd_pid

    recorded = []
    original = connector._namespace_witness

    def witness(data):
        before, after = original(data)
        recorded.append({"before": before, "after": after})
        return before, after

    monkeypatch.setattr(connector, "_namespace_witness", witness)

    before = namespaces()
    target = ConnectorTarget(harness.pidfd, harness.pidfd, harness.witness["port"])
    client = await connect_api(target, timeout_s=5)
    with client:
        assert not client.getblocking()
        assert await asyncio.get_running_loop().sock_recv(client, 32) == b"private-api"
    assert namespaces() == before
    assert harness.witness["outer"]["user"] != before["user"]
    os.fstat(harness.pidfd)
    assert recorded[0]["before"] == before
    after = recorded[0]["after"]
    assert all(after[name] == harness.witness["outer"][name] for name in ("user", "net"))
    assert all(after[name] == before[name] for name in ("pid", "mnt"))
    print(
        "NAMESPACE_WITNESS",
        {
            **harness.witness,
            "connector": recorded[0],
            "pidfd": harness.pidfd,
            "pidfd_pid": pidfd_pid(harness.pidfd),
        },
    )


async def test_u1_cannot_modify_n0_network(harness) -> None:
    import errno

    outer, u1 = harness.witness["outer"], harness.witness["u1"]
    assert u1["before"] == u1["after"]
    assert u1["after"]["user"] != outer["user"]
    assert u1["after"]["net"] == outer["net"]
    assert all(u1["after"][name] != outer[name] for name in ("pid", "mnt"))
    assert u1["down"]["errno"] == errno.EPERM
    assert u1["route_code"] != 0 and "Operation not permitted" in u1["route_error"]
    standard = {"0", "1", "2"}
    assert standard <= set(u1["descriptors"])
    assert all(v == "/dev/urandom" for k, v in u1["descriptors"].items() if k not in standard)
    assert u1["descriptors"]["0"] == "/dev/null"
    assert all(
        value.startswith("pipe:") for key, value in u1["descriptors"].items() if key in {"1", "2"}
    )
    target = ConnectorTarget(harness.pidfd, harness.pidfd, harness.witness["port"])
    with await connect_api(target, timeout_s=5) as client:
        assert await asyncio.get_running_loop().sock_recv(client, 32) == b"private-api"
    print("U1_DENIAL_WITNESS", harness.witness)


async def test_reversed_entry_is_refused(harness) -> None:
    import errno
    import json

    from .preview_connector_support import inherited_command

    code, output, error = await inherited_command("reverse", (harness.pidfd,))
    assert code == 0, error
    witness = json.loads(output)
    assert witness["errno"] == errno.EPERM
    assert witness["before"] == witness["after"]
    print("REVERSED_ORDER_WITNESS", witness)


async def test_target_freezes_identity_and_preserves_borrowed_handles(harness) -> None:
    target = ConnectorTarget(harness.pidfd, harness.pidfd, harness.witness["port"])
    with pytest.raises(dataclasses.FrozenInstanceError):
        cast(Any, target).api_port = 9
    with pytest.raises(TypeError):
        await cast(Any, connect_api)(target, timeout_s=1, hostname="example.com")
    for timeout in (True, 0, -1, float("nan"), float("inf"), "5"):
        with pytest.raises(ValueError):
            await connect_api(target, timeout_s=cast(Any, timeout))
    fd = open_pidfd(os.getpid())
    try:
        with pytest.raises((ValueError, OSError)):
            ConnectorTarget(harness.pidfd, fd, target.api_port)
    finally:
        os.close(fd)
    duplicated = os.dup(harness.pidfd)
    stale = ConnectorTarget(duplicated, duplicated, target.api_port)
    os.close(duplicated)
    with pytest.raises(OSError):
        await connect_api(stale, timeout_s=1)
    os.fstat(harness.pidfd)


async def test_connector_import_has_no_launch_side_effects(tmp_path) -> None:
    import sys

    from oversight.workspace.runner import run_process

    code = """
import asyncio, socket, oversight.workspace

def refused(*args, **kwargs):
    raise AssertionError('import performed I/O or spawned a process')
socket.socket = refused
asyncio.BaseEventLoop.subprocess_exec = refused
import oversight.workspace.preview_connector
import oversight.workspace.preview_connector_entry
"""
    result = await run_process([sys.executable, "-I", "-c", code], cwd=tmp_path, timeout_s=5)
    assert result.returncode == 0, result.stderr


@pytest.fixture
async def fake_target(monkeypatch):
    """Failure injection only; successful namespace evidence uses the real harness."""
    from oversight.workspace import preview_connector as connector
    from oversight.workspace.preview_connector_entry import namespaces

    identities = namespaces()
    monkeypatch.setattr(
        connector, "target_identity", lambda *_: (identities["user"], identities["net"])
    )
    fd = open_pidfd(os.getpid())
    try:
        yield ConnectorTarget(fd, fd, 8765)
    finally:
        os.close(fd)


@pytest.mark.parametrize(
    "mode",
    [
        "death",
        "setup",
        "hang",
        "spawn",
        "timeout-spawn",
        "cancel-spawn",
        "cancel-spawn-after",
        "timeout-spawn-after",
        "cancel-handoff",
        "hang-after",
        "fail-after",
    ],
)
async def test_connector_cleanup_on_every_exit(monkeypatch, fake_target, mode: str) -> None:
    before = await fd_baseline()
    delay = mode in ("timeout-spawn", "cancel-spawn")
    hold = mode in ("cancel-spawn-after", "timeout-spawn-after")
    transports, spawning, release, started = helper_launch(
        monkeypatch,
        "hang" if delay or hold or mode.startswith("cancel") else mode,
        delay_spawn=delay,
        hold_spawn=hold,
    )
    if mode == "spawn":

        async def failed_spawn(*args, **kwargs):
            raise OSError("injected spawn failure")

        monkeypatch.setattr(asyncio.get_running_loop(), "subprocess_exec", failed_spawn)
    timeout = 0.75 if mode in ("hang", "hang-after") else (0.15 if delay or hold else 3)
    task = asyncio.create_task(connect_api(fake_target, timeout_s=timeout))
    if mode.startswith("cancel"):
        await spawning.wait()
        if mode in ("cancel-handoff", "cancel-spawn-after"):
            await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()  # Repeated cancellation must not abandon the owned spawn.
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        if mode in ("timeout-spawn", "timeout-spawn-after"):
            await (started.wait() if hold else spawning.wait())
            done, _ = await asyncio.wait({task}, timeout=2)
            assert task in done, "timeout must interrupt spawn without releasing it"
            release.set()
        with pytest.raises((OSError, ValueError, TimeoutError)):
            await task
    assert all(transport.get_returncode() is not None for transport in transports)
    assert_no_new_descriptors(before)
    os.fstat(fake_target.u0_pidfd)


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("after_spawn", [False, True])
async def test_stalled_spawn_observes_abort(monkeypatch, fake_target, cancel, after_spawn) -> None:
    before = await fd_baseline()
    transports, spawning, release, started = helper_launch(
        monkeypatch, "hang", delay_spawn=not after_spawn, hold_spawn=after_spawn
    )
    task = asyncio.create_task(connect_api(fake_target, timeout_s=5 if cancel else 0.15))
    try:
        await asyncio.wait_for((started if after_spawn else spawning).wait(), 2)
        if cancel:
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
        done, _ = await asyncio.wait({task}, timeout=2)
        assert task in done, "aborted connector still waits for the stalled spawn"
        with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
            await task
        assert bool(transports) == after_spawn
        for transport in transports:
            assert transport.get_returncode() is not None
            with pytest.raises(ChildProcessError):
                await asyncio.to_thread(os.waitpid, transport.get_pid(), os.WNOHANG)
        assert_no_new_descriptors(before)
        os.fstat(fake_target.u0_pidfd)
        os.fstat(fake_target.n0_pidfd)
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize(
    "mode",
    [
        "extra",
        "unconnected",
        "malformed",
        "no-fd",
        "overflow",
        "bad-shape",
        "unix",
        "listener",
        "file",
        "truncated",
        "wrong-user",
        "wrong-net",
        "wrong-pid",
        "wrong-mnt",
    ],
)
async def test_connector_fallback_boundary_and_socket_handoff(
    monkeypatch, fake_target, mode
) -> None:
    """Primary SCM_RIGHTS refusal tests; no fallback path has been selected."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        target = ConnectorTarget(
            fake_target.u0_pidfd, fake_target.n0_pidfd, listener.getsockname()[1]
        )
        before = await fd_baseline()
        transports, _, _, _ = helper_launch(monkeypatch, mode)
        with pytest.raises((ValueError, OSError)):
            await connect_api(target, timeout_s=3)
        assert all(transport.get_returncode() is not None for transport in transports)
        assert_no_new_descriptors(before)


async def test_reused_pidfd_number_is_refused(harness) -> None:
    fd = os.dup(harness.pidfd)
    target = ConnectorTarget(fd, fd, harness.witness["port"])
    replacement = open_pidfd(os.getpid())
    try:
        os.dup2(replacement, fd)
        with pytest.raises(ValueError, match="pidfd identity changed"):
            await connect_api(target, timeout_s=1)
    finally:
        os.close(replacement)
        os.close(fd)


async def test_success_reaps_helpers_and_transfers_only_client(monkeypatch, harness) -> None:
    loop = asyncio.get_running_loop()
    original = loop.subprocess_exec
    transports = []

    async def spawn(*args, **kwargs):
        transport, protocol = await original(*args, **kwargs)
        transports.append(transport)
        return transport, protocol

    monkeypatch.setattr(loop, "subprocess_exec", spawn)
    target = ConnectorTarget(harness.pidfd, harness.pidfd, harness.witness["port"])
    before = await fd_baseline()
    client = await connect_api(target, timeout_s=5)
    try:
        assert len(transports) == 1
        assert transports[0].get_returncode() == 0
        with pytest.raises(ChildProcessError):
            await asyncio.to_thread(os.waitpid, transports[0].get_pid(), os.WNOHANG)
        assert not os.get_inheritable(client.fileno())
        assert new_descriptors(before) == Counter(
            {os.readlink(f"/proc/self/fd/{client.fileno()}"): 1}
        )
    finally:
        client.close()
    assert_no_new_descriptors(before)
    os.fstat(harness.pidfd)


async def test_exited_namespace_owner_is_refused(harness) -> None:
    fd = os.dup(harness.pidfd)
    target = ConnectorTarget(fd, fd, harness.witness["port"])
    try:
        await harness.stop()
        with pytest.raises((ValueError, OSError)):
            await connect_api(target, timeout_s=1)
    finally:
        os.close(fd)


async def test_changed_namespace_identity_is_refused(monkeypatch, fake_target) -> None:
    from oversight.workspace import preview_connector as connector

    before = await fd_baseline()
    monkeypatch.setattr(connector, "target_identity", lambda *_: ("changed-user", "changed-net"))
    with pytest.raises(ValueError, match="namespace identity changed"):
        await connect_api(fake_target, timeout_s=1)
    assert_no_new_descriptors(before)


async def test_socket_handoff_cannot_redirect_fixed_destination(monkeypatch, fake_target) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        actual = listener.getsockname()[1]
        expected = actual + 1 if actual < 65535 else actual - 1
        target = ConnectorTarget(fake_target.u0_pidfd, fake_target.n0_pidfd, expected)
        before = await fd_baseline()
        helper_launch(monkeypatch, "wrong-port", connect_port=actual)
        with pytest.raises(ValueError, match="fixed private API"):
            await connect_api(target, timeout_s=3)
        assert_no_new_descriptors(before)


@pytest.mark.parametrize("port", [True, False, 0, -1, 65536, "80", 1.5, None])
async def test_invalid_port_with_live_handles(fake_target, port) -> None:
    with pytest.raises(ValueError, match="API port"):
        ConnectorTarget(fake_target.u0_pidfd, fake_target.n0_pidfd, cast(Any, port))


async def test_cleanup_failure_refuses_socket_evidence(monkeypatch, harness) -> None:
    from oversight.workspace import preview_connector as connector

    original = connector._reap

    async def unverified(transport, collector):
        await original(transport, collector)
        raise connector._CleanupError("injected unverified cleanup")

    monkeypatch.setattr(connector, "_reap", unverified)
    target = ConnectorTarget(harness.pidfd, harness.pidfd, harness.witness["port"])
    before = await fd_baseline()
    with pytest.raises(OSError, match="unverified cleanup"):
        await connect_api(target, timeout_s=5)
    assert_no_new_descriptors(before)


async def test_cancellation_after_socket_handoff(monkeypatch, fake_target) -> None:
    from oversight.workspace import preview_connector as connector

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        target = ConnectorTarget(
            fake_target.u0_pidfd, fake_target.n0_pidfd, listener.getsockname()[1]
        )
        transports, _, _, _ = helper_launch(monkeypatch, "hang-after")
        original = connector._namespace_witness
        received = asyncio.Event()

        def witness(data):
            result = original(data)
            received.set()
            return result

        monkeypatch.setattr(connector, "_namespace_witness", witness)
        before = await fd_baseline()
        task = asyncio.create_task(connect_api(target, timeout_s=5))
        try:
            async with asyncio.timeout(3):
                await received.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert transports and all(t.get_returncode() is not None for t in transports)
        assert_no_new_descriptors(before)


@pytest.mark.parametrize("kind", ["partial-rights", "unknown-control"])
async def test_malformed_ancillary_closes_received_rights(fake_target, kind) -> None:
    import array
    import json

    from oversight.workspace import preview_connector as connector
    from oversight.workspace.preview_connector_entry import namespaces

    fd = os.dup(fake_target.u0_pidfd)
    payload = array.array("i", [fd]).tobytes()
    ancillary = [
        (
            socket.SOL_SOCKET,
            socket.SCM_RIGHTS,
            payload + (b"x" if kind == "partial-rights" else b""),
        )
    ]
    if kind == "unknown-control":
        ancillary.append((socket.SOL_SOCKET, -1, b""))

    class Channel:
        def recvmsg(self, *args):
            return (
                json.dumps({"before": namespaces(), "after": namespaces()}).encode(),
                ancillary,
                0,
                None,
            )

    with pytest.raises(ValueError, match="exactly one socket handoff"):
        connector._receive(cast(Any, Channel()), fake_target)
    with pytest.raises(OSError):
        os.fstat(fd)
    os.fstat(fake_target.u0_pidfd)
