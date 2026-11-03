"""Real preview ownership fixtures: C's direct runner and supervisor, never SessionRunner."""

import asyncio
import json
import os
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

from oversight.settings import REPO_ROOT
from oversight.workspace import (
    IsolatedRunner,
    IsolationUnavailable,
    PreviewCandidate,
    PreviewManager,
    PreviewProfile,
    Workspace,
)
from oversight.workspace.preview_registry import identity_data, registered_identity
from oversight.workspace.preview_types import FakeStatusSink

# isort: split
sys.path.insert(0, str(REPO_ROOT))
from tools.preview_acceptance_checks import free_port, namespace_members


async def preflight(root: Path) -> None:
    await asyncio.to_thread(lambda: root.mkdir(parents=True, exist_ok=True))
    try:
        await IsolatedRunner(root).preflight()
    except IsolationUnavailable as error:
        pytest.skip(str(error))


@pytest.fixture
def no_kernel_startup_probe():
    """Override backend/conftest's autouse fake; SessionIsolation must use the real kernel."""


@dataclass
class RealPreview:
    workspace: Workspace
    manager: PreviewManager
    head: str
    sink: FakeStatusSink
    report: Path

    async def start(self):
        receipt = await self.manager.start(
            run_id="run-20261103-000000", agent_id="agent-1", revision=1, head_commit=self.head
        )
        await self.sink.wait("ready", receipt.preview_id)
        data = self.manager.registry.read()
        assert data is not None
        for name in ("supervisor", "unshare", "namespace"):
            assert registered_identity(data[name]).alive()
        for name, identity in data["namespaces"].items():
            assert os.readlink(f"/proc/{data['namespace']['pid']}/ns/{name}") == identity
            assert identity != os.readlink(f"/proc/self/ns/{name}")
        assert self.manager.barrier.owner is not None
        process = self.manager.barrier.owner.process
        assert process is not None and await process.confirmed_live()
        return receipt, data


def attack(report: Path, targets: tuple[tuple[str, int], ...], *, descendant: bool = True) -> str:
    # Candidate code is written here, for this test. Only the application executes it under C.
    return f"""
import json, os, signal, socket, time
results = []
for target in {targets!r}:
    try:
        connection = socket.create_connection(target, timeout=0.5)
    except OSError:
        results.append(True)
    else:
        results.append(False)
        connection.close()
child = os.fork() if {descendant!r} else -1
if child == 0:
    os.setsid()
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True:
        time.sleep(60)
with open({str(report)!r}, 'w') as stream:
    json.dump(dict(refused=results, child=child,
                   namespace=os.readlink('/proc/self/ns/pid')), stream)
print('PRIVATE_PREVIEW_OUTPUT_MUST_NOT_ESCAPE', flush=True)
"""


@asynccontextmanager
async def real_preview(
    root: Path, *, code: str = "", lease: float = 30, lifetime: float = 90
) -> AsyncGenerator[RealPreview]:
    runtime = root / ".runtime"
    await preflight(runtime)
    workspace = Workspace(
        runtime, REPO_ROOT / "demo-repo", pytest_pin="9.1.1", demo_python=Path(sys.executable)
    )
    await workspace.acquire_runtime()
    try:
        await workspace.reset()
        await workspace.check_isolation()
        assert workspace.isolation.available
        tree = await workspace.create_worktree("agent-1")
        if code:
            (tree.root / "shop/__init__.py").write_text(code)
            async with workspace.locks(agent_id="agent-1"):
                await workspace.git_ok(tree, "add", "shop/__init__.py")
                await workspace.git_ok(tree, "commit", "-m", "test: exercise real preview C")
        head = await tree.head()

        async def resolve(*args):
            return PreviewCandidate("run-20261103-000000", "agent-1", "change-1", 1, head, tree)

        profile = PreviewProfile(
            adapter=REPO_ROOT / "backend/src/oversight/preview_adapter.py",
            interpreter=Path(sys.executable),
            ports=(free_port(), free_port()),
            origins=("http://127.0.0.1:8710",),
            trusted_demo=True,
            heartbeat_s=0.1,
            lease_s=lease,
            lifetime_s=lifetime,
        )
        manager = PreviewManager(workspace, profile, resolve_candidate=resolve)
        sink = FakeStatusSink()
        manager.set_status_sink(sink)
        yield RealPreview(workspace, manager, head, sink, root / "report.json")
    finally:
        if workspace.previews and not workspace.integrity.main_suspect:
            await workspace.previews.drain("shutdown")
        if not workspace.integrity.main_suspect:
            workspace.release_runtime()


def identities(data):
    return {
        name: registered_identity(data[name]) for name in ("supervisor", "unshare", "namespace")
    }


async def dead(data, *, descendants=()):
    async with asyncio.timeout(15):
        while any(identity.alive() for identity in [*identities(data).values(), *descendants]):  # noqa: ASYNC110 - cross-process death
            await asyncio.sleep(0.02)
    assert namespace_members(data["namespaces"]["pid"]) == []


async def listener_closed(port):
    with pytest.raises(OSError):
        async with asyncio.timeout(2):
            _, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()


def descendant_identities(data):
    return [
        registered_identity(identity_data(pid))
        for pid in namespace_members(data["namespaces"]["pid"])
    ]


async def report_file(path):
    async with asyncio.timeout(10):
        while not path.exists():  # noqa: ASYNC110 - cross-process report
            await asyncio.sleep(0.01)
    return json.loads(path.read_text())


async def spawn_backend(root: Path):
    """A separate backend owner with a real inherited socket and setsid descendant."""
    from oversight.workspace.runner import ProcessCollector, child_env

    report = root / "backend-ready.json"
    source = f"""
import asyncio, json, sys
from dataclasses import asdict
from pathlib import Path
sys.path.insert(0, {str(REPO_ROOT / "backend/tests")!r})
from workspace.preview_acceptance_support import real_preview, attack, descendant_identities
async def main():
    root = Path({str(root)!r})
    async with real_preview(root, code=attack(root / 'application.json', ())) as h:
        receipt, data = await h.start()
        keys = ('supervisor', 'unshare', 'namespace', 'namespaces', 'port')
        public = {{key: data[key] for key in keys}}
        public['profile'] = asdict(h.manager.profile)
        public['descendants'] = [asdict(identity) for identity in descendant_identities(data)]
        Path({str(report)!r}).write_text(json.dumps(public, default=str))
        await asyncio.Event().wait()
asyncio.run(main())
"""
    collector = ProcessCollector(0)
    async with asyncio.timeout(5):
        transport, _ = await asyncio.get_running_loop().subprocess_exec(
            lambda: collector,
            sys.executable,
            "-c",
            source,
            cwd=REPO_ROOT,
            env=child_env(None),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    try:
        data = await report_file(report)
        assert transport.get_returncode() is None
        return transport, collector, data
    except BaseException:
        transport.kill()
        await asyncio.wait_for(collector.exited, 10)
        transport.close()
        raise


def recovery_profile(data):
    values = data["profile"]
    return PreviewProfile(
        **{
            **values,
            "adapter": Path(values["adapter"]),
            "interpreter": Path(values["interpreter"]),
            "ports": tuple(values["ports"]),
            "origins": tuple(values["origins"]),
        }
    )
