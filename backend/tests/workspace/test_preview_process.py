"""Descriptor/control protocol interception; fake C only, never real unshare."""

import asyncio
import json
import os
import socket
import sys
import time
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from oversight.workspace import PreviewProfile, isolation
from oversight.workspace import preview_process as client
from oversight.workspace import preview_supervisor as supervisor
from oversight.workspace.isolation_entry import FLAGS, HELPER, LAUNCHER, PREVIEW_HELPER
from oversight.workspace.preview_process import PreviewProcess, bind_listener, launch_configuration
from oversight.workspace.preview_registry import PreviewRegistry, identity_data
from oversight.workspace.runner import ProcessCollector

from .preview_fakes import profile
from .test_isolation_entry import Executed, helper_globals

pytestmark = pytest.mark.anyio


def test_preview_exec_preserves_listener_and_original_helper_positions(monkeypatch, tmp_path):
    monkeypatch.setattr(isolation, "unshare_binary", lambda: "/usr/bin/unshare")
    argv = ["/venv/bin/python", "-I", "-B", "/trusted/preview_adapter.py", "--listener-fd", "22"]
    command, descriptors = isolation.preview_launch(
        tmp_path,
        argv,
        entry=20,
        ack=21,
        listener=22,
        nonce="nonce",
        refusal_port=54321,
    )
    assert descriptors == (20, 21, 22)
    assert command[:5] == [sys.executable, "-I", "-S", "-c", LAUNCHER]
    index = command.index(PREVIEW_HELPER)
    assert command[index - 4 : index] == [sys.executable, "-I", "-S", "-c"]
    assert command[index + 1 : index + 5] == ["20", "21", "nonce", str(os.getpid())]
    assert command[index + 10] == "54321"
    assert command[index + 11 :] == argv
    assert tuple(command[7 : 7 + len(FLAGS)]) == FLAGS
    assert "--listener-fd" not in HELPER
    assert isolation.signature(tmp_path, "/usr/bin/unshare")[-1] == HELPER


def test_preview_helper_ack_precedes_listener_exec_and_suites_stay_unchanged():
    writes, closed, inherited = [], [], []
    identities = {"net": "new-net", "pid": "new-pid", "mnt": "new-mnt"}

    def execute(path, argv):
        assert closed == [10, 11] and writes
        assert inherited == [(22, True)]
        assert path == "/demo/python" and argv[-2:] == ["--listener-fd", "22"]
        raise Executed

    fake_os = SimpleNamespace(
        getpid=lambda: 1,
        readlink=lambda p: identities[p.rsplit("/", 1)[-1]],
        stat=lambda p: SimpleNamespace(st_dev=200),
        major=lambda _: 0,
        minor=lambda n: n,
        path=SimpleNamespace(lexists=lambda _: False),
        write=lambda fd, data: writes.append(data),
        close=closed.append,
        read=lambda fd, n: b"1",
        fstat=lambda fd: None,
        set_inheritable=lambda fd, value: inherited.append((fd, value)),
        execv=execute,
    )

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def settimeout(self, value):
            pass

        def connect_ex(self, address):
            return 111

    mount = "10 9 0:200 / /proc rw - proc proc rw"
    globs = helper_globals(fake_os, lambda _: SimpleNamespace(read_text=lambda: mount), Client)
    importing = globs["__builtins__"]["__import__"]
    importing("sys").argv.extend(["--listener-fd", "22"])
    with pytest.raises(Executed):
        exec(PREVIEW_HELPER, globs)


def test_launch_argv_env_scratch_and_separate_refusal_probe(tmp_path):
    app = bind_listener((19685, 19686))
    refusal = socket.socket()
    refusal.bind(("127.0.0.1", 0))
    refusal.listen(1)
    try:
        cfg = launch_configuration(
            profile(), tmp_path, tmp_path / "tree", tmp_path / "private", app, refusal, 100
        )
        assert cfg["refusal_port"] != app.getsockname()[1]
        assert cfg["argv"][:4] == [str(profile().interpreter), "-I", "-B", str(profile().adapter)]
        assert "-m" not in cfg["argv"]
        assert cfg["env"] == dict.fromkeys(("HOME", "TMP", "TEMP"), str(tmp_path / "private"))
        assert cfg["argv"][cfg["argv"].index("--listener-fd") + 1] == str(app.fileno())
        assert cfg["argv"][cfg["argv"].index("--memory-limit-bytes") + 1] == "536870912"
    finally:
        app.close()
        refusal.close()


async def test_backend_handoff_registers_ownership_before_execute_and_closes_listener(
    monkeypatch,
    tmp_path,
):
    registry = PreviewRegistry(tmp_path)
    registry.write({"version": 1, "runtime": str(tmp_path), "phase": "intent"})
    listener = bind_listener((19685, 19686))
    listener_fd = listener.fileno()
    process = PreviewProcess(registry, listener.getsockname()[1])
    calls, messages = [], []
    loop = asyncio.get_running_loop()

    async def spawn(factory, *argv, **kwargs):
        calls.append((argv, kwargs))
        assert kwargs["pass_fds"][2] == listener_fd
        assert process.control not in kwargs["pass_fds"]
        return SimpleNamespace(get_pid=lambda: os.getpid()), factory()

    async def message():
        if process.nonce is not None:
            registry.update(phase="executing")
            return {"kind": "executing"}
        data = registry.read()
        assert data is not None and data["phase"] == "owned"
        registry.update(phase="confirmed", namespace=identity_data(os.getpid()))
        return {"kind": "confirmed", "nonce": "a" * 64}

    def send(raw):
        if raw == b"execute\n":
            data = registry.read()
            assert data is not None and data["phase"] == "confirmed"
            assert process.pidfd is not None
        messages.append(raw)

    monkeypatch.setattr(loop, "subprocess_exec", spawn)
    monkeypatch.setattr(process, "message", message)
    monkeypatch.setattr(process, "send", send)
    monkeypatch.setattr(process, "confirmed_live", AsyncMock(return_value=True))
    readfd, writefd = os.pipe()
    monkeypatch.setattr(client, "open_pidfd", lambda _: readfd)
    await process.launch(listener, profile())
    assert listener.fileno() == -1 and messages == [b"launch\n", b"execute\n"]
    argv, kwargs = calls[0]
    assert "-I" in argv and "-B" in argv and str(tmp_path) in argv
    assert "PYTHONPATH" not in kwargs["env"] and "META_API_KEY" not in kwargs["env"]
    assert process.pipe is not None
    process.pipe.close()
    os.close(process.control)
    os.close(readfd)
    os.close(writefd)


@pytest.mark.parametrize("cancel", [False, True])
async def test_launch_failure_and_cancellation_close_every_host_listener(
    monkeypatch, tmp_path, cancel
):
    listener = bind_listener((19685, 19686))
    process = PreviewProcess(PreviewRegistry(tmp_path), listener.getsockname()[1])

    async def spawn(*args, **kwargs):
        raise asyncio.CancelledError if cancel else OSError("private launcher diagnostic")

    monkeypatch.setattr(asyncio.get_running_loop(), "subprocess_exec", spawn)
    with pytest.raises(asyncio.CancelledError if cancel else OSError):
        await process.launch(listener, profile())
    assert listener.fileno() == -1 and process.control == -1


@pytest.mark.parametrize("fault", [None, "nonce", "large", "type", "extra", "duplicate"])
async def test_readiness_requires_exact_nonce_and_bounded_json(monkeypatch, fault):
    nonce = "a" * 64
    body = json.dumps({"nonce": nonce if fault != "nonce" else "wrong"}).encode()
    if fault == "extra":
        body = json.dumps({"nonce": nonce, "output": "never read"}).encode()
    kind = "text/plain" if fault == "type" else "application/json"
    length = 10000 if fault == "large" else len(body)
    header = f"HTTP/1.1 200 OK\r\nContent-Type: {kind}\r\nContent-Length: {length}\r\n"
    if fault == "duplicate":
        header += f"Content-Length: {length}\r\n"
    reader = asyncio.StreamReader()
    reader.feed_data(header.encode() + b"\r\n" + body)
    reader.feed_eof()
    writes = []
    writer = SimpleNamespace(
        write=writes.append, drain=AsyncMock(), close=lambda: None, wait_closed=AsyncMock()
    )
    monkeypatch.setattr(asyncio, "open_connection", AsyncMock(return_value=(reader, writer)))
    assert await client.probe_ready(12345, nonce) is (fault is None)
    assert b"Origin:" not in writes[0] and b"lease" not in writes[0]


@pytest.mark.parametrize(
    "termination,reason", [(b"", "shutdown"), (b"stop\n", "closed"), (None, "lease_expired")]
)
async def test_supervisor_observes_backend_eof_and_deadlines(monkeypatch, termination, reason):
    clock = [100.0]
    collector = ProcessCollector(0)
    reader = asyncio.StreamReader()
    if termination == b"":
        reader.feed_eof()
    elif termination:
        reader.feed_data(termination)
    else:
        clock[0] = 131
    assert (
        await supervisor.watch(reader, collector, supervisor.Deadlines(130, 1000, lambda: clock[0]))
        == reason
    )


def test_absolute_renewal_never_revives_expired_supervisor(monkeypatch):
    clock = [100.0]
    deadlines = supervisor.Deadlines(130, 140, lambda: clock[0])
    deadlines.renew(9999)
    assert deadlines.lease == 140
    clock[0] = 141
    deadlines.renew(9999)
    assert deadlines.lease == 140 and deadlines.expired() == "lifetime_expired"


async def test_launcher_diagnostics_survive_but_fake_application_output_never_does():
    collector = ProcessCollector(2048)
    collector.stdout.feed(b"fake application secret output")
    collector.stderr.feed(b"Loopback setup failed: /private/path/secret\n" + b"x" * 10000)
    diagnostic = supervisor.launcher_diagnostic(collector)
    assert diagnostic == "Loopback setup failed"
    assert "secret" not in diagnostic and "private" not in diagnostic


@pytest.mark.parametrize(
    "code,reason",
    [
        (70, "launch_failed"),
        (71, "launch_failed"),
        (72, "launch_failed"),
        (73, "launch_failed"),
        (74, "memory_limit"),
        (-9, "app_exited"),
        (0, "app_exited"),
    ],
)
def test_private_exit_codes_are_not_signals_or_stderr_guesses(code, reason):
    assert supervisor.exit_reason(code) == reason


@pytest.mark.parametrize("execute", [True, False])
async def test_supervisor_persists_confirmed_owner_before_ack_and_cleans_on_eof(
    monkeypatch,
    tmp_path,
    execute,
):
    registry = PreviewRegistry(tmp_path)
    registry.write({"version": 1, "runtime": str(tmp_path), "phase": "owned"})
    app = bind_listener((19685, 19686))
    control_read, control_write = os.pipe()
    report_read, report_write = os.pipe()
    config = {
        "runtime": str(tmp_path),
        "argv": ["/demo/python", "--oversight-origin", "http://127.0.0.1:8710"],
        "scratch": str(tmp_path),
        "env": {},
        "refusal_port": 45678,
        "readiness_s": 1,
        "lease_deadline": time.monotonic() + 30,
        "absolute_deadline": time.monotonic() + 900,
    }
    owner = supervisor.Supervisor(config, control_read, report_write, app.detach())
    original_write = os.write
    order, handoff = [], []
    fake_pidfd, fake_write = os.pipe()

    def write(fd, raw):
        if fd == owner.ack_write and raw == b"1":
            data = registry.read()
            assert data is not None and data["phase"] == "executing" and data["namespace"]
            order.append("ack")
            return 1
        return original_write(fd, raw)

    async def spawn(factory, *argv, **kwargs):
        descriptors = kwargs["pass_fds"]
        assert descriptors == (owner.entry_write, owner.ack_read, owner.listener)
        assert control_read not in descriptors and report_write not in descriptors
        handoff.extend(descriptors)
        return SimpleNamespace(get_pid=lambda: 123, get_returncode=lambda: 0), factory()

    def command(*args, **kwargs):
        assert kwargs["refusal_port"] == 45678
        assert kwargs["listener"] == owner.listener
        assert args[1] == ["/demo/python", "--ready-nonce", owner.nonce, *config["argv"][1:]]
        return ["trusted-launcher"], (owner.entry_write, owner.ack_read, owner.listener)

    async def cleanup(transport, collector, init):
        assert init == fake_pidfd
        assert "ack" in order if execute else "ack" not in order
        order.append("namespace_dead")
        os.close(fake_pidfd)

    monkeypatch.setattr(asyncio.get_running_loop(), "subprocess_exec", spawn)
    monkeypatch.setattr(supervisor, "preview_launch", command)
    monkeypatch.setattr(
        supervisor, "identity_data", lambda pid: {"pid": pid, "birth": "b", "boot": "x"}
    )
    monkeypatch.setattr(supervisor, "namespace_identity", lambda pid: {"pid": 124})
    monkeypatch.setattr(supervisor, "read_preview_entry", AsyncMock(return_value=b"entry"))
    monkeypatch.setattr(
        supervisor, "confirm_preview_entry", lambda *args: ({"identities": {}}, fake_pidfd)
    )
    monkeypatch.setattr(supervisor, "cleanup_preview_namespace", cleanup)
    monkeypatch.setattr(os, "write", write)
    original_write(control_write, b"launch\n" + (b"execute\n" if execute else b""))
    os.close(control_write)  # Backend death: no cooperative application stop is involved.
    await owner.run()
    messages = [json.loads(row) for row in os.read(report_read, 4096).splitlines()]
    expected = ["confirmed", "executing", "dead"] if execute else ["confirmed", "dead"]
    assert [m["kind"] for m in messages] == expected
    assert order[-1] == "namespace_dead"
    assert all(
        getattr(owner, name) == -1
        for name in ("listener", "entry_read", "entry_write", "ack_read", "ack_write")
    )
    assert handoff and messages[-1]["reason"] == "shutdown"
    os.close(report_read)
    os.close(fake_write)


async def test_namespace_descendant_witness_outlives_dead_leader(monkeypatch, tmp_path):
    from oversight.workspace.preview_registry import ProcessIdentity

    registry = PreviewRegistry(tmp_path)
    registry.write(
        {
            "version": 1,
            "runtime": str(tmp_path),
            "phase": "dead",
            "namespace": {"pid": 123, "birth": "b", "boot": "x"},
        }
    )
    process = PreviewProcess(registry, 19685)
    closed = []
    process.transport = cast(
        asyncio.SubprocessTransport,
        SimpleNamespace(close=lambda: closed.append("transport")),
    )
    process.collector.exited.set_result(None)  # Leader exit alone cannot free this preview.
    process.reader = asyncio.StreamReader()
    process.reader.feed_data(b'{"kind":"dead","reason":"closed","exit_code":0}\n')
    process.pidfd = 12345
    namespace_wait, namespace_dead = asyncio.Event(), asyncio.Event()

    async def witness(fd):
        assert fd == 12345
        namespace_wait.set()
        await namespace_dead.wait()  # Includes descendants which changed process session.

    monkeypatch.setattr(client, "verify_preview_namespace_death", witness)
    monkeypatch.setattr(ProcessIdentity, "alive", lambda self: False)
    stop = asyncio.create_task(process.stop())
    await namespace_wait.wait()
    assert not stop.done() and not process.dead
    namespace_dead.set()
    await stop
    assert process.dead and closed == ["transport"]


async def test_repeated_cancellation_cannot_lose_spawned_supervisor_ownership(
    monkeypatch, tmp_path
):
    registry = PreviewRegistry(tmp_path)
    registry.write({"version": 1, "runtime": str(tmp_path), "phase": "intent"})
    listener = bind_listener((19685, 19686))
    process = PreviewProcess(registry, listener.getsockname()[1])
    started, delivered = asyncio.Event(), asyncio.Event()
    transport = SimpleNamespace(get_pid=lambda: os.getpid())

    async def spawn(factory, *args, **kwargs):
        started.set()  # The OS has created it, but the event loop has not delivered it yet.
        await delivered.wait()
        return transport, factory()

    monkeypatch.setattr(asyncio.get_running_loop(), "subprocess_exec", spawn)
    task = asyncio.create_task(process.launch(listener, profile()))
    await started.wait()
    for _ in range(3):
        task.cancel()
        await asyncio.sleep(0)
    assert not task.done()
    delivered.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    data = registry.read()
    assert data is not None and data["supervisor"]["pid"] == os.getpid()
    assert data["phase"] == "owned" and process.transport is transport
    assert listener.fileno() == -1 and process.control == -1


async def test_supervisor_reads_configuration_only_after_durable_handoff(monkeypatch, tmp_path):
    registry = PreviewRegistry(tmp_path)
    registry.write(
        {
            "version": 1,
            "runtime": str(tmp_path),
            "phase": "owned",
            "launch": {"runtime": str(tmp_path)},
        }
    )
    listener = bind_listener((19685, 19686))
    control_read, control_write = os.pipe()
    report_read, report_write = os.pipe()
    committed = asyncio.Event()
    reads = []
    original_read = PreviewRegistry.read

    def read(owner):
        assert committed.is_set(), "read raced the backend's durable ownership write"
        reads.append("read")
        return original_read(owner)

    async def run(owner, reader, pipe, *, authorized):
        assert authorized
        pipe.close()
        await owner.cleanup("shutdown")  # No C/application was launched in this fake.

    monkeypatch.setattr(PreviewRegistry, "read", read)
    monkeypatch.setattr(supervisor.Supervisor, "run", run)
    task = asyncio.create_task(
        supervisor.supervise(
            tmp_path,
            control_read,
            report_write,
            listener.detach(),
        )
    )
    for _ in range(3):
        await asyncio.sleep(0)
    assert reads == []
    committed.set()
    os.write(control_write, b"launch\n")
    os.close(control_write)
    await task
    assert reads
    message = json.loads(os.read(report_read, 4096))
    assert message["kind"] == "dead" and message["reason"] == "shutdown"
    os.close(report_read)


@pytest.mark.parametrize("acknowledgements", [[], ["confirmed"], ["confirmed", "executing"]])
async def test_stop_owns_report_reader_after_incomplete_handoff(tmp_path, acknowledgements):
    registry = PreviewRegistry(tmp_path)
    registry.write({"version": 1, "runtime": str(tmp_path), "phase": "dead"})
    process = PreviewProcess(registry, 19685)
    reader = asyncio.StreamReader()
    process.reader = reader
    for kind in acknowledgements:
        reader.feed_data(json.dumps({"kind": kind}).encode() + b"\n")
    reader.feed_data(b'{"kind":"dead","reason":"closed","exit_code":null}\n')
    reader.feed_eof()
    async with asyncio.timeout(5):
        await process.stop()
    assert process.dead and process.reported_dead and process.reason == "closed"


def test_reviewed_configuration_never_constructs_shop_argv(tmp_path):
    frozen = SimpleNamespace(**vars(profile()))
    frozen.reviewed_app, frozen.adapter = object(), None
    with socket.socket() as listener, socket.socket() as refusal:
        for owned in (listener, refusal):
            owned.bind(("127.0.0.1", 0))
            owned.listen()
        config = launch_configuration(
            cast(PreviewProfile, frozen),
            tmp_path,
            tmp_path / "T",
            tmp_path / "scratch",
            listener,
            refusal,
            1,
        )
    assert "argv" not in config


@pytest.mark.parametrize("parent,frame", [("localhost", "127.0.0.1"), ("127.0.0.1", "localhost")])
def test_shop_launch_freezes_exact_opposite_authority(tmp_path, parent, frame):
    app = bind_listener((19685, 19686))
    refusal = socket.socket()
    refusal.bind(("127.0.0.1", 0))
    refusal.listen()
    try:
        cfg = launch_configuration(
            profile(),
            tmp_path,
            tmp_path / "tree",
            tmp_path / "private",
            app,
            refusal,
            100,
            parent_authority=f"{parent}:8710",
        )
        argv = cfg["argv"]
        assert argv[argv.index("--frame-host") + 1] == frame
        assert argv.count("--oversight-origin") == 1
        assert argv[argv.index("--oversight-origin") + 1] == f"http://{parent}:8710"
    finally:
        app.close()
        refusal.close()
