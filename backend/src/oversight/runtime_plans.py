"""Run-local plan service; the dispatcher's lock serializes all plan and queue commands."""

# Like the other runtime helpers, this composes LiveRun's owned registries.
# pyright: reportPrivateUsage=false

from __future__ import annotations

from typing import TYPE_CHECKING

from oversight.api import ApiError
from oversight.changes import WrongState, plan_core
from oversight_contracts.commands import CreatePlan, PlanCreated, RejectPlan, UpdatePlan
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
            return await self.live._finish(self._create(request))

    async def _create(self, request: CreatePlan) -> PlanCreated:
        _, snapshot = self.live.store.graph()
        assert snapshot is not None
        self._count += 1
        plan = plan_core.planning(request.task, snapshot, self._count)
        await self.save(plan)
        draft = SubtaskDraft(task=request.task, zone=plan_core.whole_graph(snapshot))
        await self.save(plan_core.propose(plan, [draft]))
        return PlanCreated(plan_id=plan.plan_id)

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
