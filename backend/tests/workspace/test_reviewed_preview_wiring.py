"""Wiring gates distinguish static fixtures from deployed namespace proofs."""

import asyncio
import os
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from oversight.workspace._reviewed_preview import prepare_reviewed, validate_storage
from oversight.workspace.preview_preparation import PreparationInputs, prepare_preview

from .test_preview_preparation import inputs as inputs

ROOT = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("field", ["preview_directory", "preparation_storage"])
async def test_reviewed_storage_exclusions_precede_preparation(
    inputs: PreparationInputs, field: str
):
    original = inputs.excluded_roots[0]
    with pytest.raises(ValueError, match="excluded"):
        await prepare_reviewed(replace(inputs, **{field: original / "previews/example"}))
    assert not (original / "previews").exists()


async def test_reviewed_preparation_off_loop_and_settled(inputs, monkeypatch):
    import threading

    from oversight.workspace import _reviewed_preview as wiring

    started, release, finished = asyncio.Event(), threading.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    original = prepare_preview

    async def prepare(value):
        assert threading.get_ident() != loop_thread
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5)
        try:
            return await original(value)
        finally:
            finished.set()

    monkeypatch.setattr(wiring, "prepare_preview", prepare)
    task = asyncio.create_task(prepare_reviewed(inputs))
    async with asyncio.timeout(5):
        await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


async def test_reviewed_exclusion_aliases(inputs, tmp_path):
    alias = tmp_path / "alias"
    alias.symlink_to(inputs.excluded_roots[0], target_is_directory=True)
    with pytest.raises(ValueError, match="excluded"):
        validate_storage(replace(inputs, excluded_roots=(alias,), tree=inputs.excluded_roots[0]))


async def test_reviewed_launch_durable_before_exec(tmp_path):
    from oversight.workspace.preview_boundary import launch_boundary
    from oversight.workspace.preview_registry import PreviewRegistry, identity_data

    from .preview_boundary_support import config, prepared

    roots = await asyncio.to_thread(prepared, tmp_path / "boundary")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    registry = PreviewRegistry(runtime)
    registry.write(dict(version=1, runtime=str(runtime), phase="intent"))
    release = asyncio.Event()
    confirmed = asyncio.Event()
    marker = roots.tree / "EXECUTED"

    async def persist(handle):
        assert not marker.exists()
        assert set(handle.processes) == {"outer", "setup", "init"}
        assert set(handle.namespace_fds) == {"u0", "n0", "u1", "pid", "mount"}
        registry.update(
            phase="confirmed",
            processes={k: identity_data(v.pid) for k, v in handle.processes.items()},
        )
        confirmed.set()
        await release.wait()
        data = registry.read()
        assert data is not None and data["phase"] == "confirmed"
        assert not marker.exists()
        registry.update(phase="executing")

    task = asyncio.create_task(
        launch_boundary(
            config("from pathlib import Path; Path('/T/EXECUTED').write_text('yes')"),
            roots,
            before_exec=persist,
        )
    )
    try:
        async with asyncio.timeout(20):
            await confirmed.wait()
        data = registry.read()
        assert data is not None and data["phase"] == "confirmed"
        assert not marker.exists()
        release.set()
        handle = await task
        await handle.wait()
        assert handle.returncode == 0, handle.stderr
        assert marker.read_text() == "yes"
        assert (data := registry.read()) is not None and data["phase"] == "executing"
        assert handle.verified_dead
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_reviewed_persistence_failure_keeps_execution_closed(tmp_path):
    from oversight.workspace.preview_boundary import BoundaryUnavailable, launch_boundary

    from .preview_boundary_support import config, prepared

    roots = await asyncio.to_thread(prepared, tmp_path / "boundary")
    owned = []

    async def refuse(handle):
        owned.append(handle)
        raise OSError("durability failed")

    with pytest.raises(BoundaryUnavailable, match="durability failed"):
        await launch_boundary(
            config("from pathlib import Path; Path('/T/EXECUTED').touch()"),
            roots,
            before_exec=refuse,
        )
    assert owned[0].verified_dead
    assert not (roots.tree / "EXECUTED").exists()


async def controlled_execution(
    tmp_path, monkeypatch, inputs, *, health_run="fresh-run", parent_host="localhost"
):
    """Real boundary/connector/gateway; prepared tools are controlled Python fixtures."""
    import json
    import socket
    import time
    from dataclasses import asdict

    from oversight.workspace import _reviewed_preview as wiring
    from oversight.workspace.preview_preparation import PreparedPreview, ToolIdentity
    from oversight.workspace.preview_profile import PreviewProfile
    from oversight.workspace.preview_registry import PreviewRegistry

    from .preview_boundary_support import prepared as boundary_fixture

    roots = await asyncio.to_thread(boundary_fixture, tmp_path / "boundary")
    directory = tmp_path / "runtime/previews/example"
    for part in ("state", "logs", "published"):
        (directory / part).mkdir(parents=True, mode=0o700)
    roots = replace(roots, state=directory / "state")
    inputs = replace(inputs, tree=roots.tree, preview_directory=directory)
    ready = PreparedPreview(
        roots.scratch,
        roots.tree,
        roots.state,
        (),
        (),
        (
            ToolIdentity("python", Path("/opt/python/bin/python"), "fixture"),
            ToolIdentity("uv-python", Path("/opt/python/bin/python"), "fixture"),
        ),
        (Path("/T"),),
        (Path("/opt/python/bin"),),
        Path("/opt/deps"),
        Path("/opt/python/bin/python"),
        (),
        directory / "logs/build.log",
    )

    async def preparation(_):
        return ready

    monkeypatch.setattr(wiring, "prepare_reviewed", preparation)
    monkeypatch.setattr(wiring, "boundary_roots", lambda *args: roots)
    listener, reservation = socket.socket(), socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    reservation.bind(("127.0.0.1", 0))
    api_port = reservation.getsockname()[1]
    reservation.close()
    build = """
from pathlib import Path
root = Path('/state/dist')
(root/'assets').mkdir(parents=True)
(root/'index.html').write_text('<html>controlled build</html>')
(root/'assets/app.js').write_text('export {}')
(root/'assets/worker.js').write_text('self.onmessage = () => {}')
Path('/T/build-completed').write_text('yes')
"""
    backend = f"""
import asyncio, ctypes, fcntl, json, os, socket, struct
from pathlib import Path
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as control:
    current = fcntl.ioctl(control, 0x8913, struct.pack('16sH22x', b'lo', 0))
    flags = struct.unpack('16sH22x', current)[1]
    try: fcntl.ioctl(control, 0x8914, struct.pack('16sH22x', b'lo', flags & ~1))
    except OSError as error: assert error.errno == 1
    else: raise AssertionError('U1 lowered N0 loopback')
with socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, socket.NETLINK_ROUTE) as route:
    route.settimeout(2)
    route.bind((0, 0))
    attributes = struct.pack('HH', 8, 1) + socket.inet_aton('198.51.100.0')
    attributes += struct.pack('HHI', 8, 4, socket.if_nametoindex('lo'))
    message = struct.pack('IHHII', 44, 24, 0x605, 1, 0)
    message += struct.pack('BBBBBBBBI', socket.AF_INET, 24, 0, 0, 254, 3, 253, 1, 0)
    route.send(message + attributes)
    reply = route.recv(4096)
    assert struct.unpack_from('H', reply, 4)[0] == 2
    assert struct.unpack_from('i', reply, 16)[0] == -1
Path('/T/network-denials').write_text('lo=EPERM; route=EPERM')
# This private proc cannot inspect/signal the actual host supervisor/gateway.
for path in ['/proc/{os.getpid()}/status', {str(wiring.__file__)!r}]:
    try: Path(path).read_bytes()
    except OSError: pass
    else: raise AssertionError('gateway visible')
try: os.kill({os.getpid()}, 0)
except OSError: pass
else: raise AssertionError('gateway signalable')
libc = ctypes.CDLL(None, use_errno=True)
assert libc.ptrace(16, {os.getpid()}, 0, 0) == -1
try: Path({str(wiring.__file__)!r}).write_text('tampered')
except OSError: pass
else: raise AssertionError('gateway replaceable')
Path('/T/import-witness').write_text('/T')
async def handle(reader, writer):
    try:
        raw = await reader.readuntil(b'\\r\\n\\r\\n')
        path = raw.split(b' ')[1]
        if path == b'/events':
            event = {{'type':'run.started','payload':{{'run_id':{health_run!r}}}}}
            body = b'data: ' + json.dumps(event).encode() + b'\\n\\n'
            kind = b'text/event-stream'
        else:
            body = json.dumps({{'ok': True, 'run_id': {health_run!r}}}).encode()
            kind = b'application/json'
        head = b'HTTP/1.1 200 OK\\r\\nContent-Type: ' + kind
        head += b'\\r\\nContent-Length: ' + str(len(body)).encode()
        writer.write(head + b'\\r\\n\\r\\n' + body)
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()
async def main():
    server = await asyncio.start_server(handle, '127.0.0.1', {api_port})
    async with server: await server.serve_forever()
asyncio.run(main())
"""
    runtime = tmp_path / "runtime"
    runtime.mkdir(exist_ok=True)
    registry = PreviewRegistry(runtime)
    registry.write(
        dict(version=1, runtime=str(runtime), phase="intent", preview_id="preview-" + "a" * 32)
    )
    policy = asdict(PreviewProfile())
    config = dict(
        readiness_s=5,
        lease_deadline=time.monotonic() + 60,
        absolute_deadline=time.monotonic() + 60,
        reviewed=dict(
            inputs=json.loads(json.dumps(asdict(inputs), default=str)),
            policy=policy,
            parent_authority=parent_host + ":8710",
            run_id="fresh-run",
            api_port=api_port,
            public_port=listener.getsockname()[1],
            commands=[
                dict(
                    argv=["/opt/python/bin/python", "-I", "-S", "-c", code], cwd="/T", timeout_s=10
                )
                for code in (build, backend)
            ],
        ),
    )
    reader = asyncio.StreamReader()
    reports = asyncio.Queue()

    def report(**fields):
        reports.put_nowait(fields)

    execution = wiring.ReviewedExecution(config, registry, reader, listener, report)
    return execution, reader, reports, registry, roots


async def next_report(reports):
    async with asyncio.timeout(20):
        report = await reports.get()
        while report["kind"] == "probing":
            assert "nonce" not in report
            report = await reports.get()
        return report


@pytest.mark.parametrize("parent_host", ["localhost", "127.0.0.1"])
async def test_reviewed_connector_u1_and_gateway_tamper(tmp_path, monkeypatch, inputs, parent_host):
    import json
    import os
    import socket

    execution, reader, reports, registry, roots = await controlled_execution(
        tmp_path, monkeypatch, inputs, parent_host=parent_host
    )
    task = asyncio.create_task(execution.run())
    try:
        assert await next_report(reports) == {"kind": "confirmed"}
        assert not (roots.tree / "build-completed").exists()
        data = registry.read()
        assert data is not None and data["phase"] == "confirmed"
        reader.feed_data(b"execute\n")
        assert await next_report(reports) == {"kind": "executing"}, (
            execution.guardian.collector.stderr.text()
        )
        ready = await next_report(reports)
        assert ready["kind"] == "ready", (
            ready,
            execution.handle.stderr if execution.handle else "",
        )
        assert len(ready["nonce"]) == 64
        from oversight.workspace.preview_process import probe_ready

        assert await probe_ready(
            execution.spec["public_port"],
            ready["nonce"],
            execution.gateway.public_authority if execution.gateway else "invalid",
            reviewed=True,
        )
        assert execution.gateway is not None and execution.gateway.alive()
        frame = "127.0.0.1" if parent_host == "localhost" else "localhost"
        addresses = await asyncio.get_running_loop().getaddrinfo(
            frame, execution.spec["public_port"], family=socket.AF_INET
        )
        assert any(address[4][0] == "127.0.0.1" for address in addresses)
        assert execution.gateway.public_authority == f"{frame}:{execution.spec['public_port']}"
        response = await gateway_request(
            execution.spec["public_port"], execution.gateway.public_authority
        )
        assert response.startswith(b"HTTP/1.1 200")
        assert ("frame-ancestors http://" + parent_host + ":8710").encode() in response
        refused = await gateway_request(
            execution.spec["public_port"], f"{parent_host}:{execution.spec['public_port']}"
        )
        assert refused.startswith(b"HTTP/1.1 403")
        assert execution.handle is not None
        assert (roots.tree / "build-completed").read_text() == "yes"
        assert (roots.tree / "import-witness").read_text() == "/T"
        assert (roots.tree / "network-denials").read_text() == "lo=EPERM; route=EPERM"
        target = execution.gateway.connector
        private = await target.connect_api()
        private.close()
        print(
            "deployed namespace witnesses", json.dumps(data["boundary"]), "gateway pid", os.getpid()
        )
        assert execution.gateway.alive()
        reader.feed_data(b"stop\n")
        await asyncio.wait_for(task, 10)
        assert execution.handle.verified_dead
        data = registry.read()
        assert data is not None and data["phase"] == "dead"
        assert (await next_report(reports))["kind"] == "dead"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize(
    "trigger", ["backend_eof", "cancel", "lease", "lifetime", "backend_crash", "gateway_crash"]
)
async def test_reviewed_teardown(tmp_path, monkeypatch, inputs, trigger):
    import socket
    import time

    from oversight.workspace.preview_registry import kill_pinned

    execution, reader, reports, registry, _roots = await controlled_execution(
        tmp_path, monkeypatch, inputs
    )
    port = execution.listener.getsockname()[1]
    task = asyncio.create_task(execution.run())
    try:
        assert (await next_report(reports))["kind"] == "confirmed"
        reader.feed_data(b"execute\n")
        assert (await next_report(reports))["kind"] == "executing"
        ready = await next_report(reports)
        assert ready["kind"] == "ready", ready
        handle = execution.handle
        gateway = execution.gateway
        assert handle is not None and gateway is not None
        death = asyncio.Event()
        original_stop = handle.stop_verified

        async def stop_verified():
            await original_stop()
            assert handle.verified_dead
            death.set()

        monkeypatch.setattr(handle, "stop_verified", stop_verified)
        if trigger == "backend_eof":
            reader.feed_eof()
        elif trigger == "cancel":
            task.cancel()
        elif trigger == "lease":
            execution.lease = time.monotonic()
        elif trigger == "lifetime":
            execution.absolute = time.monotonic()
        elif trigger == "backend_crash":
            kill_pinned(handle.pidfds["init"])
        else:
            await gateway.close()
            # A supervisor-owned duplicate retains the public reservation while T dies.
            with socket.socket() as attempted, pytest.raises(OSError):
                attempted.bind(("127.0.0.1", port))
        await asyncio.wait_for(task, 10)
        assert death.is_set() and handle.verified_dead
        with socket.socket() as available:
            available.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            available.bind(("127.0.0.1", port))
        data = registry.read()
        assert data is not None and data["phase"] == "dead"
        assert (await next_report(reports))["kind"] == "dead"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("trigger", ["stop", "eof", "cancel"])
async def test_reviewed_teardown_before_execute(tmp_path, monkeypatch, inputs, trigger):
    execution, reader, reports, registry, roots = await controlled_execution(
        tmp_path, monkeypatch, inputs
    )
    # The normal launch deadline must exceed the backend stop bound.
    execution.config["readiness_s"] = 30
    task = asyncio.create_task(execution.run())
    try:
        assert (await next_report(reports))["kind"] == "confirmed"
        handle = execution.handle
        assert handle is not None and not execution.execute.is_set()
        started = asyncio.get_running_loop().time()
        if trigger == "stop":
            reader.feed_data(b"stop\n")
        elif trigger == "eof":
            reader.feed_eof()
        else:
            task.cancel()
        await asyncio.wait_for(asyncio.shield(task), 14)
        assert asyncio.get_running_loop().time() - started < 15
        assert handle.verified_dead and not (roots.tree / "build-completed").exists()
        report = await next_report(reports)
        assert report["kind"] == "dead" and "nonce" not in report
        assert (data := registry.read()) is not None and data["phase"] == "dead"
    finally:
        execution.execute.set()  # Release a broken implementation before fixture teardown.
        execution.stopped.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize(
    "fault",
    ["stale", "backend_dead", "gateway_dead", "forged", "incomplete", "slow_incomplete", "expired"],
)
async def test_reviewed_readiness_nonce(tmp_path, monkeypatch, inputs, fault):
    from oversight.workspace.preview_gateway import PreviewGateway
    from oversight.workspace.preview_registry import kill_pinned

    execution, reader, reports, registry, _roots = await controlled_execution(
        tmp_path,
        monkeypatch,
        inputs,
        health_run="stale-run" if fault == "stale" else "fresh-run",
    )
    attach_gateway = execution.attach_gateway

    async def attach_with_probe_budget():
        # Keep the normal fixture budget for namespace entry and shorten only readiness.
        execution.config["readiness_s"] = 2
        await attach_gateway()

    monkeypatch.setattr(execution, "attach_gateway", attach_with_probe_budget)
    if fault == "slow_incomplete":
        confirm = execution.confirm

        async def slow_confirm(handle):
            await confirm(handle)
            # A healthy namespace handshake can exceed the short fault-probe budget.
            await asyncio.sleep(2.1)

        monkeypatch.setattr(execution, "confirm", slow_confirm)
    original = PreviewGateway.probe
    original_get = PreviewGateway._probe_get
    probing = asyncio.Event()
    proceed = asyncio.Event()

    async def probe_get(gateway, path, **kwargs):
        result = await original_get(gateway, path, **kwargs)
        if fault.endswith("dead") and path == "/events":
            # Fresh health and the real service's SSE have succeeded; stop while probing.
            probing.set()
            await proceed.wait()
        return result

    async def probe(gateway, **kwargs):
        if not fault.endswith("dead"):
            probing.set()
            await proceed.wait()
        if fault.endswith("incomplete"):
            kwargs["workers"] = ()
        evidence = await original(gateway, **kwargs)
        if fault == "forged":
            return replace(evidence)
        if fault == "expired":
            gateway.limits = replace(gateway.limits, evidence_s=0.000001)
            await asyncio.sleep(0.01)
        return evidence

    monkeypatch.setattr(PreviewGateway, "probe", probe)
    monkeypatch.setattr(PreviewGateway, "_probe_get", probe_get)
    task = asyncio.create_task(execution.run())
    try:
        assert (await next_report(reports))["kind"] == "confirmed"
        reader.feed_data(b"execute\n")
        assert (await next_report(reports))["kind"] == "executing"
        async with asyncio.timeout(20):
            assert (await reports.get())["kind"] == "probing"
            await probing.wait()
        assert execution.handle is not None and execution.gateway is not None
        if fault == "backend_dead":
            kill_pinned(execution.handle.pidfds["init"])
            await execution.handle.wait()
        elif fault == "gateway_dead":
            await execution.gateway.close()
        proceed.set()
        report = await next_report(reports)
        assert report["kind"] == "dead" and "nonce" not in report
        if fault.endswith("dead"):
            assert report["reason"] == "app_exited"
        await asyncio.wait_for(task, 10)
        assert not execution.gateway.alive() and execution.handle.verified_dead
        assert (data := registry.read()) is not None and data["phase"] == "dead"
        assert reports.empty()
    finally:
        proceed.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def real_prepared_inputs(directory, tracked):
    """External static fixture preparer: record private tool/dependency copies before reuse."""
    import hashlib
    import shutil
    import sys

    from oversight.workspace.preview_boundary_entry import SYSTEM_DIRECTORIES, SYSTEM_FILES
    from oversight.workspace.preview_preparation import ProjectImport, RootMapping, ToolIdentity

    from .test_preview_preparation import record

    original = ROOT
    tree, storage = directory / "T", directory / "prepared"
    tree.mkdir()
    storage.mkdir()
    for name in tracked:
        source = original / name
        if source.is_file() and not source.is_symlink():
            destination = tree / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    mappings, inventories = [], []

    def add(name, role, source, target):
        mappings.append(RootMapping(source, Path(target)))
        inventories.append(replace(record(source, name, owned=True), role=role))

    python = storage / "python"
    shutil.copytree(Path(sys.executable).resolve().parents[1], python, symlinks=True)
    for path in python.rglob("*"):
        if path.is_file() and not path.is_symlink() and path.name not in {"python3.12", "python"}:
            path.chmod(path.stat().st_mode & ~0o111)
    add("python", "tool", python, "/opt/python")
    for tool in ("uv", "node", "pnpm"):
        executable = shutil.which(tool)
        assert executable is not None, f"required prepared tool missing: {tool}"
        root = storage / tool
        root.mkdir()
        shutil.copy2(Path(executable).resolve(), root / tool)
        if tool == "uv":
            scanner = shutil.which("gitleaks")
            assert scanner is not None, "required prepared tool missing: gitleaks"
            shutil.copy2(Path(scanner).resolve(), root / "gitleaks")
        add(tool, "tool", root, "/opt/" + tool)
    backend = storage / "backend"
    shutil.copytree(
        original / ".venv", backend, symlinks=True, ignore=shutil.ignore_patterns("__pycache__")
    )
    for path in (backend / "bin").iterdir():
        path.unlink()
    (backend / "bin/python").symlink_to("/opt/python/bin/python")
    (backend / "pyvenv.cfg").write_text(
        "home = /opt/python/bin\ninclude-system-site-packages = false\n"
    )
    (backend / "lib/python3.12/site-packages/_virtualenv.pth").unlink()
    for path in backend.rglob("*"):
        if path.is_file() and not path.is_symlink():
            path.chmod(path.stat().st_mode & ~0o111)
    add("backend", "backend", backend, "/opt/deps/backend")
    for name in ("contracts", "demo-test"):
        root = storage / name
        root.mkdir()
        source = (
            original / "contracts/pyproject.toml"
            if name == "contracts"
            else original / "tools/demo-venv-requirements.txt"
        )
        shutil.copy2(source, root / source.name)
        add(name, name, root, "/opt/deps/" + name)
    frontend = storage / "frontend"
    shutil.copytree(
        original / "frontend/node_modules",
        frontend,
        symlinks=True,
        ignore=shutil.ignore_patterns(".vite", ".vite-temp", ".cache"),
    )
    shutil.rmtree(frontend / ".bin")
    for path in frontend.rglob("*"):
        if path.is_file() and not path.is_symlink():
            path.chmod(path.stat().st_mode & ~0o111)
    add("frontend", "frontend", frontend, "/opt/deps/frontend")
    # These ignored mount anchors exist before any candidate evidence or edited code runs.
    anchors = tree / "frontend/node_modules"
    anchors.mkdir()
    for entry in frontend.iterdir():
        (anchors / entry.name).symlink_to(
            Path("/opt/deps/frontend") / entry.name, target_is_directory=entry.is_dir()
        )
    state = directory / "previews/example/state"
    for name, destination in ((".vite-temp", "config/vite-temp"), (".vite", "cache/vite")):
        (state / destination).mkdir(parents=True, mode=0o700)
        (anchors / name).symlink_to(Path("/state") / destination, target_is_directory=True)
    groups = {}
    for target in (*SYSTEM_FILES, *SYSTEM_DIRECTORIES):
        parent = str(Path(target).parent)
        # Group only siblings; never mount a broad host system directory into T.
        root = groups.setdefault(parent, storage / ("system-" + str(len(groups))))
        root.mkdir(exist_ok=True)
        source, destination = Path(target).resolve(strict=True), root / Path(target).name
        if source.is_dir():
            shutil.copytree(source, destination, symlinks=True)
        else:
            shutil.copy2(source, destination)
    # The static inventory also validates dormant Git template shebangs. These extra
    # validation inputs are not mounted: the adapter still selects the fixed system list.
    shutil.copy2("/usr/bin/perl", groups["/usr/bin"] / "perl")
    shell = storage / "system-bin"
    shell.mkdir()
    shutil.copy2("/usr/bin/dash", shell / "sh")
    add("system-bin", "system", shell, "/bin")
    for index, (target, root) in enumerate(groups.items()):
        add("system-" + str(index), "system", root, target)
    mappings.append(RootMapping(tree, Path("/T"), False))
    tools = []
    for name, path in (
        ("python", "/opt/deps/backend/bin/python"),
        ("uv-python", "/opt/python/bin/python"),
        ("uv", "/opt/uv/uv"),
        ("node", "/opt/node/node"),
        ("pnpm", "/opt/pnpm/pnpm"),
        ("gitleaks", "/opt/uv/gitleaks"),
    ):
        executable = (
            str(Path(sys.executable).resolve())
            if name in {"python", "uv-python"}
            else shutil.which(name)
        )
        assert executable is not None, f"required prepared tool missing: {name}"
        source = Path(executable).resolve()
        tools.append(
            ToolIdentity(name, Path(path), hashlib.sha256(source.read_bytes()).hexdigest())
        )
    imports = tuple(
        ProjectImport(
            Path("/opt/deps/backend/lib/python3.12/site-packages"),
            original / name,
            Path("/T") / name,
            (package,),
            "src",
        )
        for name, package in (("backend", "oversight"), ("contracts", "oversight_contracts"))
    )
    return PreparationInputs(
        tree,
        (original,),
        directory / "previews/example",
        storage,
        tuple(inventories),
        tuple(tools),
        tuple(map(Path, (*SYSTEM_FILES, *SYSTEM_DIRECTORIES))),
        tuple(mappings),
        imports,
        Path("/opt/deps/backend"),
        Path("/opt/deps/backend/bin/python"),
        (),
    )


@pytest.mark.timeout(180)
async def test_reviewed_offline_build_and_imports(tmp_path):
    import json
    from dataclasses import asdict

    from oversight.workspace import _reviewed_preview as wiring
    from oversight.workspace.preview_boundary import BoundaryConfig, launch_boundary
    from oversight.workspace.preview_preparation import scrub_preview_env
    from oversight.workspace.runner import run_process

    original = ROOT
    files = await run_process(["git", "ls-files", "-z"], cwd=original, timeout_s=20)
    assert files.returncode == 0 and not files.timed_out
    inputs = await asyncio.to_thread(real_prepared_inputs, tmp_path, files.stdout.split("\0")[:-1])
    prepared = await wiring.prepare_reviewed(inputs)
    roots = await asyncio.to_thread(wiring.boundary_roots, prepared, inputs.excluded_roots)
    print("prepared inventories", [(i.name, i.digest, len(i.entries)) for i in prepared.manifests])
    print("prepared tool hashes", [(t.name, t.sha256) for t in prepared.tools])
    code = """
import json, os
from pathlib import Path
import oversight, oversight_contracts
assert str(Path(oversight.__file__).resolve()).startswith('/T/backend/src/')
assert str(Path(oversight_contracts.__file__).resolve()).startswith('/T/contracts/src/')
assert os.environ['UV_OFFLINE'] == os.environ['UV_NO_SYNC'] == '1'
import resource
assert resource.getrlimit(resource.RLIMIT_AS) == (512*1024**2, 512*1024**2)
assert 'META_API_KEY' not in os.environ
try: Path('/opt/deps/backend/forbidden').write_text('bad')
except OSError: pass
else: raise AssertionError('dependency write')
print(json.dumps(dict(backend=oversight.__file__, contracts=oversight_contracts.__file__)))
"""
    node = "/opt/node/node"
    commands: list[dict[str, Any]] = [
        dict(argv=["/opt/deps/backend/bin/python", "-c", code], cwd="/T"),
        dict(argv=[node, "/opt/deps/frontend/typescript/bin/tsc", "--noEmit"], cwd="/T/frontend"),
        dict(
            argv=[node, "/opt/deps/frontend/vite/bin/vite.js", "build", "--outDir", "/state/dist"],
            cwd="/T/frontend",
        ),
    ]
    for command in commands:
        payload = dict(
            command,
            env=scrub_preview_env({"META_API_KEY": "must-be-removed"}, prepared),
            python=command["argv"][0].endswith("python"),
            python_bytes=512 * 1024**2,
            node_heap_mib=1536,
            cores=2,
            file_bytes=16 * 1024**2,
        )
        print("translated command", command)
        handle = await launch_boundary(
            BoundaryConfig(
                argv=(
                    "/opt/python/bin/python",
                    "-I",
                    "-S",
                    "-c",
                    wiring._EXEC,
                    json.dumps(payload),
                ),
                cwd=command["cwd"],
                lifetime_s=120,
                launch_timeout_s=20,
                output_cap=1024**2,
            ),
            roots,
        )
        try:
            async with asyncio.timeout(120):
                await handle.wait()
            assert handle.returncode == 0, handle.stdout + handle.stderr
            print("build/import provenance", handle.stdout)
        finally:
            await handle.stop_verified()
    assert (prepared.state / "dist/index.html").is_file()
    print("readonly mounts", json.dumps([asdict(m) for m in roots.readonly], default=str))
    await deployed_supervisor_death(inputs, prepared)


def test_reviewed_policy_deadlines_resources_and_log(tmp_path):
    from dataclasses import asdict

    from oversight.preview_startup import PreviewStartupReport, serialize_preview_startup
    from oversight.settings import resolve_preview_profile
    from oversight.workspace._reviewed_preview import cache_exceeded
    from oversight.workspace.preview_profile import PreviewProfile

    defaults = PreviewProfile()
    assert (defaults.readiness_s, defaults.lease_s, defaults.heartbeat_s, defaults.lifetime_s) == (
        30,
        600,
        10,
        900,
    )
    assert resolve_preview_profile({}, 8710, None).readiness_s == defaults.readiness_s
    for field in (
        "build_memory_bytes",
        "build_cores",
        "nested_memory_bytes",
        "nested_cores",
        "node_heap_mib",
        "output_file_bytes",
        "output_total_bytes",
        "output_files",
        "cache_bytes",
        "diagnostics_bytes",
        "queue_cap",
    ):
        value = asdict(defaults)[field]
        resolved = resolve_preview_profile(
            {"OVERSIGHT_PREVIEW_" + field.upper(): str(value * 2)}, 8710, None
        )
        assert asdict(resolved)[field] == value * 2
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "sparse").write_bytes(b"x" * 16)
    assert cache_exceeded(cache, 15)
    assert not cache_exceeded(cache, 16)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "not-cache").write_bytes(b"x" * 100)
    (cache / "link").symlink_to(outside, target_is_directory=True)
    assert not cache_exceeded(cache, 16)
    cache_link = tmp_path / "cache-link"
    cache_link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        cache_exceeded(cache_link, 1000)
    report = PreviewStartupReport(
        (("python",),),
        "/T",
        "fixture",
        (),
        "per-process limits only; aggregate usage is not enforced",
        "",
        "/runtime/previews/id/logs/build.log",
    )
    assert len(serialize_preview_startup(report).encode()) < 4096


async def test_reviewed_owned_handle_before_entry_failure(tmp_path):
    from oversight.workspace.preview_boundary import (
        BoundaryConfig,
        BoundaryUnavailable,
        launch_boundary,
    )

    from .preview_boundary_support import prepared

    roots = await asyncio.to_thread(prepared, tmp_path / "boundary")
    captured = []
    with pytest.raises(BoundaryUnavailable, match="unapproved boundary executable"):
        await launch_boundary(BoundaryConfig(("/unapproved",)), roots, on_ownership=captured.append)
    assert len(captured) == 1 and captured[0].verified_dead
    assert not captured[0].processes


async def deployed_supervisor_death(inputs, prepared):
    """Actual supervisor process/EOF handoff and guardian, using the real prepared layout."""
    import json
    import select
    import socket
    import time
    from dataclasses import asdict
    from types import SimpleNamespace

    from oversight.workspace.isolation import open_pidfd
    from oversight.workspace.preview_process import PreviewProcess, bind_listener
    from oversight.workspace.preview_profile import PreviewProfile
    from oversight.workspace.preview_registry import (
        PreviewRegistry,
        kill_pinned,
        reclaim_registered,
    )

    runtime = inputs.tree.parent / "supervisor-runtime"
    runtime.mkdir()
    registry = PreviewRegistry(runtime)
    listener = bind_listener((0,))
    port = listener.getsockname()[1]
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        api = reservation.getsockname()[1]
    code = f"""
import asyncio, json
async def handle(reader, writer):
    try:
        raw = await reader.readuntil(b'\\r\\n\\r\\n')
        if raw.split(b' ')[1] == b'/events':
            event = {{'type':'run.started','payload':{{'run_id':'fresh-deployed'}}}}
            body = b'data: ' + json.dumps(event).encode() + b'\\n\\n'
            kind = b'text/event-stream'
        else:
            body = b'{{"ok":true,"run_id":"fresh-deployed"}}'
            kind = b'application/json'
        writer.write(b'HTTP/1.1 200 OK\\r\\nContent-Type: ' + kind +
            b'\\r\\nContent-Length: ' + str(len(body)).encode() + b'\\r\\n\\r\\n' + body)
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()
async def main():
    server = await asyncio.start_server(handle, '127.0.0.1', {api})
    async with server: await server.serve_forever()
asyncio.run(main())
"""
    launch = dict(
        runtime=str(runtime),
        readiness_s=120,
        lease_deadline=time.monotonic() + 180,
        absolute_deadline=time.monotonic() + 180,
        reviewed=dict(
            inputs=json.loads(json.dumps(asdict(inputs), default=str)),
            policy=asdict(PreviewProfile()),
            parent_authority="localhost:8710",
            run_id="fresh-deployed",
            api_port=api,
            public_port=port,
            commands=[
                dict(argv=["/opt/deps/backend/bin/python", "-c", code], cwd="/T", timeout_s=120)
            ],
        ),
    )
    registry.write(
        dict(
            version=1,
            runtime=str(runtime),
            phase="intent",
            preview_id="preview-" + "d" * 32,
            launch=launch,
        )
    )
    process = PreviewProcess(registry, port)
    process.public_authority = f"127.0.0.1:{port}"
    profile = SimpleNamespace(
        readiness_s=120,
        reviewed_app=SimpleNamespace(launch=SimpleNamespace(build=())),
        log_secrets=(),
    )
    pinned = []
    try:
        await process.launch(listener, cast(PreviewProfile, profile))
        assert await process.ready(120)
        data = registry.read()
        assert data is not None and data["guardian"] and data["phase"] == "executing"
        for identity in data["boundary"]["processes"].values():
            pinned.append(open_pidfd(identity["pid"]))
        supervisor = open_pidfd(data["supervisor"]["pid"])
        try:
            kill_pinned(supervisor)
        finally:
            os.close(supervisor)
        for _ in range(1000):
            with socket.socket() as available:
                available.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    available.bind(("127.0.0.1", port))
                except OSError:
                    pass
                else:
                    assert all(select.select([fd], [], [], 0)[0] for fd in pinned)
                    break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("guardian did not release public reservation after descendant death")
        await reclaim_registered(data)
        from oversight.workspace._reviewed_preview import retire_registered

        await asyncio.to_thread(retire_registered, data.get("prepared_cleanup", []))
        print(
            "actual supervisor death and reservation witnesses", data["boundary"], data["guardian"]
        )
    finally:
        for fd in pinned:
            os.close(fd)
        try:
            await process.stop()
        except Exception:
            # Supervisor SIGKILL sends no verified-dead report. Registry remains for recovery.
            assert registry.read() is not None
        if process.pidfd is not None:
            os.close(process.pidfd)
            process.pidfd = None


async def gateway_request(port, authority):
    async with asyncio.timeout(5):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write(
                f"GET /graph HTTP/1.1\r\nHost: {authority}\r\n"
                f"Origin: http://{authority}\r\nConnection: close\r\n\r\n".encode()
            )
            await writer.drain()
            return await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()


@pytest.mark.parametrize("operand", ["home", "repository"])
async def test_reviewed_builtin_exclusions_are_canonical(inputs, tmp_path, monkeypatch, operand):
    from oversight.workspace import _reviewed_preview as wiring

    excluded = tmp_path / "excluded"
    excluded.mkdir()
    alias = tmp_path / "excluded-alias"
    alias.symlink_to(excluded, target_is_directory=True)
    if operand == "home":
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: alias))
    else:
        monkeypatch.setattr(wiring, "REPO_ROOT", alias)
    with pytest.raises(ValueError, match="excluded"):
        wiring.validate_storage(replace(inputs, excluded_roots=(), tree=excluded))


@pytest.mark.parametrize("field", ["tree", "state", "root"])
async def test_reviewed_writable_boundary_roots_exclude_original(inputs, field):
    from oversight.workspace._reviewed_preview import boundary_roots

    prepared = await prepare_reviewed(inputs)
    with pytest.raises(ValueError, match="excluded"):
        boundary_roots(
            replace(prepared, **{field: inputs.excluded_roots[0]}), inputs.excluded_roots
        )


def test_reviewed_command_translation_preserves_frozen_tool_identities(inputs):
    from types import SimpleNamespace

    from oversight.workspace._reviewed_preview import translate_path, verify_tool_bindings
    from oversight.workspace.preview_launch_spec import PreviewLaunchSpec

    root = next(i.root for i in inputs.inventories if i.role == "tool")
    identities = {t.name: t for t in inputs.tools}
    spec = SimpleNamespace(
        resources=(root / "bin/python", root / "bin/node"),
        executables=(identities["python"], identities["node"]),
    )
    verify_tool_bindings(cast(PreviewLaunchSpec, spec), inputs)
    assert translate_path(inputs.tree / "frontend", inputs) == "/workspace/frontend"
    assert translate_path(inputs.preview_directory / "state/dist", inputs) == "/state/dist"
    with pytest.raises(ValueError, match="no unique"):
        translate_path(Path("/unregistered/host/executable"), inputs)
    spec.executables = (replace(identities["python"], sha256="f" * 64), identities["node"])
    with pytest.raises(ValueError, match="frozen executable"):
        verify_tool_bindings(cast(PreviewLaunchSpec, spec), inputs)


async def test_reviewed_prepared_copy_recovery_ownership(inputs, tmp_path):
    from oversight.workspace._reviewed_preview import prepared_cleanup, retire_registered

    prepared = await prepare_reviewed(inputs)

    def exercise():
        records = prepared_cleanup(prepared, inputs)
        assert records and all(Path(record["path"]).exists() for record in records)
        first = Path(records[0]["path"])
        preserved = first.with_name(first.name + "-preserved")
        first.rename(preserved)
        first.mkdir(mode=0o700)
        (first / "must-survive").write_text("unowned replacement")
        with pytest.raises(ValueError, match="identity changed"):
            retire_registered(records)
        assert (first / "must-survive").read_text() == "unowned replacement"
        (first / "must-survive").unlink()
        first.rmdir()
        preserved.rename(first)
        retire_registered(records)
        assert not first.exists()
        retire_registered(records)  # Death after cleanup but before registry clear is recoverable.

    await asyncio.to_thread(exercise)


async def test_reviewed_boundary_listener_and_escape_denial(tmp_path):
    import json
    import socket

    from oversight.workspace.preview_boundary import launch_boundary

    from .preview_boundary_support import config, prepared

    roots = await asyncio.to_thread(prepared, tmp_path / "boundary")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    control, report = os.pipe()
    for fd in (control, report, listener.fileno()):
        os.set_inheritable(fd, True)
    try:
        payload = f"""
import json, os, socket
from pathlib import Path
paths = [{str(ROOT / ".env")!r}, '/proc/self/root' + {str(ROOT / ".env")!r},
         '/proc/{os.getpid()}/fd/{listener.fileno()}', '/mnt', '/run/WSL']
for path in paths:
    try: os.stat(path)
    except OSError: pass
    else: raise AssertionError('excluded path visible')
visible = []
for entry in Path('/proc/self/fd').iterdir():
    try: os.readlink(entry)
    except OSError: continue
    visible.append(entry.name)
assert set(visible) == {{'0','1','2'}}
assert 'WSL_INTEROP' not in os.environ and 'META_API_KEY' not in os.environ
with socket.socket() as client:
    client.settimeout(1)
    parent_error = client.connect_ex(('127.0.0.1', {listener.getsockname()[1]}))
    assert parent_error != 0
with socket.socket() as client:
    client.settimeout(1)
    outside_error = client.connect_ex(('1.1.1.1', 443))
    assert outside_error != 0
try: socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
except OSError as error: assert error.errno == 1
else: raise AssertionError('AF_VSOCK allowed')
print(json.dumps(dict(parent=parent_error, outside=outside_error, fds=visible)))
"""
        handle = await launch_boundary(config(payload), roots)
        try:
            await handle.wait()
            assert handle.returncode == 0, handle.stderr
            data = json.loads(handle.stdout)
            assert data["parent"] == 111 and data["outside"] == 101
            assert handle.verified_dead
        finally:
            await handle.stop_verified()
    finally:
        listener.close()
        os.close(control)
        os.close(report)


async def test_manager_survives_two_reviewed_build_transitions(
    workspace, tmp_path, monkeypatch, inputs
):
    import threading
    from types import MethodType

    from oversight.workspace import _reviewed_preview as wiring
    from oversight.workspace.preview_process import PreviewProcess
    from oversight.workspace.preview_registry import identity_data

    from .preview_fakes import harness
    from .test_preview_starting import gated

    h = await harness(workspace)
    execution, reader, reports, _, roots = await controlled_execution(tmp_path, monkeypatch, inputs)
    created, process = await gated(h, "ready")
    execution.registry = h.manager.registry
    execution.guardian.registry = h.manager.registry
    h.manager.registry.update(supervisor=identity_data(os.getpid()))
    process.reviewed = True
    process.confirmed_live = MethodType(PreviewProcess.confirmed_live, process)
    execution.spec["commands"].insert(
        1,
        dict(
            argv=["/opt/python/bin/python", "-c", "compile('pass', '<typecheck>', 'exec')"],
            cwd="/T",
            timeout_s=10,
        ),
    )
    transitions = asyncio.Queue()
    releases = [threading.Event(), threading.Event()]
    loop = asyncio.get_running_loop()
    calls = 0

    def pause_transition(*_):
        nonlocal calls
        calls += 1
        if calls > 1:
            loop.call_soon_threadsafe(transitions.put_nowait, calls - 2)
            assert releases[calls - 2].wait(10)
        return roots

    monkeypatch.setattr(wiring, "boundary_roots", pause_transition)
    task = asyncio.create_task(execution.run())
    try:
        assert (await next_report(reports))["kind"] == "confirmed"
        reader.feed_data(b"execute\n")
        assert (await next_report(reports))["kind"] == "executing"
        for index in range(2):
            async with asyncio.timeout(20):
                assert await transitions.get() == index
            data = h.manager.registry.read()
            assert data is not None and execution.handle is not None
            assert data["phase"] == "between_commands" and data["boundary_pending"]
            assert execution.handle.verified_dead
            for _ in range(2):
                assert (await h.manager.get(created.preview_id)).state == "starting"
                assert (
                    await h.manager.renew(created.preview_id, created.lease_token)
                ).state == "starting"
                assert h.manager.has_owner
            releases[index].set()
        assert (await next_report(reports))["kind"] == "ready"
        process.ready_gate.set()
        await h.sink.wait("ready", created.preview_id)
        assert await process.confirmed_live()
    finally:
        for release in releases:
            release.set()
        reader.feed_data(b"stop\n")
        await asyncio.wait_for(task, 15)
        await h.close()


async def launched_after_output(launch, *args, **kwargs):
    handle = await launch(*args, **kwargs)
    for _ in range(500):
        if "visible" in handle.stdout:
            return handle
        await asyncio.sleep(0.01)
    pytest.fail("controlled command did not emit its diagnostic before timeout")


def configure_reviewed_failure(execution, failure, secrets, monkeypatch):
    from functools import partial

    from oversight.workspace import preview_boundary

    diagnostic = "visible\x1b[31m " + " ".join(secrets)
    code = f"print({diagnostic!r}, flush=True); "
    code += "import time; time.sleep(30)" if failure == "timeout" else "raise SystemExit(2)"
    command = dict(
        argv=["/opt/python/bin/python", "-c", code],
        cwd="/T",
        timeout_s=0.1 if failure == "timeout" else 10,
    )
    if failure == "typecheck":
        execution.spec["commands"].insert(1, command)
    elif failure != "readiness":
        execution.spec["commands"][0] = command
    else:
        original_attach = execution.attach_gateway

        async def expire_readiness():
            execution.config["readiness_s"] = 0.2
            await original_attach()

        monkeypatch.setattr(execution, "attach_gateway", expire_readiness)

    if failure == "timeout":
        monkeypatch.setattr(
            preview_boundary,
            "launch_boundary",
            partial(launched_after_output, preview_boundary.launch_boundary),
        )


@pytest.mark.parametrize("failure", ["build", "typecheck", "timeout", "readiness"])
async def test_reviewed_failure_reason_and_sanitized_log(
    workspace, tmp_path, monkeypatch, inputs, failure
):
    import json
    from types import SimpleNamespace

    from oversight.settings import Settings
    from oversight.workspace.preview_process import PreviewProcess
    from oversight.workspace.preview_profile import PreviewProfile

    from .preview_fakes import harness
    from .test_preview_starting import gated

    h = await harness(workspace)
    execution, reader, reports, registry, _ = await controlled_execution(
        tmp_path,
        monkeypatch,
        inputs,
        health_run="stale-run" if failure == "readiness" else "fresh-run",
    )
    settings = Settings(meta_api_key="fixture-meta-value", baseten_api_key="fixture-baseten-value")
    secrets = settings.preview_profile.log_secrets
    assert secrets == (settings.meta_api_key, settings.baseten_api_key)
    assert all(secret not in repr(settings.preview_profile) for secret in secrets)
    configure_reviewed_failure(execution, failure, secrets, monkeypatch)
    created, process = await gated(h, "ready")
    process.reader = asyncio.StreamReader()
    h.manager.lifecycle.profile = cast(
        PreviewProfile,
        SimpleNamespace(
            readiness_s=30, reviewed_app=SimpleNamespace(launch=SimpleNamespace(build=()))
        ),
    )
    task = asyncio.create_task(execution.run())
    expected = "readiness_timeout" if failure == "readiness" else "launch_failed"
    try:
        assert (await next_report(reports))["kind"] == "confirmed"
        reader.feed_data((json.dumps({"log_secrets": secrets}) + "\nexecute\n").encode())
        assert (await next_report(reports))["kind"] == "executing"
        dead = await next_report(reports)
        await asyncio.wait_for(task, 15)
        assert dead["kind"] == "dead" and dead["reason"] == expected
        assert dead["diagnostic"] == ""
        log = execution.inputs.preview_directory / "logs/build.log"
        assert log == registry.runtime / "previews/example/logs/build.log"
        assert log.is_file()
        text = log.read_text()
        if failure != "readiness":
            assert "visible" in text
        assert "\x1b" not in text and all(secret not in text for secret in secrets)
        process.reader.feed_data((json.dumps(dead) + "\n").encode())
        process.ready_result = await PreviewProcess._reviewed_ready(process, 5)
        assert not process.ready_result
        process.ready_gate.set()
        await h.sink.wait("failed", created.preview_id)
        status = await h.manager.get(created.preview_id)
        assert status.state == "failed" and status.reason == expected
        events = repr(h.sink.records) + repr(dead)
        assert "visible" not in events and all(secret not in events for secret in secrets)
    finally:
        reader.feed_data(b"stop\n")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await h.close()
