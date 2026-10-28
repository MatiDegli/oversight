"""Real namespace acceptance for local sockets without outside network access."""

import json
import os
import sys
from pathlib import Path

import pytest

from oversight.workspace import Workspace, capability
from oversight.workspace import isolation as iso
from oversight.workspace.capability import check_effect
from oversight.workspace.isolation_entry import FLAGS, HELPER
from oversight.workspace.isolation_probe import isolation_probe
from oversight.workspace.runner import run_process

pytestmark = pytest.mark.anyio

LOCAL_SOCKET_TEST = """
import socket

def test_local_connection():
    with socket.socket() as server, socket.socket() as client:
        server.settimeout(1)
        client.settimeout(1)
        server.bind(('127.0.0.1', 0))
        server.listen(1)
        client.connect(server.getsockname())
        peer, _ = server.accept()
        with peer:
            client.sendall(b'local')
            assert peer.recv(5) == b'local'
"""

NETWORK_STATE = """
import errno, fcntl, json, os, socket, struct
from pathlib import Path
with socket.socket() as client:
    flags = struct.unpack('16sH22x', fcntl.ioctl(
        client, 0x8913, struct.pack('16sH22x', b'lo', 0)))[1]
    client.settimeout(1)
    outside_errno = client.connect_ex(('192.0.2.1', 9))
assert outside_errno == errno.ENETUNREACH, outside_errno
caps = {row.split(':')[0]: row.split()[1]
        for row in Path('/proc/self/status').read_text().splitlines()
        if row.startswith('Cap')}
print(json.dumps(dict(up=bool(flags & 1), outside_errno=outside_errno,
                      net=os.readlink('/proc/self/ns/net'), caps=caps)))
"""


@pytest.fixture
async def isolated(tmp_path: Path) -> iso.IsolatedRunner:
    runner = iso.IsolatedRunner(tmp_path)
    try:
        await runner.preflight()
    except iso.IsolationUnavailable as error:
        pytest.skip(str(error))
    return runner


async def test_isolated_suite_can_bind_and_connect_loopback(
    isolated: iso.IsolatedRunner, workspace: Workspace, demo_python: Path
) -> None:
    workspace._demo_python = demo_python
    (workspace.main.root / "tests/test_local.py").write_text(LOCAL_SOCKET_TEST)
    run = await workspace.run_tests(
        workspace.main, timeout_s=15, case_ids=("tests/test_local.py",), spawn=isolated
    )
    assert run.result.outcome == "passed", run.result
    assert len(run.cases) == 1 and not run.problems
    assert isolated.entry is not None and isolated.cleanup_complete


async def test_loopback_up_has_no_outside_route_or_retained_capabilities(
    isolated: iso.IsolatedRunner, tmp_path: Path
) -> None:
    # Compare to the existing unshare capabilities: setup must acquire/retain no new ones.
    baseline = await run_process(
        [iso.unshare_binary(), *FLAGS, sys.executable, "-c", NETWORK_STATE],
        cwd=tmp_path,
        timeout_s=5,
    )
    assert baseline.returncode == 0, baseline.stderr
    result = await isolated([sys.executable, "-c", NETWORK_STATE], cwd=tmp_path, timeout_s=5)
    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout)
    assert state["up"] is True
    assert state["net"] != os.readlink("/proc/self/ns/net")
    assert state["caps"] == json.loads(baseline.stdout)["caps"]
    assert isolated.entry is not None and isolated.cleanup_complete


async def test_failed_loopback_setup_runs_isolated_and_records_diagnostic(
    isolated: iso.IsolatedRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inject_failure = """
import errno, fcntl
original_ioctl = fcntl.ioctl
def failing_ioctl(fd, request, *args):
    if request == 0x8914:
        raise OSError(errno.EPERM, 'forced loopback failure')
    return original_ioctl(fd, request, *args)
fcntl.ioctl = failing_ioctl
"""
    monkeypatch.setattr(iso, "HELPER", inject_failure + HELPER)
    result = await isolated([sys.executable, "-c", NETWORK_STATE], cwd=tmp_path, timeout_s=5)
    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout)
    assert state["up"] is False
    assert state["net"] != os.readlink("/proc/self/ns/net")
    assert "forced loopback failure" in result.stderr
    assert isolated.entry is not None and isolated.cleanup_complete


async def test_capability_and_probe_accept_loopback_only(
    isolated: iso.IsolatedRunner,
    tmp_path: Path,
    template: Path,
    demo_python: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Both public probes exercise the real helper, with a listening server on the host.
    monkeypatch.setattr(capability, "IsolatedRunner", iso.IsolatedRunner)
    await check_effect(tmp_path)
    (template / "tests/test_local.py").write_text(LOCAL_SOCKET_TEST)

    async def venv(self: Workspace) -> None:
        self._demo_python = demo_python

    monkeypatch.setattr(Workspace, "ensure_demo_venv", venv)
    result = await isolation_probe(tmp_path / ".runtime", template, pytest_pin="9.1.1")
    assert result["status"] == "confirmed", result
    assert result["outside_accepted"] is True and result["inside_refused"] is True


@pytest.mark.parametrize("probe", ["capability", "suite"])
async def test_probes_reject_reachable_host(
    isolated: iso.IsolatedRunner,
    tmp_path: Path,
    template: Path,
    demo_python: Path,
    monkeypatch: pytest.MonkeyPatch,
    probe: str,
) -> None:
    # Model a successful connection across the boundary; neither probe may certify C.
    monkeypatch.setattr(capability, "IsolatedRunner", iso.IsolatedRunner)
    monkeypatch.setattr(
        iso,
        "HELPER",
        "import socket\nsocket.socket.connect_ex = lambda *args: 0\n" + HELPER,
    )
    if probe == "capability":
        with pytest.raises(iso.IsolationUnavailable, match="confirmation"):
            await check_effect(tmp_path)
    else:

        async def venv(self: Workspace) -> None:
            self._demo_python = demo_python

        monkeypatch.setattr(Workspace, "ensure_demo_venv", venv)
        result = await isolation_probe(tmp_path / ".runtime", template, pytest_pin="9.1.1")
        assert result["status"] != "confirmed", result
        assert "confirmation" in str(result["reason"])
