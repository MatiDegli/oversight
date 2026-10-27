"""Helpers for the change manager tests: a workspace whose git can be armed to fail, a harness with
a real event store, and a fake runtime that drives the manager through the sink calls."""

import asyncio
import functools
import sys
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import pytest
from pydantic import BaseModel, TypeAdapter

from oversight.agents import AgentTask, RunEnd
from oversight.changes import ChangeManager
from oversight.events import EventPayload, EventStore, StoredEvent
from oversight.graph import WorkspaceGraph
from oversight.workspace import Author, LockOrderError, ProcessResult, Tree, Workspace
from oversight_contracts.events import Limits, ServerEvent
from oversight_contracts.graph import GraphSnapshot

TEMPLATE = {
    "pyproject.toml": "[tool.pytest.ini_options]\ntestpaths = ['tests']\npythonpath = ['.']\n",
    "shop/__init__.py": "",
    "shop/mod.py": (
        "def value() -> int:\n    return 1\n\n\ndef twice() -> int:\n    return value() + value()\n"
    ),
    "tests/test_mod.py": (
        "from shop.mod import value\n\n\ndef test_value() -> None:\n    assert value() > 0\n"
    ),
}
VALUE = "shop.mod:value"
TWICE = "shop.mod:twice"
THRICE = "shop.mod:thrice"
ADD_THRICE = ("shop/mod.py", "+ value()\n", "+ value()\n\n\ndef thrice() -> int:\n    return 3\n")
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
GIT_TIMEOUT_S = 29.0
SERVER_EVENT: TypeAdapter[Any] = TypeAdapter(ServerEvent)

type Mode = Literal["fail", "timeout", "after", "cancel", "cut"]


@dataclass
class Fault:
    subcommand: str
    tree: str
    mode: Mode
    left: int


@dataclass(frozen=True)
class Call:
    tree: str
    args: tuple[str, ...]
    timeout_s: float
    held: tuple[str, ...]


class FaultyWorkspace(Workspace):
    """Records every git call, and fails or times out the armed ones: "fail" and "timeout" never
    run git, "after" runs it and then reports a timeout, "cancel" runs it and then cancels the
    calling task, which waits on an event that never comes, and "cut" cancels the calling task
    before git runs."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.faults: list[Fault] = []
        self.calls: list[Call] = []

    def arm(self, subcommand: str, *, tree: str = "main", mode: Mode = "fail") -> None:
        self.faults.append(Fault(subcommand, tree, mode, 1))

    async def git(
        self,
        tree: Tree,
        *args: str,
        timeout_s: float = 30,
        author: Author | None = None,
        date: str | None = None,
    ) -> ProcessResult:
        name = tree.agent_id or "main"
        held = tuple(
            n
            for n in ("main", "agent-1", "agent-2")
            if self.operations.holds(main=n == "main", agent_id=None if n == "main" else n)
        )
        self.calls.append(Call(name, args, timeout_s, held))
        for fault in self.faults:
            if fault.left and fault.subcommand == args[0] and fault.tree == name:
                fault.left -= 1
                if not self.operations.holds(main=tree.agent_id is None, agent_id=tree.agent_id):
                    raise LockOrderError("an armed git call without the tree's lock")
                if fault.mode == "cut":
                    current = asyncio.current_task()
                    assert current is not None
                    current.cancel()
                    await asyncio.Event().wait()
                if fault.mode in ("after", "cancel"):
                    real = await super().git(
                        tree, *args, timeout_s=timeout_s, author=author, date=date
                    )
                    if fault.mode == "cancel":
                        current = asyncio.current_task()
                        assert current is not None
                        current.cancel()
                        await asyncio.Event().wait()
                    return replace(real, returncode=None, timed_out=True)
                if fault.mode == "timeout":
                    return ProcessResult(None, "", "", True, False, 0)
                return ProcessResult(1, "", "fatal: an armed test failure", False, False, 0)
        return await super().git(tree, *args, timeout_s=timeout_s, author=author, date=date)


def bounded[**P](test: Callable[P, Awaitable[None]]) -> Callable[P, Coroutine[Any, Any, None]]:
    """Run a test under a 60 s deadline."""

    @functools.wraps(test)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> None:
        async with asyncio.timeout(60):
            await test(*args, **kwargs)

    return wrapper


async def queued_callbacks_ran() -> None:
    """Return once the callbacks queued so far have run, in their order: a task that a cancel woke
    has taken it and is waiting again. It waits on a loop callback, never on time."""
    loop = asyncio.get_running_loop()
    turn = loop.create_future()
    loop.call_soon(turn.set_result, None)
    await turn


async def cancel_twice(decision: asyncio.Task[None]) -> None:
    """Cancel a decision held in a close-out publish, again once it waits through the first cancel:
    the second cancel ends its wait, while the publish stays held."""
    decision.cancel()
    await queued_callbacks_ran()
    decision.cancel()
    with pytest.raises(asyncio.CancelledError):
        await decision


def limits() -> Limits:
    return Limits(
        max_steps=20,
        max_output_tokens=4096,
        max_wall_clock_s=120,
        test_timeout_s=60,
        cone_max_depth=4,
        cone_max_nodes=40,
    )


def write_tree(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return root


@dataclass
class Harness:
    tmp_path: Path
    workspace: FaultyWorkspace
    store: EventStore
    base_commit: str
    main_graph: WorkspaceGraph
    log: list[StoredEvent] = field(default_factory=lambda: list[StoredEvent]())
    fail_once: type[BaseModel] | None = None
    """The next `fail_times` payloads of this type fail to publish, as a closing store would."""
    fail_times: int = 1
    fail_script: list[type[BaseModel]] = field(default_factory=lambda: list[type[BaseModel]]())
    """Publishes that fail in this order: one whose type is the head of the list fails and pops it,
    any other goes through; for a store that fails a resolution once, then a delta once."""
    hold: type[BaseModel] | None = None
    """The next payload of this type is committed, then its receipt waits for `release`, with
    `held` set meanwhile, as a slow store's would."""
    held: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    _manager: ChangeManager | None = None

    @property
    def manager(self) -> ChangeManager:
        assert self._manager is not None
        return self._manager

    async def publish(self, payload: EventPayload) -> StoredEvent:
        if self.fail_once is not None and isinstance(payload, self.fail_once):
            self.fail_times -= 1
            if self.fail_times <= 0:
                self.fail_once, self.fail_times = None, 1
            raise RuntimeError("an armed publish failure")
        if self.fail_script and isinstance(payload, self.fail_script[0]):
            self.fail_script.pop(0)
            raise RuntimeError("a scripted publish failure")
        event = await self.store.publish(payload)
        self.log.append(event)
        if self.hold is not None and isinstance(payload, self.hold):
            self.hold = None
            self.held.set()
            await self.release.wait()
        return event

    # --- The fake runtime -------------------------------------------------------------------

    @staticmethod
    def task(n: int) -> AgentTask:
        return AgentTask(f"agent-{n}", f"change-{n}", "A" if n == 1 else "B", "Do it.", limits())

    async def open(self, n: int) -> Tree:
        return await self.manager.open(self.task(n), "fake")

    async def edit(self, n: int, path: str, old: str, new: str) -> bool:
        """Write an edit into agent n's worktree, as edit_file does, then report it."""
        tree = self.workspace.worktree(f"agent-{n}")
        read = await tree.read_text(path)
        assert read.content.count(old) == 1
        await tree.write_text(path, read.content.replace(old, new), expected_hash=read.hash)
        return await self.manager.file_edited(f"agent-{n}", path)

    async def finish(self, n: int, summary: str | None = "Done.", steps: int = 3) -> bool:
        """End agent n's run as the runtime does: propose, then report the end."""
        ok = await self.manager.proposed(f"agent-{n}", summary)
        end = (
            RunEnd("ready_for_review", None, steps, summary)
            if ok and summary is not None
            else RunEnd("halted", "Its change could not be proposed.", steps, None)
        )
        self.manager.run_ended(f"agent-{n}", end)
        return ok

    async def run_agent(self, n: int, *edits: tuple[str, str, str]) -> Tree:
        tree = await self.open(n)
        for path, old, new in edits:
            assert await self.edit(n, path, old, new)
        assert await self.finish(n)
        return tree

    # --- Inspection -------------------------------------------------------------------------

    async def main_git(self, *args: str) -> ProcessResult:
        async with self.workspace.locks(main=True):
            return await self.workspace.git(self.workspace.main, *args)

    async def main_head(self) -> str:
        return (await self.main_git("rev-parse", "HEAD")).stdout.strip()

    async def has_branch(self, agent: str) -> bool:
        found = await self.main_git("rev-parse", "--verify", "--quiet", f"refs/heads/{agent}")
        return found.returncode == 0

    def worktree_dir(self, agent: str) -> Path:
        return self.workspace.runtime_root / "worktrees" / agent

    def events(self) -> list[Any]:
        """Every event, validated against the contract, with no temporary path, and each delta's
        `from_version` chained to the graph the UI holds."""
        events = [SERVER_EVENT.validate_json(e.envelope) for e in sorted(self.log, key=seq)]
        for stored in self.log:
            assert str(self.tmp_path) not in stored.envelope
        version: str | None = None
        for event in events:
            if event.type == "graph.snapshot":
                version = event.payload.version
            elif event.type == "graph.delta":
                assert event.payload.from_version == version
                version = event.payload.to_version
        return events

    def types(self) -> list[str]:
        return [event.type for event in self.events()]

    def payloads[T: BaseModel](self, kind: type[T]) -> list[T]:
        return [e.payload for e in self.events() if isinstance(e.payload, kind)]

    async def close(self) -> None:
        try:
            if self._manager is not None:
                await self.manager.aclose()
            await self.store.close()
            self.events()
        finally:
            self.workspace.release_runtime()


def seq(event: StoredEvent) -> int:
    return event.seq


async def start_harness(tmp_path: Path) -> Harness:
    template = await asyncio.to_thread(write_tree, tmp_path / "template", TEMPLATE)
    root = tmp_path / "rt"
    await asyncio.to_thread(functools.partial(root.mkdir, parents=True))
    workspace = FaultyWorkspace(
        root / ".runtime", template, pytest_pin="9.1.1", demo_python=Path(sys.executable)
    )
    await workspace.acquire_runtime()
    base = await workspace.reset()
    store = EventStore(tmp_path / "run.sqlite", "run-20261026-120000", clock=lambda: NOW)
    await store.start()
    main_graph = WorkspaceGraph(workspace.main.root)
    harness = Harness(tmp_path, workspace, store, base, main_graph)
    snapshot: GraphSnapshot = await asyncio.to_thread(main_graph.scan, base)
    await harness.publish(snapshot)
    harness._manager = ChangeManager(
        workspace,
        harness.publish,
        main_graph=main_graph,
        main_snapshot=snapshot,
        clock=lambda: NOW,
        git_timeout_s=GIT_TIMEOUT_S,
    )
    return harness
