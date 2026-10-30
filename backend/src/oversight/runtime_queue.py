"""Run-local durable queue, slot admission and dispatch. Restarts never resume old items."""

# Like the other runtime helpers, this composes LiveRun's owned registries.
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from oversight.agents import AgentTask
from oversight.api import ApiError
from oversight.changes import QUEUE_LIMIT, TERMINAL, queue_candidates, queue_transition, queued
from oversight_contracts.commands import AgentCreated, EnqueueTask, QueueRevision, StartAgent
from oversight_contracts.events import ModelProfile
from oversight_contracts.queue import QueuedTask

if TYPE_CHECKING:
    from oversight.app import LiveRun


@dataclass(frozen=True)
class Admission:
    task: AgentTask
    profile: ModelProfile
    queued_task_id: str | None


class Dispatcher:
    def __init__(self, live: LiveRun) -> None:
        self.live = live
        # Restart policy: each LiveRun starts empty, without loading any archived queue.
        self.items: dict[str, QueuedTask] = {}
        self.queue_lock = asyncio.Lock()
        self.wake = asyncio.Event()
        self.launching: set[str] = set()
        self._outcomes: list[tuple[str, str]] = []
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="queue-dispatcher")

    def resolved(self, agent_id: str, outcome: Literal["merged", "rejected"]) -> None:
        """Called after release, even under git locks: no awaits and no queue lock."""
        self._outcomes.append((agent_id, outcome))
        self.wake.set()

    async def _save(self, item: QueuedTask) -> None:
        # Finish both the durable write and its cache update before propagating cancellation.
        async def commit() -> None:
            await self.live.store.publish(item)
            self.items[item.queued_task_id] = item
            self.wake.set()

        pending = asyncio.create_task(commit())
        try:
            await asyncio.shield(pending)
        except asyncio.CancelledError:
            await self.live._finish(pending)
            raise

    def _available(self) -> None:
        if self._closed or self.live._closing or self.live._manager is None:
            raise ApiError("wrong_state", "the run is not accepting queue commands")

    @staticmethod
    def _request(item: QueuedTask) -> StartAgent:
        assert item.agent is not None
        return StartAgent(task=item.agent.task, slot=item.agent.slot, model=item.agent.model)

    async def enqueue(self, request: EnqueueTask) -> QueuedTask:
        async with self.queue_lock:
            self._available()
            if request.agent is None:
                raise ApiError("not_found", "plan queueing is not available")
            if request.agent.zone is not None:
                raise ApiError("invalid_request", "standalone queue zones are not supported")
            if sum(item.state not in TERMINAL for item in self.items.values()) >= QUEUE_LIMIT:
                raise ApiError("wrong_state", f"the queue holds at most {QUEUE_LIMIT} active items")
            item = queued(request, len(self.items) + 1)
            manager = self.live._manager
            assert manager is not None
            try:
                self.live._check(self._request(item), manager)
            except ApiError as error:
                if error.code not in {"slot_busy", "spend_brake", "not_configured", "wrong_state"}:
                    raise
            await self._save(item)
            self.wake.set()
            return item.model_copy(deep=True)

    async def cancel(self, identifier: str, request: QueueRevision) -> None:
        async with self.queue_lock:
            self._available()
            item = self.items.get(identifier)
            if item is None:
                raise ApiError("not_found", f"there is no queue item {identifier!r}")
            if identifier in self.launching:
                raise ApiError("wrong_state", "the item is being dispatched")
            if item.state not in {"queued", "blocked"} or item.revision != request.revision:
                raise ApiError(
                    "wrong_state", "cancel requires a queued or blocked item at that revision"
                )
            await self._save(queue_transition(item, "cancelled"))
            self.wake.set()

    async def _refused(self, item: QueuedTask, error: ApiError) -> None:
        if error.code == "slot_busy":
            return
        state = (
            "blocked"
            if error.code in {"spend_brake", "not_configured", "wrong_state"}
            else "failed"
        )
        if item.state != state or item.detail != error.message:
            # Repeated refusals do not create revisions or events. A changed blocked reason
            # goes through queued so every published edge follows the state machine.
            if item.state == "blocked" and state == "blocked":
                item = queue_transition(item, "queued")
                await self._save(item)
            await self._save(queue_transition(item, state, detail=error.message))

    async def _select(self) -> tuple[QueuedTask, Admission] | None:
        for agent_id, outcome in self._outcomes:
            for item in list(self.items.values()):
                if item.state == "running" and agent_id in item.agent_ids:
                    await self._save(queue_transition(item, "completed", detail=outcome))
        self._outcomes.clear()
        for item in queue_candidates(self.items.values()):
            if self._closed:
                return None
            if item.state == "blocked":
                manager = self.live._manager
                assert manager is not None
                try:
                    self.live._check(self._request(item), manager)
                except ApiError as error:
                    if error.code != "slot_busy":
                        await self._refused(item, error)
                        continue
                item = queue_transition(item, "queued")
                await self._save(item)
            try:
                # Check, reserve and allocate without an await, while queue_lock is held.
                admission = self.live._admit(
                    self._request(item), queued_task_id=item.queued_task_id
                )
            except ApiError as error:
                await self._refused(item, error)
                if error.code == "slot_busy":
                    return None  # strict head-of-line, including a named busy slot
                continue  # blocked or failed items do not stop the queue
            self.launching.add(item.queued_task_id)
            return item, admission
        return None

    async def _dispatch(self, item: QueuedTask, admission: Admission) -> None:
        created: AgentCreated | None = None
        refusal: ApiError | None = None
        try:
            try:
                created = await self.live._launch(admission)
            except ApiError as error:
                refusal = error
            async with self.queue_lock:
                if refusal is not None:
                    await self._refused(item, refusal)
                else:
                    assert created is not None
                    await self._save(queue_transition(item, "running", agent_id=created.agent_id))
                self.launching.discard(item.queued_task_id)
        finally:
            if item.queued_task_id in self.launching:
                async with self.queue_lock:
                    self.launching.discard(item.queued_task_id)

    async def _run(self) -> None:
        try:
            while not self._closed:
                await self.wake.wait()
                self.wake.clear()
                while not self._closed:
                    async with self.queue_lock:
                        selected = await self._select()
                    if selected is None:
                        break
                    await self._dispatch(*selected)  # never hold queue_lock over launch/git
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self.live._report("server", f"Queue dispatcher failed: {type(error).__name__}")
            self._closed = True

    async def close(self) -> None:
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        pending = [
            item.queued_task_id for item in self.items.values() if item.state not in TERMINAL
        ]
        if pending:
            await self.live._report("server", "Queue ended without resuming: " + ", ".join(pending))
