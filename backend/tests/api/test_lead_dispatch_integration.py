"""Joined lead/queue proof: concurrent configured plans share coding seats after person queueing."""

# Integration inspects ownership and uses independent scripted clients, including coding agents.
# pyright: reportPrivateUsage=false

import asyncio
from pathlib import Path
from typing import Any

import pytest
from backend.tests.agents.lead_helpers import call, turn
from backend.tests.api.lead_helpers import ThreadModels, idle, send
from backend.tests.api.test_lead_concurrency import HeldScript
from decision_helpers import observed_body
from live_helpers import DEADLINE_S, is_type, live_server, two_fakes

from oversight.agents.client import CALL_SCOPE

pytestmark = pytest.mark.anyio


async def test_two_large_lead_plans_dispatch_dependencies_through_shared_seats(
    tmp_path: Path,
) -> None:
    drafts = [{"task": "s" * 2100, "depends_on": [7] if i < 7 else []} for i in range(8)]
    scripts = ThreadModels(
        [
            [
                turn(call("request_plan", task=label * 2100, subtasks=drafts)),
                {"text": label + " ready"},
            ]
            for label in ("a", "b")
        ]
    )
    coder = ThreadModels([[turn(call("finish", summary="No code change needed."))]])
    built: asyncio.Queue[HeldScript] = asyncio.Queue()

    def factory(profile: Any, slot: Any) -> Any:
        scope = CALL_SCOPE.get()
        if scope is not None and scope.role == "lead":
            model = HeldScript(scripts(profile, slot))
            built.put_nowait(model)
            return model
        return coder(profile, slot)

    settings = two_fakes(
        tmp_path, max_agents=2, lead_max_plans=1, lead_max_subtasks=8, lead_max_task_chars=3000
    )
    async with asyncio.timeout(DEADLINE_S), live_server(settings, factory) as s:
        clients = []
        for _ in range(2):
            assert (await s.client.post("/leads", json={"task": "Coordinate"})).status_code == 201
            clients.append(await built.get())
            await clients[-1].entered.wait()
        for client in clients:
            client.release.set()
        await asyncio.gather(idle(s, agent_id="agent-1"), idle(s, agent_id="agent-2"))
        plans = list(s.live.plans.plans.values())
        assert len(plans) == 2 and not s.live._agents and not coder.built
        for plan in plans:
            assert plan.started_by in {"agent-1", "agent-2"}
            assert plan.subtasks and len(plan.subtasks) == 8
            assert len(plan.task) == len(plan.subtasks[0].task) == 2100
            response = await s.client.post(
                "/queue", json={"plan_id": plan.plan_id, "plan_revision": plan.revision}
            )
            assert response.status_code == 201, response.text
            await s.until(is_type("plan.updated", plan_id=plan.plan_id, state="running"))
        assert len(s.live._agents) == 2
        for plan in plans:
            running = s.live.plans.get(plan.plan_id)
            assert running.subtasks and running.subtasks[7].agent_id is not None
            agent_id = running.subtasks[7].agent_id
            await s.until(is_type("agent.status", agent_id=agent_id, state="ready_for_review"))
            status = next(
                e.payload
                for e in reversed(s.events())
                if e.type == "agent.status" and e.payload.agent_id == agent_id
            )
            after = s.store.last_seq
            assert (
                await s.client.post(
                    f"/changes/{status.change_id}/approve", json=observed_body(s, status.change_id)
                )
            ).status_code == 202
            await s.until(is_type("change.resolved", agent_id=agent_id, merge="merged"), after)
        await s.until(
            lambda e: (
                e.type == "plan.updated"
                and e.payload.subtasks is not None
                and any(t.agent_id is not None for t in e.payload.subtasks[:7])
            )
        )
        assert sum(v is not None for v in s.live._slots.values()) <= 2
        for plan in s.live.plans.plans.values():
            assert plan.subtasks and any(t.state == "waiting" for t in plan.subtasks[:7])
            assert plan.started_by in {"agent-1", "agent-2"}
        for event in s.events():
            if event.type == "agent.status":
                assert event.payload.plan_id in {p.plan_id for p in plans}
                assert event.payload.started_by is None
        for agent_id, label, other in (("agent-1", "a", "b"), ("agent-2", "b", "a")):
            sending = asyncio.create_task(send(s, "keep my budget", agent_id))
            client = await built.get()
            client.release.set()
            await sending
            lead = s.live.leads.leads[agent_id]
            assert lead.run is not None and lead.turns == 2
            assert len(lead.run.tools.plan_ids) == 1
            assert "already requested 1 plans" in str(client.requests)
            assert label + " ready" in str(client.requests[0])
            assert other + " ready" not in str(client.requests[0])
        # Dispatch reserves both seats before launching them sequentially. Wait for actual
        # owners under the enclosing timeout so a pending launch cannot change this snapshot.
        await s.until(
            lambda _: all(holder not in {None, "starting"} for holder in s.live._slots.values())
        )
        slots = dict(s.live._slots)
        stopped = s.live.leads.leads["agent-1"]
        assert (
            await s.client.post("/leads/agent-1/stop", json={"revision": stopped.revision})
        ).status_code == 202
        assert stopped.task is not None
        await stopped.task
        assert s.live.leads.leads["agent-2"].phase == "idle" and s.live._slots == slots
        assert all(p.started_by in {"agent-1", "agent-2"} for p in s.live.plans.plans.values())
