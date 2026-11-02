"""Runtime plan bounds stay explicit after the contract removes its ceilings."""

# These tests seed contract-valid future plans to exercise each live admission boundary.
# pyright: reportPrivateUsage=false

import asyncio
from pathlib import Path

import pytest
from live_helpers import DEADLINE_S, error_code, live_settings
from plan_helpers import plan_server

from oversight.api import ApiError
from oversight_contracts.commands import UpdatePlan
from oversight_contracts.plans import Plan, PlanSubtask, PlanZone, SubtaskDraft
from oversight_contracts.queue import QueuedTask

pytestmark = pytest.mark.anyio


def drafts(dependency: bool) -> list[SubtaskDraft]:
    return [
        SubtaskDraft(
            task=f"Task {index}",
            zone=PlanZone(node_ids=[], paths=[]),
            depends_on=[6] if dependency and index == 0 else [],
        )
        for index in range(7)
    ]


def proposed(subtasks: list[SubtaskDraft]) -> Plan:
    return Plan(
        plan_id="plan-1",
        revision=2,
        task="Coordinate",
        graph_version="graph",
        state="proposed",
        started_by="agent-99",
        subtasks=[PlanSubtask(**task.model_dump(), state="waiting") for task in subtasks],
    )


def message(dependency: bool) -> str:
    return (
        "A subtask dependency index must be at most 5."
        if dependency
        else "A plan has 1 to 6 subtasks."
    )


@pytest.mark.parametrize("dependency", [False, True])
async def test_lead_plan_creation_refuses_runtime_limits_before_saving(
    tmp_path: Path,
    dependency: bool,
) -> None:
    async with asyncio.timeout(DEADLINE_S), plan_server(live_settings(tmp_path)) as s:
        before = s.store.last_seq
        with pytest.raises(ApiError) as caught:
            await s.live.plans.create_and_propose(
                "Coordinate", drafts(dependency), started_by="agent-99"
            )
        assert caught.value.code == "invalid_request"
        assert caught.value.message == message(dependency)
        assert s.store.last_seq == before and not s.live.plans.plans


@pytest.mark.parametrize("dependency", [False, True])
@pytest.mark.parametrize("boundary", ["service", "app", "http"])
async def test_person_edit_of_lead_plan_refuses_runtime_limits(
    tmp_path: Path,
    dependency: bool,
    boundary: str,
) -> None:
    async with asyncio.timeout(DEADLINE_S), plan_server(live_settings(tmp_path)) as s:
        plan = proposed(drafts(False)[:1])
        await s.live.plans.save(plan)
        before = s.store.last_seq
        request = UpdatePlan(revision=2, graph_version="graph", subtasks=drafts(dependency))
        if boundary == "http":
            response = await s.client.put(f"/plans/{plan.plan_id}", json=request.model_dump())
            assert error_code(response) == (422, "invalid_request")
            assert response.json()["error"]["message"] == message(dependency)
        else:
            update = s.live.update_plan if boundary == "app" else s.live.plans.update
            with pytest.raises(ApiError) as caught:
                await update(plan.plan_id, request)
            assert caught.value.code == "invalid_request"
            assert caught.value.message == message(dependency)
        assert s.store.last_seq == before and s.live.plans.get(plan.plan_id) == plan


@pytest.mark.parametrize("dependency", [False, True])
@pytest.mark.parametrize("route", ["queue", "start"])
async def test_queue_admission_refuses_contract_valid_larger_plans(
    tmp_path: Path,
    dependency: bool,
    route: str,
) -> None:
    async with asyncio.timeout(DEADLINE_S), plan_server(live_settings(tmp_path)) as s:
        plan = proposed(drafts(dependency))
        await s.live.plans.save(plan)
        before = s.store.last_seq
        path, body = (
            ("/queue", {"plan_id": plan.plan_id, "plan_revision": 2})
            if route == "queue"
            else (f"/plans/{plan.plan_id}/start", {"revision": 2})
        )
        response = await s.client.post(path, json=body)
        assert error_code(response) == (422, "invalid_request")
        assert response.json()["error"]["message"] == message(dependency)
        assert s.store.last_seq == before and not s.live.dispatcher.items
        assert not s.live._agents


@pytest.mark.parametrize("dependency", [False, True])
async def test_dispatch_rechecks_limits_even_when_queued_content_matches(
    tmp_path: Path,
    dependency: bool,
) -> None:
    async with asyncio.timeout(DEADLINE_S), plan_server(live_settings(tmp_path)) as s:
        plan = proposed(drafts(dependency))
        s.live.plans.revisions[plan.plan_id, 2] = plan
        s.live.plans.plans[plan.plan_id] = plan.model_copy(
            update={"queued_task_id": "queued-task-1"}
        )
        item = QueuedTask(
            queued_task_id="queued-task-1",
            revision=1,
            state="queued",
            agent_ids=[],
            detail=None,
            plan_id=plan.plan_id,
            plan_revision=2,
        )
        with pytest.raises(ApiError) as caught:
            s.live.dispatcher._requests(item)
        assert caught.value.code == "invalid_request"
        assert caught.value.message == message(dependency)
        assert not s.live._agents and all(holder is None for holder in s.live._slots.values())
