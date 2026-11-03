"""Fake application/transport boundaries; no unshare, worktree imports or provider calls."""

import asyncio
import json
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

from oversight.workspace import PreviewCandidate, PreviewManager, PreviewProfile, Tree, Workspace
from oversight.workspace.preview_process import PreviewProcess
from oversight.workspace.preview_registry import PreviewRegistry
from oversight.workspace.preview_types import FakeStatusSink


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class FakeProcess(PreviewProcess):
    def __init__(self, registry: PreviewRegistry, port: int) -> None:
        super().__init__(registry, port)
        self.live = True
        self.stop_started = asyncio.Event()
        self.stop_gate = asyncio.Event()
        self.stop_gate.set()
        self.ready_gate = asyncio.Event()
        self.ready_gate.set()
        self.launch_gate = asyncio.Event()
        self.launch_gate.set()
        self.launch_started = asyncio.Event()
        self.ready_started = asyncio.Event()
        self.launch_failure = False
        self.ready_result = True
        self.stop_failure = False
        self.sent: list[bytes] = []
        self.launches = 0
        self.order: list[str] = []
        self.effective_deadline: float | None = None
        self.absolute_deadline = 0.0
        self.ready_budget = 0.0

    async def launch(self, listener: socket.socket, profile: PreviewProfile) -> None:
        self.launches += 1
        self.order.append("launch")
        assert listener.getsockname() == ("127.0.0.1", self.port)
        assert listener.fileno() >= 3
        data = self.registry.read()
        assert data and data["phase"] == "intent" and data["snapshot"]
        assert "lease_token" not in data and "nonce" not in data
        self.effective_deadline = data["launch"]["lease_deadline"]
        self.absolute_deadline = data["launch"]["absolute_deadline"]
        self.launch_started.set()
        await self.launch_gate.wait()
        if self.launch_failure:
            raise OSError("raw child diagnostic and secret must never escape")
        self.authorize_execution()
        self.order.append("execute")
        listener.close()

    async def ready(self, within_s: float) -> bool:
        self.ready_budget = within_s
        self.ready_started.set()
        await self.ready_gate.wait()
        return self.ready_result

    async def confirmed_live(self) -> bool:
        return self.live

    def send(self, message: bytes) -> None:
        assert "execute" in self.order, "renewal raced unfinished handoff"
        self.effective_deadline = min(json.loads(message)["renew"], self.absolute_deadline)
        self.sent.append(message)

    async def stop(self) -> None:
        self.stop_started.set()
        await self.stop_gate.wait()
        if self.stop_failure:
            raise OSError("fake application output must never escape")
        self.live = False
        self.dead = True
        self.order.append("namespace_dead")
        self.registry.update(phase="dead")


class FakeProfile(PreviewProfile):
    def validate_files(self) -> None:
        """Only the trusted filesystem seam is fake; all startup shape/eligibility checks run."""


def profile() -> PreviewProfile:
    return FakeProfile(
        adapter=Path(__file__).resolve().parents[2] / "src/oversight/preview_adapter.py",
        interpreter=Path(sys.executable),
        ports=(19681, 19682, 19683, 19684),
        origins=("http://127.0.0.1:8710",),
        trusted_demo=True,
    )


@dataclass
class PreviewHarness:
    workspace: Workspace
    tree: Tree
    head: str
    manager: PreviewManager
    sink: FakeStatusSink
    clock: Clock
    processes: list[FakeProcess]

    async def start(self, *, wait_ready=True):
        created = await self.manager.start(
            run_id="run-20261103-000000", agent_id="agent-1", revision=1, head_commit=self.head
        )
        if wait_ready:
            await self.sink.wait("ready", created.preview_id)
        return created

    async def close(self) -> None:
        if not self.workspace.integrity.main_suspect:
            await self.manager.drain("shutdown")
        watcher = self.manager._watcher
        if watcher is not None:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)


async def harness(workspace: Workspace) -> PreviewHarness:
    await workspace.check_isolation()
    tree = await workspace.create_worktree("agent-1")
    head = await tree.head()
    processes: list[FakeProcess] = []

    def factory(registry: PreviewRegistry, port: int) -> FakeProcess:
        process = FakeProcess(registry, port)
        processes.append(process)
        return process

    async def resolve(run: str, agent: str, revision: int, commit: str) -> PreviewCandidate:
        return PreviewCandidate("run-20261103-000000", "agent-1", "change-1", 1, head, tree)

    clock = Clock()
    manager = PreviewManager(
        workspace, profile(), resolve_candidate=resolve, process_factory=factory, clock=clock
    )
    sink = FakeStatusSink()
    manager.set_status_sink(sink)
    return PreviewHarness(workspace, tree, head, manager, sink, clock, processes)
