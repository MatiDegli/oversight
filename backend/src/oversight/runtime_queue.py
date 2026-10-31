"""Run-local durable queue, slot admission and dispatch. Restarts never resume old items."""

# Like the other runtime helpers, this composes LiveRun's owned registries.
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from oversight.agents import AgentTask
from oversight.api import ApiError
from oversight.changes import (
    QUEUE_LIMIT,
    TEMPORARY_REFUSALS,
    TERMINAL,
    plan_core,
    queue_candidates,
    queue_transition,
    queued,
    refusal_state,
)
from oversight_contracts.commands import AgentCreated, EnqueueTask, QueueRevision, StartAgent
from oversight_contracts.events import ModelProfile
from oversight_contracts.plans import Plan, PlanZone
from oversight_contracts.queue import QueuedTask

if TYPE_CHECKING:
    from oversight.app import LiveRun


@dataclass(frozen=True)
class Admission:
    task: AgentTask
    profile: ModelProfile
    queued_task_id: str | None
    plan_id: str | None = None
    zone: PlanZone | None = None


class Dispatcher:
    def __init__(self, live: LiveRun) -> None:
        self.live = live
        # Restart policy: each LiveRun starts empty, without loading any archived queue.
        self.items: dict[str, QueuedTask] = {}
        self.queue_lock = asyncio.Lock()
        self.wake = asyncio.Event()
        self.launching: set[str] = set()
        self._outcomes: list[tuple[str, Literal["merged", "rejected"]]] = []
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        self._count = 0

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="queue-dispatcher")

    def resolved(self, agent_id: str, outcome: Literal["merged", "rejected"]) -> None:
        """Called after release, even under git locks: no awaits and no queue lock."""
        self._outcomes.append((agent_id, outcome))
        self.wake.set()

    async def _save(self, item: QueuedTask) -> None:
        async def commit() -> None:
            await self.live.store.publish(item)
            self.items[item.queued_task_id] = item

        pending = asyncio.create_task(commit())
        try:
            await asyncio.shield(pending)
        except asyncio.CancelledError:
            await self.live._finish(pending)
            raise

    def _available(self, *, dispatch: bool = True) -> None:
        if (dispatch and self._closed) or self.live._closing or self.live._manager is None:
            raise ApiError("wrong_state", "the run is not accepting queue commands")

    def _requests(self, item: QueuedTask) -> list[tuple[int | None, StartAgent]]:
        if item.agent is not None:
            return [
                (
                    None,
                    StartAgent(task=item.agent.task, slot=item.agent.slot, model=item.agent.model),
                )
            ]
        plan = self.live.plans.dispatch_plan(item)
        assert plan.subtasks is not None
        return [
            (index, StartAgent(task=plan.subtasks[index].task, zone=plan.subtasks[index].zone))
            for index in plan_core.ready(plan)
        ]

    async def enqueue(self, request: EnqueueTask) -> QueuedTask:
        async with self.queue_lock:
            self._available()
            if sum(item.state not in TERMINAL for item in self.items.values()) >= QUEUE_LIMIT:
                raise ApiError("wrong_state", f"the queue holds at most {QUEUE_LIMIT} active items")
            plan: Plan | None = None
            if request.plan_id is not None:
                assert request.plan_revision is not None
                plan = self.live.plans.for_queue(request.plan_id, request.plan_revision)
            elif request.agent is not None and request.agent.zone is not None:
                raise ApiError("invalid_request", "standalone queue zones are not supported")
            item = queued(request, self._count + 1)
            if plan is None:
                manager = self.live._manager
                assert manager is not None
                try:
                    self.live._check(self._requests(item)[0][1], manager)
                except ApiError as error:
                    if error.code not in TEMPORARY_REFUSALS:
                        raise
            self._count += 1
            await self.live._finish(self._enqueue(item, plan))
            self.wake.set()
            return item.model_copy(deep=True)

    async def _enqueue(self, item: QueuedTask, plan: Plan | None) -> None:
        if plan is not None:
            await self.live.plans.save(plan_core.link(plan, plan.revision, item.queued_task_id))
        await self._save(item)

    async def cancel_item(self, item: QueuedTask) -> None:
        if item.queued_task_id in self.launching or item.state not in {"queued", "blocked"}:
            raise ApiError("wrong_state", "cancel requires an undispatched item")
        await self._save(queue_transition(item, "cancelled"))
        self.wake.set()

    async def cancel(self, identifier: str, request: QueueRevision) -> None:
        async with self.queue_lock:
            self._available(dispatch=False)
            item = self.items.get(identifier)
            if item is None:
                raise ApiError("not_found", f"there is no queue item {identifier!r}")
            if item.revision != request.revision:
                raise ApiError("wrong_state", "the queue revision differs")
            await self.live._finish(self._cancel(item))

    async def _cancel(self, item: QueuedTask) -> None:
        await self.cancel_item(item)
        if item.plan_id is not None:
            await self.live.plans.save(plan_core.unlink(self.live.plans.get(item.plan_id)))

    async def _refused(
        self, item: QueuedTask, error: ApiError, *, launch_failed: bool = False
    ) -> None:
        if item.plan_id is None and error.code == "slot_busy":
            return
        state = refusal_state(item, error.code, launch_failed=launch_failed)
        if item.state == state and item.detail == error.message:
            return
        # Preserve F1's blocked -> queued edge when the explanation changes.
        if item.state == "blocked" and state == "blocked":
            item = queue_transition(item, "queued")
            await self._save(item)
        await self.live._finish(self._record_refusal(item, state, error.message))

    async def _record_refusal(self, item: QueuedTask, state: str, detail: str) -> None:
        if item.plan_id is not None and state == "failed":
            plan = self.live.plans.get(item.plan_id)
            await self.live.plans.save(plan_core.fail(plan, detail))
        await self._save(queue_transition(item, state, detail=detail))

    async def _settle(self, agent_id: str, outcome: Literal["merged", "rejected"]) -> None:
        for item in list(self.items.values()):
            if item.state not in {"starting", "running"} or agent_id not in item.agent_ids:
                continue
            if item.plan_id is not None:
                plan = plan_core.resolved(self.live.plans.get(item.plan_id), agent_id, outcome)
                await self.live.plans.save(plan)
                if plan.state != "completed":
                    continue
            await self._save(queue_transition(item, "completed", detail=outcome))

    def _reserve(self, item: QueuedTask) -> list[tuple[int | None, Admission]]:
        """Check and reserve all k ready slots without yielding; roll back all on refusal."""
        requests = self._requests(item)
        if len(requests) > len(self.live._slots):
            raise ApiError("invalid_request", "the ready subtasks exceed the offered slot count")
        admissions: list[tuple[int | None, Admission]] = []
        try:
            for index, request in requests:
                admission = self.live._admit(
                    request, queued_task_id=item.queued_task_id, plan_id=item.plan_id
                )
                admissions.append((index, admission))
        except BaseException:
            self._release_reservations(admissions)
            raise
        return admissions

    def _release_reservations(self, admissions: list[tuple[int | None, Admission]]) -> None:
        for _, admission in admissions:
            if self.live._slots[admission.task.slot] == "starting":
                self.live._slots[admission.task.slot] = None

    async def _select(self) -> tuple[QueuedTask, list[tuple[int | None, Admission]]] | None:
        while self._outcomes:
            agent_id, outcome = self._outcomes[0]
            await self.live._finish(self._settle(agent_id, outcome))
            self._outcomes.pop(0)
        for item in queue_candidates(self.items.values(), self.live.plans.plans):
            if self._closed:
                return None
            if item.state == "blocked":
                manager = self.live._manager
                assert manager is not None
                try:
                    for _, request in self._requests(item):
                        self.live._check(request, manager)
                except ApiError as error:
                    if error.code != "slot_busy":
                        await self._refused(item, error)
                        continue
                item = queue_transition(item, "queued")
                await self._save(item)
            try:
                admissions = self._reserve(item)
            except ApiError as error:
                await self._refused(item, error)
                if error.code == "slot_busy":
                    return None
                continue
            if admissions:
                self.launching.add(item.queued_task_id)
                return item, admissions
        return None

    async def _record_launch(
        self,
        identifier: str,
        index: int | None,
        created: AgentCreated,
        *,
        more_initial: bool,
        last: bool,
    ) -> None:
        async with self.queue_lock:
            item = self.items[identifier]
            if item.plan_id is not None:
                assert index is not None
                plan = plan_core.launched(
                    self.live.plans.get(item.plan_id),
                    index,
                    created.agent_id,
                    more_initial=more_initial,
                )
                await self.live.plans.save(plan)
            state = "starting" if more_initial else "running"
            await self._save(queue_transition(item, state, agent_id=created.agent_id))
            if last:
                self.launching.discard(identifier)

    async def _dispatch(
        self, item: QueuedTask, admissions: list[tuple[int | None, Admission]]
    ) -> None:
        remaining = list(admissions)
        try:
            for index, admission in admissions:
                remaining.pop(0)
                try:
                    created = await self.live._launch(admission)
                except ApiError as error:
                    async with self.queue_lock:
                        await self._refused(
                            self.items[item.queued_task_id],
                            error,
                            launch_failed=error.code not in TEMPORARY_REFUSALS,
                        )
                        self.launching.discard(item.queued_task_id)
                    return
                # Once launch returned, finish publication even if close cancels us while
                # another command holds queue_lock. No running agent is left labelled queued.
                await self.live._finish(
                    self._record_launch(
                        item.queued_task_id,
                        index,
                        created,
                        more_initial=bool(remaining) and item.state != "running",
                        last=not remaining,
                    )
                )
                if self._closed:
                    return
        finally:
            self._release_reservations(remaining)
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
