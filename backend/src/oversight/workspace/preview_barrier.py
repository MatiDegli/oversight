"""Git admission and snapshot restitution. No tree lock is acquired beneath the Git lease."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from oversight.workspace.integrity import IntegrityError
from oversight.workspace.locks import offload
from oversight.workspace.preview_process import PreviewProcess
from oversight.workspace.preview_types import (
    PreviewCandidate,
    PreviewError,
    PreviewReason,
    PreviewState,
)
from oversight.workspace.snapshot import MetadataSnapshot, finish_snapshot, take_snapshot

if TYPE_CHECKING:
    from oversight.workspace.core import Tree, Workspace


async def settle(task: asyncio.Task[None]) -> None:
    """Keep the exclusive caller's lease through repeated cancellation of async cleanup."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    task.result()


@dataclass
class PreviewOwnership:
    candidate: PreviewCandidate
    snapshot: MetadataSnapshot
    process: PreviewProcess | None = None
    snapshot_finished: bool = False
    comparison_finished: bool = False
    reason: PreviewReason | None = None
    cleanup: asyncio.Task[None] | None = None


class PreviewBarrier:
    def __init__(
        self,
        workspace: "Workspace",
        transition: Callable[[PreviewState, PreviewReason], Awaitable[None]],
        terminal: Callable[[PreviewReason], Awaitable[None]],
    ) -> None:
        self.workspace = workspace
        self.transition = transition
        self.terminal = terminal
        self.owner: PreviewOwnership | None = None
        self.contaminated: dict[str, tuple[int, str]] = {}
        self.finalizer: asyncio.Task[None] | None = None
        workspace.operations.set_git_hook(self.before_git)

    async def capture(self, candidate: PreviewCandidate) -> PreviewOwnership:
        snapshot = await offload(
            partial(
                take_snapshot,
                candidate.tree.git_identity,
                self.workspace.integrity,
                10,
            )
        )
        owner = PreviewOwnership(candidate, snapshot)
        self.owner = owner
        return owner

    async def before_git(self) -> None:
        """Runs after exclusive acquisition, including operations.git_lease callers."""
        self.workspace.integrity.before_git()
        owner = self.owner
        if owner is not None and not owner.snapshot_finished:
            await self.drain_namespace(owner.reason or "mutation")
            # Comparison needs this preview's tree lock, possibly held by a queued caller.
            # Mark it pending before yielding; candidate/merge ports refuse pending evidence.
            if self.finalizer is None or self.finalizer.done():
                self.finalizer = asyncio.create_task(self.finish_pending())
        self.workspace.integrity.before_git()

    async def drain_namespace(self, reason: PreviewReason) -> None:
        owner = self.owner
        if owner is None or owner.snapshot_finished:
            return
        if owner.cleanup is None:
            owner.reason = reason
            owner.cleanup = asyncio.create_task(self._cleanup(owner, reason))
        try:
            await asyncio.shield(owner.cleanup)
        except asyncio.CancelledError:
            await settle(owner.cleanup)
            owner.cleanup.result()
            raise

    async def _cleanup(self, owner: PreviewOwnership, reason: PreviewReason) -> None:
        try:
            # A sink failure must not leave execution running. Retain it until death/restoration.
            failure: BaseException | None = None
            try:
                await self.transition("stopping", reason)
            except BaseException as error:
                failure = error
            if owner.process is not None:
                await owner.process.stop()
            try:
                await offload(partial(finish_snapshot, owner.snapshot, self.workspace.integrity))
            except IntegrityError:
                if self.workspace.integrity.main_suspect:
                    raise
                # Verified restoration still suspends rules, just as the suite boundary does.
                owner.reason = "worktree_changed"
                self.contaminated[owner.candidate.agent_id] = (
                    owner.candidate.revision,
                    owner.candidate.head_commit,
                )
            owner.snapshot_finished = True
            if failure is not None:
                raise failure
        except BaseException:
            self.workspace.refuse_unverified_cleanup(preview=True)
            with_sink = PreviewError("Live preview cleanup is unverified; restart is required.")
            try:
                await self.transition("failed", "cleanup_unverified")
            finally:
                raise with_sink from None

    async def compare(self, tree: "Tree") -> None:
        owner = self.owner
        if owner is None or owner.candidate.tree is not tree or owner.comparison_finished:
            return
        if not owner.snapshot_finished:
            raise PreviewError("Live preview evidence is pending.")
        candidate = owner.candidate
        status = await self.workspace.git_ok(tree, "status", "--porcelain", "--untracked-files=all")
        head = (await self.workspace.git_ok(tree, "rev-parse", "HEAD")).strip()
        if status or head != candidate.head_commit or not candidate.current():
            owner.reason = "worktree_changed"
            self.contaminated[candidate.agent_id] = (candidate.revision, candidate.head_commit)
        await self.terminal(owner.reason or "closed")
        owner.comparison_finished = True
        self.owner = None

    async def finish_pending(self) -> None:
        owner = self.owner
        if owner is None:
            return
        try:
            async with (
                self.workspace.locks(main=True, agent_id=owner.candidate.agent_id),
                self.workspace.git_lease(),
            ):
                await self.compare(owner.candidate.tree)
        except BaseException:
            # The original waiter sees failures at the choke point; background comparison
            # failures latch as well, rather than leaking an unobserved task exception.
            self.workspace.refuse_unverified_cleanup(preview=True)

    async def evidence(self, tree: "Tree", revision: int, head: str) -> None:
        await self.compare(tree)
        previous = self.contaminated.get(tree.agent_id or "")
        if previous is not None:
            if revision <= previous[0] or head == previous[1]:
                raise PreviewError("Live preview changed the worktree; request a new revision.")
            del self.contaminated[tree.agent_id or ""]
