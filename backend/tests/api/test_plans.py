"""Person-created plans through real HTTP, git and events; only scripted coding models."""

# Tests inspect runtime ownership to prove revision, launch and provenance invariants.
# pyright: reportPrivateUsage=false

import asyncio
from pathlib import Path
from typing import Any

import pytest
from decision_helpers import observed_body
from live_helpers import (
    DEADLINE_S,
    GatedModel,
    Scripts,
    error_code,
    is_type,
    live_server,
    live_settings,
)
from revise_helpers import ready, request, scripted_a

from oversight.api import ApiError
from oversight.changes import ChangeManager, plan_core, read_plan, read_queue
from oversight_contracts.commands import ErrorCode
from oversight_contracts.plans import Plan

pytestmark = pytest.mark.anyio
TASK = "Document the coupon.\n  Keep this whitespace. "


async def create(s: Any) -> Plan:
    response = await s.client.post("/plans", json={"task": TASK})
    assert response.status_code == 201, response.text
    response = await s.client.get(f"/plans/{response.json()['plan_id']}")
    assert response.status_code == 200
    return Plan.model_validate(response.json())


def edit_body(plan: Plan, **updates: Any) -> dict[str, Any]:
    assert plan.subtasks is not None
    return {
        "revision": plan.revision,
        "graph_version": plan.graph_version,
        "subtasks": [
            {"task": t.task, "zone": t.zone.model_dump(), "depends_on": t.depends_on}
            for t in plan.subtasks
        ],
    } | updates


async def queue(s: Any, plan: Plan, *, via_start: bool = False) -> str:
    response = (
        await s.client.post(f"/plans/{plan.plan_id}/start", json={"revision": plan.revision})
        if via_start
        else await s.client.post(
            "/queue", json={"plan_id": plan.plan_id, "plan_revision": plan.revision}
        )
    )
    assert response.status_code == (202 if via_start else 201), response.text
    return s.live.plans.get(plan.plan_id).queued_task_id


async def plan_state(s: Any, identifier: str, state: str) -> None:
    await s.until(is_type("plan.updated", plan_id=identifier, state=state))


async def folded(s: Any) -> None:
    async with s.live.dispatcher.queue_lock:
        revisions: dict[str, int] = {}
        plans: dict[str, Plan] = {}
        for event in s.events():  # LiveServer validates every envelope against the contract.
            if event.type == "plan.updated":
                plan = event.payload
                assert plan.revision == revisions.get(plan.plan_id, 0) + 1
                revisions[plan.plan_id] = plan.revision
                plans[plan.plan_id] = plan
        assert plans == s.live.plans.plans
        assert all(read_plan(s.store, p.plan_id) == p for p in plans.values())
        assert read_queue(s.store).tasks == list(s.live.dispatcher.items.values())


async def test_create_edit_reject_preserves_verbatim_task_without_a_model(tmp_path: Path) -> None:
    clients = Scripts()
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path), clients) as s:
        plan = await create(s)
        assert plan.revision == 2 and plan.state == "proposed"
        assert plan.task == TASK and plan.started_by is None and plan.queued_task_id is None
        assert plan.subtasks and plan.subtasks[0].task == TASK
        assert plan.subtasks[0].state == "waiting" and plan.subtasks[0].agent_id is None
        _, snapshot = s.store.graph()
        assert snapshot is not None
        assert plan.subtasks[0].zone == plan_core.whole_graph(snapshot)
        body = edit_body(plan)
        body["subtasks"][0]["task"] = "Edited by the person"
        assert (await s.client.put(f"/plans/{plan.plan_id}", json=body)).status_code == 202
        edited = read_plan(s.store, plan.plan_id)
        assert edited.revision == 3 and edited.task == TASK
        assert edited.subtasks and edited.subtasks[0].task == "Edited by the person"
        assert (
            await s.client.post(
                f"/plans/{plan.plan_id}/reject", json={"revision": 3, "comment": "Not now"}
            )
        ).status_code == 202
        rejected = read_plan(s.store, plan.plan_id)
        assert (
            rejected.revision == 4 and rejected.state == "rejected" and rejected.detail == "Not now"
        )
        assert rejected.subtasks and rejected.subtasks[0].state == "cancelled"
        assert clients.calls == 0 and not s.live._agents
        await folded(s)


async def test_validation_exact_revisions_graph_version_and_one_subtask_limit(
    tmp_path: Path,
) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        assert error_code(await s.client.post("/plans", json={"task": " "})) == (
            422,
            "invalid_request",
        )
        assert error_code(await s.client.get("/plans/missing")) == (404, "not_found")
        plan = await create(s)
        path = f"/plans/{plan.plan_id}"
        for body in (edit_body(plan, revision=1), edit_body(plan, graph_version="stale")):
            assert error_code(await s.client.put(path, json=body)) == (409, "wrong_state")
        for count in (0, 2, 6, 7):
            body = edit_body(plan)
            body["subtasks"] *= count
            assert error_code(await s.client.put(path, json=body)) == (422, "invalid_request")
        for suffix in ("start", "reject"):
            assert error_code(await s.client.post(f"{path}/{suffix}", json={"revision": 1})) == (
                409,
                "wrong_state",
            )
            assert error_code(
                await s.client.post(f"/plans/missing/{suffix}", json={"revision": 1})
            ) == (404, "not_found")
        assert error_code(await s.client.put("/plans/missing", json=edit_body(plan))) == (
            404,
            "not_found",
        )
        assert error_code(
            await s.client.post("/queue", json={"plan_id": plan.plan_id, "plan_revision": 1})
        ) == (409, "wrong_state")
        assert read_plan(s.store, plan.plan_id) == plan
        await folded(s)


@pytest.mark.parametrize("decision", ["approve", "reject", "stop", "request-changes"])
@pytest.mark.parametrize("via_start", [False, True])
async def test_single_subtask_lifecycle_and_status_provenance(
    tmp_path: Path, decision: str, via_start: bool
) -> None:
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: scripted_a()) as s,
    ):
        plan = await create(s)
        identifier = await queue(s, plan, via_start=via_start)
        await plan_state(s, plan.plan_id, "running")
        await ready(s)
        current = read_plan(s.store, plan.plan_id)
        assert current.revision == 4 and current.queued_task_id == identifier
        assert s.live.dispatcher.items[identifier].plan_revision == 2
        assert current.subtasks and current.subtasks[0].agent_id == "agent-1"
        assert error_code(
            await s.client.put(f"/plans/{plan.plan_id}", json=edit_body(current))
        ) == (409, "wrong_state")
        if decision == "request-changes":
            seq = s.store.last_seq
            assert (await request(s)).status_code == 202
            await ready(s, after=seq)
            assert read_plan(s.store, plan.plan_id) == current
            assert s.live.dispatcher.items[identifier].state == "running"
            decision = "approve"
        if decision == "stop":
            response = await s.client.post("/agents/agent-1/stop")
        else:
            response = await s.client.post(f"/changes/change-1/{decision}", json=observed_body(s))
        assert response.status_code == 202
        await s.until(is_type("queue.updated", queued_task_id=identifier, state="completed"))
        final = read_plan(s.store, plan.plan_id)
        assert final.state == "completed" and final.revision == 5
        assert final.subtasks and final.subtasks[0].state == (
            "merged" if decision == "approve" else "rejected"
        )
        plan_done = next(e.seq for e in s.events() if is_type("plan.updated", state="completed")(e))
        item_done = next(
            e.seq for e in s.events() if is_type("queue.updated", state="completed")(e)
        )
        assert item_done > plan_done
        statuses = [e.payload for e in s.events() if e.type == "agent.status"]
        assert plan.subtasks is not None
        assert statuses and all(
            status.plan_id == plan.plan_id
            and status.zone == plan.subtasks[0].zone
            and status.queued_task_id == identifier
            and status.started_by is None
            for status in statuses
        )
        await folded(s)


async def test_queued_plan_freeze_cancel_reenqueue_and_reject_are_fifo(tmp_path: Path) -> None:
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: GatedModel()) as s,
    ):
        await s.client.post("/agents", json={"task": "Hold A"})
        standalone = await s.client.post(
            "/queue",
            json={
                "agent": {
                    "task": "earlier",
                    "slot": None,
                    "model": None,
                    "zone": None,
                }
            },
        )
        assert standalone.status_code == 201
        plan = await create(s)
        identifier = await queue(s, plan, via_start=True)
        linked = read_plan(s.store, plan.plan_id)
        assert linked.state == "proposed" and linked.revision == 3
        assert error_code(await s.client.put(f"/plans/{plan.plan_id}", json=edit_body(linked))) == (
            409,
            "wrong_state",
        )
        assert error_code(
            await s.client.post(f"/plans/{plan.plan_id}/start", json={"revision": 3})
        ) == (409, "wrong_state")
        item = s.live.dispatcher.items[identifier]
        assert (
            await s.client.post(f"/queue/{identifier}/cancel", json={"revision": item.revision})
        ).status_code == 202
        unlinked = read_plan(s.store, plan.plan_id)
        assert (
            unlinked.state == "proposed"
            and unlinked.queued_task_id is None
            and unlinked.revision == 4
        )
        assert plan_core.content_matches(unlinked, plan)
        identifier2 = await queue(s, unlinked)
        assert identifier2 != identifier
        assert (
            await s.client.post(f"/plans/{plan.plan_id}/reject", json={"revision": 5})
        ).status_code == 202
        assert s.live.dispatcher.items[identifier2].state == "cancelled"
        assert read_plan(s.store, plan.plan_id).state == "rejected"
        assert set(s.live._agents) == {"agent-1"}
        await folded(s)


@pytest.mark.parametrize(
    "code,state",
    [
        ("slot_busy", "queued"),
        ("spend_brake", "blocked"),
        ("wrong_state", "blocked"),
        ("not_configured", "blocked"),
        ("invalid_request", "failed"),
        ("internal", "failed"),
    ],
)
async def test_before_first_launch_refusal_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: ErrorCode, state: str
) -> None:
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: GatedModel()) as s,
    ):
        original = s.live._check_start

        def refused(*_args: Any) -> Any:
            raise ApiError(code, "refused before launch")

        monkeypatch.setattr(s.live, "_check_start", refused)
        plan = await create(s)
        identifier = await queue(s, plan)
        await s.until(
            is_type(
                "queue.updated",
                queued_task_id=identifier,
                state=state,
                detail="refused before launch",
            )
        )
        item = s.live.dispatcher.items[identifier]
        assert not item.agent_ids and not s.live._agents and s.live._slots["A"] is None
        current = read_plan(s.store, plan.plan_id)
        assert current.state == ("failed" if state == "failed" else "proposed")
        assert current.queued_task_id == identifier and current.subtasks
        assert current.subtasks[0].state == ("cancelled" if state == "failed" else "waiting")
        if state == "failed":
            assert current.detail == item.detail
        else:
            monkeypatch.setattr(s.live, "_check_start", original)
            s.live.dispatcher.wake.set()
            await plan_state(s, plan.plan_id, "running")
        await folded(s)


@pytest.mark.parametrize(
    "refusal", [None, "slot_busy", "spend_brake", "wrong_state", "invalid_request", "internal"]
)
async def test_reject_during_held_launch_and_launch_refusals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, refusal: ErrorCode | None
) -> None:
    entered, gate = asyncio.Event(), asyncio.Event()
    real = ChangeManager.open

    async def held(self: ChangeManager, *args: Any, **kwargs: Any) -> Any:
        entered.set()
        await gate.wait()
        if refusal is not None:
            raise ApiError(refusal, "refused launch")
        return await real(self, *args, **kwargs)

    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: GatedModel()) as s,
    ):
        monkeypatch.setattr(ChangeManager, "open", held)
        plan = await create(s)
        identifier = await queue(s, plan)
        await entered.wait()
        try:
            assert error_code(
                await s.client.post(f"/plans/{plan.plan_id}/reject", json={"revision": 3})
            ) == (409, "wrong_state")
            assert error_code(
                await s.client.post(f"/queue/{identifier}/cancel", json={"revision": 1})
            ) == (409, "wrong_state")
            if refusal is not None:

                def still_refused(*_args: Any) -> Any:
                    raise ApiError(refusal, "refused launch")

                monkeypatch.setattr(s.live, "_check_start", still_refused)
            gate.set()
            if refusal is None:
                await plan_state(s, plan.plan_id, "running")
            else:
                state = {
                    "slot_busy": "queued",
                    "spend_brake": "blocked",
                    "wrong_state": "blocked",
                }.get(refusal, "failed")
                await s.until(
                    is_type(
                        "queue.updated",
                        queued_task_id=identifier,
                        state=state,
                        detail="refused launch",
                    )
                )
                assert not s.live._agents and s.live._slots["A"] is None
            await folded(s)
        finally:
            gate.set()


@pytest.mark.parametrize(
    "field,value",
    [
        ("task", "Changed"),
        ("zone", {"node_ids": [], "paths": ["other.py"]}),
        ("depends_on", [0]),
    ],
)
async def test_dispatch_rejects_changed_queued_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: Any
) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        # Hold dispatch before selection, then simulate internal corruption of frozen content.
        await s.live.dispatcher.close()
        s.live.dispatcher._closed = False
        plan = await create(s)
        identifier = await queue(s, plan)
        current = s.live.plans.revisions[plan.plan_id, plan.revision]
        assert current.subtasks is not None
        updates = {field: value}
        if field == "zone":
            from oversight_contracts.plans import PlanZone

            updates[field] = PlanZone.model_validate(value)
        current.subtasks[0] = current.subtasks[0].model_copy(update=updates)
        s.live.dispatcher.start()
        s.live.dispatcher.wake.set()
        await plan_state(s, plan.plan_id, "failed")
        await s.until(is_type("queue.updated", queued_task_id=identifier, state="failed"))
        assert not s.live._agents
        # The invalid dependency is a deliberate corruption; the failure cancels it safely.
        await folded(s)


async def test_dispatch_runs_the_edited_revision(tmp_path: Path) -> None:
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: GatedModel()) as s,
    ):
        plan = await create(s)
        body = edit_body(plan)
        body["subtasks"][0]["task"] = "The person's exact edited task."
        assert (await s.client.put(f"/plans/{plan.plan_id}", json=body)).status_code == 202
        edited = read_plan(s.store, plan.plan_id)
        identifier = await queue(s, edited)
        await plan_state(s, plan.plan_id, "running")
        await s.until(is_type("agent.status", state="working"))
        statuses = [e.payload for e in s.events() if e.type == "agent.status"]
        assert statuses and all(status.task == body["subtasks"][0]["task"] for status in statuses)
        assert s.live.dispatcher.items[identifier].plan_revision == 3
        await folded(s)


async def test_person_count_guard_and_lead_plan_edits_preserve_provenance(tmp_path: Path) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        plan = await create(s)
        drafts = edit_body(plan)["subtasks"] * 2
        # Synthetic lead input uses the same generic core; POST /plans never sets started_by.
        from oversight_contracts.commands import UpdatePlan

        multi = plan_core.update(plan, UpdatePlan.model_validate(edit_body(plan, subtasks=drafts)))
        await s.live.plans.save(multi)
        assert error_code(
            await s.client.post(
                "/queue",
                json={
                    "plan_id": plan.plan_id,
                    "plan_revision": 3,
                },
            )
        ) == (422, "invalid_request")
        multi = multi.model_copy(update={"started_by": "agent-99"})
        # Test-only seeding, before exposing the lead front's producer.
        s.live.plans.plans[plan.plan_id] = multi
        assert (
            await s.client.put(f"/plans/{plan.plan_id}", json=edit_body(multi))
        ).status_code == 202
        updated = read_plan(s.store, plan.plan_id)
        assert updated.started_by == "agent-99" and updated.subtasks and len(updated.subtasks) == 2


async def test_unexpected_launch_failure_fails_the_plan_and_cancels_waiting_subtask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failed(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("injected launch failure")

    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        monkeypatch.setattr(ChangeManager, "open", failed)
        plan = await create(s)
        identifier = await queue(s, plan)
        await s.until(is_type("queue.updated", queued_task_id=identifier, state="failed"))
        final = read_plan(s.store, plan.plan_id)
        assert final.state == "failed" and final.detail
        assert final.subtasks and final.subtasks[0].state == "cancelled"
        assert not s.live._agents and s.live._slots["A"] is None
        assert s.live.dispatcher.items[identifier].detail == final.detail
        await folded(s)
