"""Run-local plans; admission and person edits share the dispatcher's lock."""

# Like the other runtime helpers, this composes LiveRun's owned registries.
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from oversight.agents import MUSE_SPARK, run_plan_call
from oversight.api import ApiError
from oversight.changes import WrongState, plan_core
from oversight.runtime_accounting import PlanAccounting, durable
from oversight_contracts.commands import CreatePlan, PlanCreated, RejectPlan, UpdatePlan
from oversight_contracts.events import ModelProfile
from oversight_contracts.graph import GraphSnapshot
from oversight_contracts.plans import Plan, SubtaskDraft
from oversight_contracts.queue import QueuedTask

if TYPE_CHECKING:
    from oversight.app import LiveRun


class PlanService:
    def __init__(self, live: LiveRun) -> None:
        self.live = live
        self.plans: dict[str, Plan] = {}
        self.revisions: dict[tuple[str, int], Plan] = {}
        self._count = 0
        self._calls: dict[str, asyncio.Task[PlanCreated]] = {}

    def get(self, plan_id: str) -> Plan:
        if plan_id not in self.plans:
            raise ApiError("not_found", f"there is no plan {plan_id!r}")
        return self.plans[plan_id]

    def _available(self) -> None:
        if self.live._closing or self.live._manager is None:
            raise ApiError("wrong_state", "the run is not accepting plan commands")

    async def save(self, plan: Plan) -> None:
        await self.live.store.publish(plan)
        self.plans[plan.plan_id] = plan.model_copy(deep=True)
        self.revisions[plan.plan_id, plan.revision] = plan.model_copy(deep=True)

    @staticmethod
    def check_count(plan: Plan, count: int) -> None:
        if plan.started_by is None and count != 1:
            raise ApiError("invalid_request", "person-created plans require exactly one subtask")

    async def create(self, request: CreatePlan) -> PlanCreated:
        async with self.live.dispatcher.queue_lock:
            self._available()
            if not request.task.strip():
                raise ApiError("invalid_request", "the task is empty")
            profile = self._profile()
            self.live._check_spending("A", profile)
            _, snapshot = self.live.store.graph()
            assert snapshot is not None
            self._count += 1
            plan = plan_core.planning(request.task, snapshot, self._count)
            # Q1: reserve a run-local planner identity without creating a card or taking a slot.
            planner_id = self._planner_id()
            call = asyncio.create_task(self._create(plan, snapshot, profile, planner_id))
            self._calls[plan.plan_id] = call
        # A model wait must not hold queue_lock: queue commands and shutdown remain available.
        try:
            return await self.live._finish(call)
        finally:
            self._calls.pop(plan.plan_id, None)

    def _profile(self) -> ModelProfile:
        profile = self.live._slot_profiles["A"]
        return profile if profile.provider == "fake" else MUSE_SPARK

    def _planner_id(self) -> str:
        self.live._count += 1
        return f"agent-{self.live._count}"

    async def _create(
        self, plan: Plan, snapshot: GraphSnapshot, profile: ModelProfile, planner_id: str
    ) -> PlanCreated:
        await durable(self.save(plan))
        accounting = PlanAccounting(self.live, plan.plan_id, profile, planner_id)
        ready_time = asyncio.get_running_loop().time()
        # Client construction uses the same injectable factory as author starts.
        outcome = await run_plan_call(
            lambda: self.live._client_factory(profile, "A"),
            plan.task,
            snapshot,
            ready_time=ready_time,
            record=accounting.record,
        )
        if accounting.failure is not None:
            await self.live._finish(self.live._report("server", accounting.failure))
        if self.live._closing:
            return PlanCreated(plan_id=plan.plan_id)
        draft = SubtaskDraft(task=outcome.task, zone=plan_core.whole_graph(snapshot))
        proposed = plan_core.propose(plan, [draft]).model_copy(update={"detail": outcome.detail})
        await self.live._finish(self.save(proposed))
        return PlanCreated(plan_id=plan.plan_id)

    async def create_and_propose(
        self, task: str, drafts: list[SubtaskDraft], *, started_by: str
    ) -> PlanCreated:
        """Lead-supplied drafts never trigger a model call or enter the queue."""
        async with self.live.dispatcher.queue_lock:
            self._available()
            _, snapshot = self.live.store.graph()
            assert snapshot is not None
            self._count += 1
            plan = plan_core.planning(task, snapshot, self._count).model_copy(
                update={"started_by": started_by}
            )
            proposed = plan_core.propose(plan, drafts)
            return await self.live._finish(self._save_drafts(plan, proposed))

    async def _save_drafts(self, plan: Plan, proposed: Plan) -> PlanCreated:
        await self.save(plan)
        await self.save(proposed)
        return PlanCreated(plan_id=plan.plan_id)

    async def close(self) -> None:
        calls = list(self._calls.values())
        for call in calls:
            call.cancel()
        await asyncio.gather(*calls, return_exceptions=True)
        pending = [plan.plan_id for plan in self.plans.values() if plan.state == "planning"]
        if pending:
            await self.live._report("server", "Plans ended without resuming: " + ", ".join(pending))

    async def update(self, plan_id: str, request: UpdatePlan) -> None:
        async with self.live.dispatcher.queue_lock:
            self._available()
            plan = self.get(plan_id)
            try:
                plan_core.editable(plan, request.revision)
                self.check_count(plan, len(request.subtasks))
                updated = plan_core.update(plan, request)
                if plan.started_by is None:
                    assert plan.subtasks is not None
                    if any(draft.zone != plan.subtasks[0].zone for draft in request.subtasks):
                        raise ApiError(
                            "invalid_request", "person plans require the whole-graph zone"
                        )
            except WrongState as error:
                raise ApiError("wrong_state", str(error)) from None
            except ValueError as error:
                raise ApiError("invalid_request", str(error)) from None
            await self.live._finish(self.save(updated))

    def for_queue(self, plan_id: str, revision: int) -> Plan:
        plan = self.get(plan_id)
        try:
            plan_core.editable(plan, revision)
        except WrongState as error:
            raise ApiError("wrong_state", str(error)) from None
        assert plan.subtasks is not None
        self.check_count(plan, len(plan.subtasks))
        return plan

    def dispatch_plan(self, item: QueuedTask) -> Plan:
        assert item.plan_id is not None
        assert item.plan_revision is not None
        plan = self.get(item.plan_id)
        original = self.revisions.get((item.plan_id, item.plan_revision))
        if (
            original is None
            or plan.queued_task_id != item.queued_task_id
            or not plan_core.content_matches(plan, original)
        ):
            raise ApiError("invalid_request", "plan content differs from the queued revision")
        return plan

    async def reject(self, plan_id: str, request: RejectPlan) -> None:
        dispatcher = self.live.dispatcher
        async with dispatcher.queue_lock:
            self._available()
            plan = self.get(plan_id)
            if plan.queued_task_id in dispatcher.launching:
                raise ApiError("wrong_state", "the plan is being dispatched")
            try:
                rejected = plan_core.reject(plan, request.revision, request.comment)
            except WrongState as error:
                raise ApiError("wrong_state", str(error)) from None
            await self.live._finish(self._reject(plan, rejected))

    async def _reject(self, plan: Plan, rejected: Plan) -> None:
        if plan.queued_task_id is not None:
            await self.live.dispatcher.cancel_item(self.live.dispatcher.items[plan.queued_task_id])
        await self.save(rejected)
