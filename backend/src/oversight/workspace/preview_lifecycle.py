"""Owned startup work: one deadline, task-local admission locks, observed completion."""

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from oversight.workspace.preview_types import (
    PreviewReason,
    PreviewRestoredError,
    PreviewState,
    preserve_ownership,
)

if TYPE_CHECKING:
    from oversight.workspace.core import Workspace
    from oversight.workspace.preview_barrier import PreviewBarrier, PreviewOwnership
    from oversight.workspace.preview_profile import PreviewProfile


@dataclass
class PreviewLifecycle:
    workspace: "Workspace"
    barrier: "PreviewBarrier"
    profile: "PreviewProfile"
    clock: Callable[[], float]
    deadlines: Callable[[], tuple[float, float]]
    authorize: Callable[["PreviewOwnership"], None]
    launch: Callable[["PreviewOwnership"], Awaitable[None]]
    transition: Callable[[PreviewState, PreviewReason | None], Awaitable[None]]
    drain: Callable[[PreviewReason], Awaitable[None]]
    watch: Callable[[], Awaitable[None]]

    async def start(self, owner: "PreviewOwnership") -> None:
        """Cancellation settles before process.stop can take the channel's sole reader."""
        try:
            queued = time.monotonic()
            # Waiting for admission locks is queue time, bounded by the absolute lifetime.
            async with self.workspace.locks(main=True, agent_id=owner.candidate.agent_id):
                self.authorize(owner)
            owner.queue_s += time.monotonic() - queued
            allowance = self.profile.readiness_s
            if self.profile.reviewed_app is not None:
                allowance += sum(c.timeout_s for c in self.profile.reviewed_app.launch.build)
            ready_deadline = time.monotonic() + allowance
            owner.execution_deadline = ready_deadline
            async with asyncio.timeout(allowance):
                deadline, _ = self.deadlines()
                await self.launch(owner)
                if owner.draining or self.barrier.owner is not owner:
                    return
                owner.launched = True
                assert owner.process is not None
                lease, absolute = self.deadlines()
                if deadline != lease:
                    owner.process.send((f'{{"renew":{lease}}}\n').encode())
                if not await owner.process.ready(max(0, ready_deadline - time.monotonic())):
                    owner.reason = "readiness_timeout"
                    return
                lease, absolute = self.deadlines()
                if self.clock() >= min(lease, absolute):
                    owner.reason = (
                        "lifetime_expired" if self.clock() >= absolute else "lease_expired"
                    )
                    return
                if not owner.draining and self.barrier.owner is owner:
                    self.authorize(owner)
                    await self.transition("ready", None)
        except TimeoutError:
            reason = "readiness_timeout"
            if self.profile.reviewed_app is not None and (
                owner.process is None or owner.process.stage != "readiness"
            ):
                reason = "launch_failed"
            owner.reason = owner.reason or reason

    async def supervise(self, owner: "PreviewOwnership") -> None:
        """Observe failures after returning starting; never leak a task exception."""
        assert owner.launch_task is not None
        try:
            try:
                await asyncio.shield(owner.launch_task)
            except asyncio.CancelledError:
                return
            except Exception:
                owner.reason = owner.reason or "launch_failed"
            if self.barrier.owner is not owner or owner.draining:
                return
            if owner.reason is not None:
                await self.drain(owner.reason)
            else:
                await self.watch()
        except (PreviewRestoredError, asyncio.CancelledError):
            # Verified restoration refuses this operation; the barrier owns finalization.
            # Uncertain restoration already latched the persistent refusal at its source.
            return
        except Exception:
            self.workspace.refuse_unverified_cleanup(preview=True)

    async def failed_admission(self) -> None:
        owner = self.barrier.owner
        if owner is not None:
            owner.reason = owner.reason or "launch_failed"
            task = asyncio.ensure_future(self.drain(owner.reason))
            # Import locally: the barrier also uses quiesce before namespace cleanup.
            from oversight.workspace.preview_barrier import settle

            await settle(task)


async def quiesce(owner: "PreviewOwnership") -> None:
    """Settle spawn ownership and handoff before the cleanup reader takes control."""
    task = owner.launch_task
    if task is None:
        return
    if not task.done():
        task.cancel()
    # Supervision owns classification; cleanup owns verified namespace death.
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task


async def finish_supervision(owner: "PreviewOwnership", watcher: asyncio.Task[None] | None) -> None:
    if (
        owner.cleanup is not None
        and owner.cleanup.done()
        and watcher is not None
        and watcher is not asyncio.current_task()
    ):
        watcher.cancel()
        await preserve_ownership(asyncio.gather(watcher, return_exceptions=True))
