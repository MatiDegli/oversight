"""Regression cases from the plan service's first independent review."""

# Tests hold launch boundaries to inspect server-owned reservations and plan state.
# pyright: reportPrivateUsage=false

import asyncio
from pathlib import Path
from typing import Any

import pytest
from live_helpers import DEADLINE_S, GatedModel, error_code, is_type, live_settings
from plan_helpers import plan_server as live_server

from oversight.api import ApiError
from oversight.changes import ChangeManager

pytestmark = pytest.mark.anyio


async def test_refused_queue_launch_preserves_a_direct_starts_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queued, refuse, refusing = asyncio.Event(), asyncio.Event(), asyncio.Event()
    direct_entered, direct_gate, released = asyncio.Event(), asyncio.Event(), asyncio.Event()
    real_open = ChangeManager.open

    async def open_tree(self: ChangeManager, task: Any, *args: Any, **kwargs: Any) -> Any:
        if task.agent_id == "agent-1":
            queued.set()
            await refuse.wait()
            raise ApiError("internal", "refused queued launch")
        direct_entered.set()
        await direct_gate.wait()
        return await real_open(self, task, *args, **kwargs)

    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: GatedModel()) as s,
    ):
        dispatcher = s.live.dispatcher
        real_refused, real_release = dispatcher._refused, dispatcher._release_reservations

        async def held_refusal(*args: Any, **kwargs: Any) -> None:
            refusing.set()  # _launch has freed A, and the dispatcher now holds queue_lock.
            await direct_entered.wait()
            await real_refused(*args, **kwargs)

        def release(*args: Any) -> None:
            real_release(*args)
            released.set()

        monkeypatch.setattr(ChangeManager, "open", open_tree)
        monkeypatch.setattr(dispatcher, "_refused", held_refusal)
        monkeypatch.setattr(dispatcher, "_release_reservations", release)
        response = await s.client.post(
            "/queue",
            json={
                "agent": {
                    "task": "Queued",
                    "slot": "A",
                    "model": None,
                    "zone": None,
                }
            },
        )
        assert response.status_code == 201
        await queued.wait()
        refuse.set()
        await refusing.wait()
        direct = asyncio.create_task(s.client.post("/agents", json={"task": "Direct", "slot": "A"}))
        try:
            await direct_entered.wait()
            await released.wait()
            assert s.live._slots["A"] == "starting"
        finally:
            direct_gate.set()
            response = await direct
        assert response.status_code == 201
        assert s.live._slots["A"] == response.json()["agent_id"]


async def test_plan_blocks_when_only_the_unconfigured_slot_is_free(tmp_path: Path) -> None:
    settings = live_settings(tmp_path, max_agents=2, agent_model_b="muse-spark", meta_api_key=None)
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(settings, lambda profile, _s: GatedModel(profile)) as s,
    ):
        response = await s.client.post("/agents", json={"task": "Hold configured A", "slot": "A"})
        assert response.status_code == 201
        plan_id = (await s.client.post("/plans", json={"task": "Wait for configured A"})).json()[
            "plan_id"
        ]
        response = await s.client.post("/queue", json={"plan_id": plan_id, "plan_revision": 2})
        assert response.status_code == 201
        identifier = response.json()["queued_task_id"]
        await s.until(
            lambda event: any(
                is_type("queue.updated", queued_task_id=identifier, state=state)(event)
                for state in ("blocked", "failed")
            )
        )
        item = s.live.dispatcher.items[identifier]
        assert item.state == "blocked" and item.detail and not item.agent_ids
        plan = s.live.plans.get(plan_id)
        assert plan.state == "proposed" and plan.queued_task_id == identifier
        assert plan.subtasks and plan.subtasks[0].state == "waiting"
        assert s.live._slots == {"A": "agent-1", "B": None}
        assert (await s.client.post("/agents/agent-1/stop")).status_code == 202
        await s.until(is_type("queue.updated", queued_task_id=identifier, state="running"))
        assert s.live.plans.get(plan_id).state == "running" and s.live._slots["B"] is None


@pytest.mark.parametrize("zone_kind", ["empty", "file", "node"])
async def test_person_plan_put_refuses_a_changed_whole_graph_zone(
    tmp_path: Path, zone_kind: str
) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        plan_id = (
            await s.client.post("/plans", json={"task": "Keep the whole graph zone"})
        ).json()["plan_id"]
        plan = (await s.client.get(f"/plans/{plan_id}")).json()
        draft = {key: plan["subtasks"][0][key] for key in ("task", "zone", "depends_on")}
        original_zone = draft["zone"]
        _, graph = s.store.graph()
        assert graph is not None
        zones = {
            "empty": {"paths": [], "node_ids": []},
            "file": {"paths": original_zone["paths"][:1], "node_ids": []},
            "node": {"paths": [], "node_ids": [graph.nodes[0].id]},
        }
        seq = s.store.last_seq
        body = {
            "revision": plan["revision"],
            "graph_version": plan["graph_version"],
            "subtasks": [draft | {"zone": zones[zone_kind]}],
        }
        response = await s.client.put(f"/plans/{plan_id}", json=body)
        assert response.status_code == 422, response.text
        assert error_code(response) == (
            422,
            "invalid_request",
        )
        assert s.store.last_seq == seq
        assert (await s.client.get(f"/plans/{plan_id}")).json() == plan
        body["subtasks"] = [draft | {"task": "Edited task, same whole graph zone"}]
        assert (await s.client.put(f"/plans/{plan_id}", json=body)).status_code == 202
