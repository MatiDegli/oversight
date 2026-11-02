"""Configured lead plans and dependency batches through real service/queue boundaries."""

# Inspect owned slots and frozen revisions; coding calls use only gated/scripted fakes.
# pyright: reportPrivateUsage=false

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from decision_helpers import observed_body
from live_helpers import DEADLINE_S, GatedModel, LiveServer, error_code, is_type, two_fakes
from plan_helpers import plan_server
from revise_helpers import ready, scripted_a

from oversight.changes import plan_core, queued
from oversight_contracts.commands import EnqueueTask, UpdatePlan
from oversight_contracts.plans import Plan, SubtaskDraft

pytestmark = pytest.mark.anyio


async def lead_plan(s: LiveServer, dependencies: list[list[int]], lead: str = "agent-99") -> Plan:
    _, graph = s.store.graph()
    assert graph is not None
    drafts = [
        SubtaskDraft(task=f"Task {i}", zone=plan_core.whole_graph(graph), depends_on=deps)
        for i, deps in enumerate(dependencies)
    ]
    created = await s.live.plans.create_and_propose("Coordinate", drafts, started_by=lead)
    return s.live.plans.get(created.plan_id)


async def enqueue(s: LiveServer, plan: Plan) -> str:
    item = await s.live.enqueue(EnqueueTask(plan_id=plan.plan_id, plan_revision=plan.revision))
    return item.queued_task_id


async def launched(s: LiveServer, plan: Plan, count: int) -> Plan:
    await s.until(
        lambda e: (
            e.type == "plan.updated"
            and e.payload.plan_id == plan.plan_id
            and sum(t.agent_id is not None for t in e.payload.subtasks or []) == count
        )
    )
    current = s.live.plans.get(plan.plan_id)
    assert current.state == "running"
    return current


async def stop(s: LiveServer, agent: str | None) -> None:
    assert agent is not None
    plan_id = s.live._agents[agent].plan_id
    assert (await s.client.post(f"/agents/{agent}/stop")).status_code == 202
    if plan_id is not None:
        await s.until(
            lambda e: (
                e.type == "plan.updated"
                and e.payload.plan_id == plan_id
                and any(
                    t.agent_id == agent and t.state == "rejected" for t in e.payload.subtasks or []
                )
            )
        )


@pytest.mark.parametrize("route", ["queue", "start", "edit", "dispatch"])
async def test_impossible_first_wave_is_editable_until_dispatch_backstop(
    tmp_path: Path, route: str
) -> None:
    async with asyncio.timeout(DEADLINE_S), plan_server(two_fakes(tmp_path, max_agents=2)) as s:
        plan = await lead_plan(s, [[], [], []] if route != "edit" else [[]])
        message = "The first wave exceeds OVERSIGHT_MAX_AGENTS=2."
        if route == "dispatch":
            # Bypass admission to prove the dispatcher backstop and failed outcome.
            async with s.live.dispatcher.queue_lock:
                item = queued(EnqueueTask(plan_id=plan.plan_id, plan_revision=2), 1)
                await s.live.dispatcher._enqueue(item, plan)
            s.live.dispatcher.wake.set()
            await s.until(is_type("queue.updated", state="failed", detail=message))
            assert s.live.plans.get(plan.plan_id).state == "failed"
        else:
            if route == "edit":
                assert plan.subtasks
                body = UpdatePlan(
                    revision=plan.revision,
                    graph_version=plan.graph_version,
                    subtasks=[SubtaskDraft(task=t.task, zone=t.zone) for t in plan.subtasks] * 3,
                ).model_dump()
                response = await s.client.put(f"/plans/{plan.plan_id}", json=body)
            elif route == "start":
                response = await s.client.post(f"/plans/{plan.plan_id}/start", json={"revision": 2})
            else:
                response = await s.client.post(
                    "/queue", json={"plan_id": plan.plan_id, "plan_revision": 2}
                )
            assert error_code(response) == (422, "invalid_request")
            assert response.json()["error"]["message"] == message
            assert s.live.plans.get(plan.plan_id) == plan
            assert not s.live.dispatcher.items
        assert not s.live._agents and not s.live.dispatcher.launching
        assert all(holder is None for holder in s.live._slots.values())


async def test_raised_limit_and_index_survive_edits_queue_and_settings_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = two_fakes(tmp_path, max_agents=2, lead_max_subtasks=8)
    async with asyncio.timeout(DEADLINE_S), plan_server(settings, lambda _p, _s: GatedModel()) as s:
        monkeypatch.setenv("OVERSIGHT_LEAD_MAX_SUBTASKS", "2")
        s.live._settings = replace(settings, lead_max_subtasks=2, max_agents=1)
        plan = await lead_plan(s, [[]] + [[i - 1] for i in range(1, 8)])
        assert plan.subtasks and plan.subtasks[7].depends_on == [6]
        request = UpdatePlan(
            revision=plan.revision,
            graph_version=plan.graph_version,
            subtasks=[
                SubtaskDraft(task=t.task + " edited", zone=t.zone, depends_on=t.depends_on)
                for t in plan.subtasks
            ],
        )
        response = await s.client.put(f"/plans/{plan.plan_id}", json=request.model_dump())
        assert response.status_code == 202, response.text
        edited = s.live.plans.get(plan.plan_id)
        assert edited.started_by == "agent-99" and edited.revision == 3
        identifier = await enqueue(s, edited)
        current = await launched(s, plan, 1)
        assert current.subtasks and current.subtasks[0].task.endswith(" edited")
        assert s.live.dispatcher.items[identifier].plan_revision == 3
        assert s.live._lead_limits.subtasks == 8 and len(s.live._slots) == 2


@pytest.mark.parametrize(
    "dependencies,reason",
    [
        ([[1], [0]], "acyclic"),
        ([[1, 1], []], "unique"),
        ([[0], []], "exclude self"),
        ([[-1], []], "greater than or equal to 0"),
        ([[2], []], "below the subtask count"),
    ],
)
async def test_raised_limit_keeps_actual_plan_dependency_validation(
    tmp_path: Path, dependencies: list[list[int]], reason: str
) -> None:
    async with (
        asyncio.timeout(DEADLINE_S),
        plan_server(two_fakes(tmp_path, lead_max_subtasks=8)) as s,
    ):
        plan = await lead_plan(s, [[], [0]])
        assert plan.subtasks
        body = {
            "revision": 2,
            "graph_version": plan.graph_version,
            "subtasks": [
                {"task": t.task, "zone": t.zone.model_dump(), "depends_on": d}
                for t, d in zip(plan.subtasks, dependencies, strict=True)
            ],
        }
        response = await s.client.put(f"/plans/{plan.plan_id}", json=body)
        assert error_code(response) == (422, "invalid_request")
        assert reason in response.json()["error"]["message"]
        assert s.live.plans.get(plan.plan_id) == plan and not s.live.dispatcher.items


async def test_fitting_initial_wave_waits_for_occupied_seats_then_starts(tmp_path: Path) -> None:
    async with (
        asyncio.timeout(DEADLINE_S),
        plan_server(two_fakes(tmp_path, max_agents=2), lambda _p, _s: GatedModel()) as s,
    ):
        holders = [
            (await s.client.post("/agents", json={"task": "Hold"})).json()["agent_id"]
            for _ in range(2)
        ]
        plan = await lead_plan(s, [[], []])
        identifier = await enqueue(s, plan)
        await s.until(
            lambda e: (
                is_type("queue.updated", queued_task_id=identifier)(e) and bool(e.payload.detail)
            )
        )
        item = s.live.dispatcher.items[identifier]
        assert item.state == "queued" and not item.agent_ids
        assert item.detail and "taken" in item.detail
        await stop(s, holders[0])
        # One free seat is reserved then rolled back: initial admission is atomic.
        await s.until(
            lambda e: (
                is_type("queue.updated", queued_task_id=identifier)(e)
                and e.payload.detail is not None
                and "starting" in e.payload.detail
            )
        )
        assert s.live._slots == {"A": None, "B": holders[1]}
        await stop(s, holders[1])
        current = await launched(s, plan, 2)
        assert current.subtasks and all(t.state == "running" for t in current.subtasks)


async def test_seven_children_wait_for_merge_and_launch_once_in_stable_batches(
    tmp_path: Path,
) -> None:
    calls = 0

    def factory(*_args: object):
        nonlocal calls
        calls += 1
        return scripted_a() if calls == 1 else GatedModel()

    async with (
        asyncio.timeout(DEADLINE_S),
        plan_server(two_fakes(tmp_path, max_agents=2, lead_max_subtasks=8), factory) as s,
    ):
        plan = await lead_plan(s, [[]] + [[0]] * 7)
        identifier = await enqueue(s, plan)
        current = await launched(s, plan, 1)
        assert current.subtasks and calls == 1
        root = current.subtasks[0].agent_id
        assert root
        await ready(s, root)
        assert (
            sum(t.agent_id is not None for t in s.live.plans.get(plan.plan_id).subtasks or []) == 1
        )
        change = root.replace("agent-", "change-")
        assert (
            await s.client.post(f"/changes/{change}/approve", json=observed_body(s, change))
        ).status_code == 202
        current = await launched(s, plan, 3)
        assert current.subtasks and current.subtasks[0].state == "merged"
        # Both seats stay held until a decision; the remaining children retain their indices.
        await s.until(
            lambda e: (
                is_type("queue.updated", queued_task_id=identifier, state="running")(e)
                and bool(e.payload.detail)
            )
        )
        assert [t.state for t in current.subtasks] == ["merged", "running", "running"] + [
            "waiting"
        ] * 5
        for count in (3, 5, 7, 8):
            current = await launched(s, plan, count)
            assert current.subtasks
            for task in current.subtasks:
                if task.state == "running":
                    await stop(s, task.agent_id)
        await s.until(is_type("queue.updated", queued_task_id=identifier, state="completed"))
        final = s.live.plans.get(plan.plan_id)
        assert final.state == "completed" and calls == 8
        assert final.subtasks and all(t.agent_id for t in final.subtasks)
        assert len({t.agent_id for t in final.subtasks}) == 8
        assert s.live.dispatcher.items[identifier].agent_ids == [t.agent_id for t in final.subtasks]
        assert all(holder is None for holder in s.live._slots.values())
        statuses = [e.payload for e in s.events() if e.type == "agent.status"]
        assert statuses and all(
            t.plan_id == plan.plan_id and t.queued_task_id == identifier and t.started_by is None
            for t in statuses
        )


async def test_two_lead_plans_share_seats_and_rejection_cancels_only_dependents(
    tmp_path: Path,
) -> None:
    calls = 0

    def factory(*_args: object):
        nonlocal calls
        calls += 1
        return scripted_a() if calls == 2 else GatedModel()

    async with (
        asyncio.timeout(DEADLINE_S),
        plan_server(two_fakes(tmp_path, max_agents=2), factory) as s,
    ):
        first = await lead_plan(s, [[], [0]], "agent-90")
        second = await lead_plan(s, [[], [0], [1], [0]], "agent-91")
        one = await enqueue(s, first)
        first = await launched(s, first, 1)
        two = await enqueue(s, second)
        second = await launched(s, second, 1)
        assert first.subtasks and second.subtasks
        root = second.subtasks[0].agent_id
        assert root
        await ready(s, root)
        change = root.replace("agent-", "change-")
        assert (
            await s.client.post(f"/changes/{change}/approve", json=observed_body(s, change))
        ).status_code == 202
        await s.until(
            lambda e: is_type("queue.updated", queued_task_id=two)(e) and bool(e.payload.detail)
        )
        # The second plan's two-child batch rolls back its free seat while the first holds one.
        assert s.live._slots == {"A": first.subtasks[0].agent_id, "B": None}
        await stop(s, first.subtasks[0].agent_id)
        await s.until(is_type("queue.updated", queued_task_id=one, state="completed"))
        second = await launched(s, second, 3)
        assert second.subtasks
        await stop(s, second.subtasks[1].agent_id)
        current = s.live.plans.get(second.plan_id)
        assert current.subtasks
        assert [t.state for t in current.subtasks] == ["merged", "rejected", "cancelled", "running"]
        await stop(s, current.subtasks[3].agent_id)
        await s.until(is_type("queue.updated", queued_task_id=two, state="completed"))
        assert s.live.plans.get(first.plan_id).started_by == "agent-90"
        assert s.live.plans.get(second.plan_id).started_by == "agent-91"
        for e in s.events():
            if e.type == "agent.status":
                assert (e.payload.plan_id, e.payload.queued_task_id) in {
                    (first.plan_id, one),
                    (second.plan_id, two),
                }
        ids = [a for item in s.live.dispatcher.items.values() for a in item.agent_ids]
        assert len(ids) == len(set(ids)) == 4
        assert calls == 4 and all(holder is None for holder in s.live._slots.values())


async def test_raised_limit_dispatch_still_refuses_an_unknown_queued_revision(
    tmp_path: Path,
) -> None:
    async with (
        asyncio.timeout(DEADLINE_S),
        plan_server(two_fakes(tmp_path, max_agents=2, lead_max_subtasks=8)) as s,
    ):
        plan = await lead_plan(s, [[]] + [[i - 1] for i in range(1, 8)])
        async with s.live.dispatcher.queue_lock:
            item = queued(EnqueueTask(plan_id=plan.plan_id, plan_revision=999), 1)
            await s.live.dispatcher._enqueue(item, plan)
        s.live.dispatcher.wake.set()
        await s.until(
            is_type(
                "queue.updated",
                state="failed",
                detail="plan content differs from the queued revision",
            )
        )
        assert s.live.plans.get(plan.plan_id).state == "failed"
        assert not s.live._agents and all(holder is None for holder in s.live._slots.values())
