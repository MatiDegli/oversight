"""The plan HTTP entry makes one fake-model call, and never starts or queues work."""

# Inspect run-local ownership and call scopes without any external transport.
# pyright: reportPrivateUsage=false

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from live_helpers import (
    DEADLINE_S,
    GatedModel,
    error_code,
    live_server,
    live_settings,
    meta_settings,
)

from oversight.agents import Message, ModelAPIError, ModelReply, ToolSchema, Usage
from oversight.agents.client import CALL_SCOPE
from oversight.changes import plan_core
from oversight_contracts.events import ModelProfile
from oversight_contracts.plans import Plan, SubtaskDraft

pytestmark = pytest.mark.anyio
TASK = "The person's task.\n  Preserve its whitespace. "


@dataclass
class PlanModel:
    profile: ModelProfile
    text: str = '{"task":"The model subtask"}'
    usage: Usage = field(default_factory=Usage)
    calls: list[list[Message]] = field(default_factory=list)
    error: Exception | None = None
    closed: bool = False

    async def aclose(self) -> None:
        self.closed = True

    async def complete(
        self, messages: Sequence[Message], tools: Sequence[ToolSchema]
    ) -> ModelReply:
        assert not tools
        scope = CALL_SCOPE.get()
        assert scope is not None and scope.role == "plan"
        self.calls.append(list(messages))
        if self.error is not None:
            raise self.error
        return ModelReply(self.text, [], self.usage, self.profile.model)


@dataclass
class ClosableGatedModel(GatedModel):
    closed: bool = False

    async def aclose(self) -> None:
        self.closed = True


@dataclass
class FailingCloseModel(PlanModel):
    close_attempts: int = 0

    async def aclose(self) -> None:
        self.close_attempts += 1
        if self.close_attempts == 1:
            raise OSError("injected close failure")
        await super().aclose()


async def create(s: Any) -> Plan:
    response = await s.client.post("/plans", json={"task": TASK})
    assert response.status_code == 201, response.text
    response = await s.client.get(f"/plans/{response.json()['plan_id']}")
    return Plan.model_validate(response.json())


@pytest.mark.parametrize(
    "text,error,reason",
    [
        ('{"task":"The model subtask"}', None, "model wrote"),
        ("not JSON", None, "invalid"),
        ('{"task":"extra","zone":[]}', None, "invalid"),
        ('{"task":"' + "x" * 4001 + '"}', None, "invalid"),
        ("", ModelAPIError("fake failure"), "ModelAPIError"),
    ],
)
async def test_post_makes_exactly_one_logical_call_and_does_not_queue(
    tmp_path: Path,
    text: str,
    error: Exception | None,
    reason: str,
) -> None:
    models: list[PlanModel] = []

    def factory(profile: ModelProfile, _slot: Any) -> PlanModel:
        model = PlanModel(profile, text=text, error=error)
        models.append(model)
        return model

    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path), factory) as s:
        plan = await create(s)
        assert len(models) == 1 and len(models[0].calls) == 1
        assert models[0].closed
        assert models[0].calls[0][1]["content"] == TASK
        assert plan.detail and reason in plan.detail
        assert plan.task == TASK and plan.state == "proposed" and plan.revision == 2
        assert plan.subtasks and len(plan.subtasks) == 1
        subtask = plan.subtasks[0]
        assert subtask.task == ("The model subtask" if reason == "model wrote" else TASK)
        _, snapshot = s.store.graph()
        assert snapshot is not None and plan.graph_version == snapshot.version
        assert subtask.zone == plan_core.whole_graph(snapshot)
        assert subtask.depends_on == [] and subtask.agent_id is None and subtask.state == "waiting"
        assert not s.live._agents and s.live._slots["A"] is None
        assert not any(e.type in {"queue.updated", "agent.status"} for e in s.events())
        ticks = [e.payload for e in s.events() if e.type == "usage.tick"]
        assert len(ticks) == (0 if error else 1)
        if ticks:
            tick = ticks[0]
            assert tick.role == "plan" and tick.plan_id == plan.plan_id
            assert tick.change_id is None and tick.agent_id == "agent-1" and tick.cost_usd is None
        second = await create(s)
        assert second.plan_id != plan.plan_id and len(models) == 2
        assert all(len(model.calls) == 1 for model in models)
        assert all(model.closed for model in models)


@pytest.mark.parametrize("api_error", [False, True])
async def test_failed_client_close_preserves_the_proposal(tmp_path: Path, api_error: bool) -> None:
    models: list[FailingCloseModel] = []

    def factory(profile: ModelProfile, _slot: Any) -> FailingCloseModel:
        model = FailingCloseModel(
            profile, error=ModelAPIError("injected API failure") if api_error else None
        )
        models.append(model)
        return model

    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path), factory) as s:
        plan = await create(s)
        assert plan.state == "proposed" and plan.subtasks
        assert plan.subtasks[0].task == (TASK if api_error else "The model subtask")
        assert plan.detail and ("fallback" if api_error else "model wrote") in plan.detail
        assert len(models) == 1 and models[0].close_attempts == 1 and not models[0].closed
    assert models[0].close_attempts == 2 and models[0].closed


@pytest.mark.parametrize("refusal", ["setup", "brake"])
async def test_post_refused_before_a_call_or_a_plan(tmp_path: Path, refusal: str) -> None:
    built: list[object] = []

    def factory(profile: ModelProfile, _slot: Any) -> PlanModel:
        built.append(profile)
        return PlanModel(profile)

    settings = meta_settings(tmp_path, **({"meta_api_key": None} if refusal == "setup" else {}))
    async with asyncio.timeout(DEADLINE_S), live_server(settings, factory) as s:
        if refusal == "brake":
            s.live._ledger_lost = True
        seq = s.store.last_seq
        response = await s.client.post("/plans", json={"task": TASK})
        assert error_code(response) == (
            (503, "not_configured") if refusal == "setup" else (403, "spend_brake")
        )
        assert not built and not s.live.plans.plans and s.live._count == 0
        assert s.store.last_seq == seq


async def test_lead_drafts_never_call_a_model_or_queue(tmp_path: Path) -> None:
    built: list[object] = []

    def factory(profile: ModelProfile, _slot: Any) -> PlanModel:
        built.append(profile)
        return PlanModel(profile)

    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path), factory) as s:
        _, snapshot = s.store.graph()
        assert snapshot is not None
        drafts = [SubtaskDraft(task="Lead's exact draft", zone=plan_core.whole_graph(snapshot))]
        created = await s.live.plans.create_and_propose(TASK, drafts, started_by="agent-99")
        plan = s.live.plans.get(created.plan_id)
        assert plan.started_by == "agent-99" and plan.subtasks
        assert plan.subtasks[0].task == drafts[0].task and plan.revision == 2
        assert not built and s.live._count == 0
        assert not any(e.type in {"usage.tick", "queue.updated"} for e in s.events())


async def test_planner_id_does_not_collide_with_next_author(tmp_path: Path) -> None:
    def factory(profile: ModelProfile, _slot: Any) -> Any:
        scope = CALL_SCOPE.get()
        return PlanModel(profile) if scope and scope.role == "plan" else GatedModel(profile)

    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path), factory) as s:
        await create(s)
        response = await s.client.post("/agents", json={"task": "Person starts an author"})
        assert response.status_code == 201 and response.json()["agent_id"] == "agent-2"
        assert not any(
            e.type == "agent.status" and e.payload.agent_id == "agent-1" for e in s.events()
        )


async def test_held_plan_does_not_block_queue_commands(tmp_path: Path) -> None:
    held = GatedModel()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: held) as s,
    ):
        pending = asyncio.create_task(s.client.post("/plans", json={"task": TASK}))
        await held.entered.wait()
        try:
            async with s.live.dispatcher.queue_lock:
                assert s.live.plans.get("plan-1").state == "planning"
        finally:
            held.gate.set()
        assert (await pending).status_code == 201
