import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC
from typing import TYPE_CHECKING, Any

from oversight.agents import Publish
from oversight.changes._evidence import WriteEvidence
from oversight.changes._mode import ModeController
from oversight.changes._review_port import ReviewPort
from oversight.changes._state import ChangeRecord, ChangeState, Source
from oversight.events import Clock
from oversight.graph import (
    WorkspaceGraph,
)
from oversight.workspace import (
    ProcessResult,
    Tree,
    Workspace,
    WorkspaceError,
)
from oversight_contracts.events import (
    ErrorReport,
)
from oversight_contracts.graph import GraphSnapshot
from oversight_contracts.policy import ChangeAssessment

if TYPE_CHECKING:
    from oversight.changes._assessment import RulesEvidence, RulesScheduler
    from oversight.changes._policy import PolicyInputs

_log = logging.getLogger("oversight.changes._manager")


async def closed_rules_transaction(rec: ChangeRecord, evidence: WriteEvidence) -> str:
    return "Rules merge transaction is unavailable; review this change yourself (P3a)."


@dataclass
class ChangePorts:
    """Lambdas resolve facade methods at call time, including class-level spies."""

    drain: Callable[[ChangeRecord], Awaitable[None]]
    review: ReviewPort | None = None
    rules_evidence: Callable[["PolicyInputs"], "RulesEvidence"] | None = None
    rules_admission: Callable[[ChangeRecord, ChangeAssessment], str | None] | None = None
    rules_transaction: Callable[[ChangeRecord, WriteEvidence], Awaitable[str | None]] = (
        closed_rules_transaction
    )


@dataclass
class ChangeContext:
    """Dependencies and utilities shared by stages; state has exactly one owner."""

    workspace: Workspace
    publish: Publish
    main_graph: WorkspaceGraph
    clock: Clock
    timeout: float
    state: ChangeState
    ports: ChangePorts
    mode: ModeController
    behavior_diff: bool = False
    assessment: "RulesScheduler" = field(init=False)

    def date(self) -> str:
        return self.clock().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    async def run(self, tree: Tree, *args: str) -> ProcessResult:
        return await self.workspace.git(tree, *args, timeout_s=self.timeout)

    async def git(self, tree: Tree, *args: str, commit_as: str | None = None) -> str:
        """`git_ok` with the manager's timeout; a commit gets an author and the clock's date."""
        if commit_as is None:
            return await self.workspace.git_ok(tree, *args, timeout_s=self.timeout)
        workspace = self.workspace
        author = (
            workspace.user_author() if commit_as == "user" else workspace.agent_author(commit_as)
        )
        return await workspace.git_ok(
            tree, *args, timeout_s=self.timeout, author=author, date=self.date()
        )

    async def yes_no(self, tree: Tree, *args: str) -> bool:
        """A git query that answers with its exit code: 0 yes, 1 no, anything else fails."""
        result = await self.run(tree, *args)
        if result.truncated:
            raise WorkspaceError(f"git {args[0]} failed: output was truncated")
        if result.timed_out or result.returncode not in (0, 1):
            raise WorkspaceError(f"git {args[0]} failed: {self.workspace.redact(result.stderr)}")
        return result.returncode == 0

    async def error(
        self, source: Source, message: str, rec: ChangeRecord | None, *, fatal: bool
    ) -> bool:
        """Publish an `error`; False when the store refused it: nowhere left to say so."""
        report = ErrorReport(
            source=source,
            message=self.workspace.redact(message)[:1000],
            agent_id=rec.agent_id if rec else None,
            change_id=rec.change_id if rec else None,
            fatal=fatal,
        )
        try:
            await self.publish(report)
        except Exception:
            return False
        return True

    def spawn[T](self, work: Coroutine[Any, Any, T]) -> asyncio.Task[T]:
        task = asyncio.get_running_loop().create_task(work)
        self.state.tasks.add(task)
        task.add_done_callback(self.state.tasks.discard)
        return task

    async def whole[T](self, work: Coroutine[Any, Any, T]) -> T:
        """Run `work` as a tracked task and wait for its end through every cancel, then re-raise
        the first cancel. For a publish and the bookkeeping that must match what the store took, so
        a cancel never leaves one without the other; and for a save or rescan on main, whole."""
        task = self.spawn(work)
        cancelled = False
        while not task.done():
            try:
                await asyncio.wait([task])
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError
        return task.result()

    async def scan(self, commit: str) -> GraphSnapshot:
        """M2: `WorkspaceGraph.scan` in a thread. A cancel waits for the thread to return before it
        is re-raised, so no lock is released while the graph is being rewritten."""
        scanning = asyncio.ensure_future(asyncio.to_thread(self.main_graph.scan, commit))
        cancelled = False
        while not scanning.done():
            try:
                await asyncio.wait([scanning])
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            if not scanning.cancelled() and scanning.exception() is not None:
                _log.error("A cancelled scan of main failed: %s", scanning.exception())
            raise asyncio.CancelledError
        return scanning.result()

    async def report(self, source: Source, message: str, *, fatal: bool) -> None:
        """An `error` with no change or agent; a store that refuses it leaves a log line."""
        if not await self.error(source, message, None, fatal=fatal):
            _log.error("The store refused an error event: %s", self.workspace.redact(message))

    @staticmethod
    async def settled(task: asyncio.Task[None]) -> bool:
        """Wait for `task` through one cancel of the caller: True if one came, for the caller to
        re-raise once the outcome is recorded. A second cancel ends the wait; `task` runs on, kept
        on the record, records its own outcome, and the next close-out or merge waits for it."""
        try:
            await asyncio.wait([task])
        except asyncio.CancelledError:
            await asyncio.wait([task])
            return True
        return False
