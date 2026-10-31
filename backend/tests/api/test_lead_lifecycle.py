"""Stop, shutdown and accounting failures leave exactly one committed lead ending."""

# Lifecycle tests inspect task ownership and inject durable-store failures.
# pyright: reportPrivateUsage=false

import ast
import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest
from live_helpers import (
    DEADLINE_S,
    GatedModel,
    error_code,
    is_type,
    live_server,
    meta_settings,
    two_fakes,
)

from oversight.agents import (
    FAKE_MODEL,
    LedgerError,
    LedgerState,
    Message,
    ModelReply,
    Script,
    ScriptedModel,
    ToolSchema,
    load_script,
)
from oversight.agents.client import CALL_SCOPE
from oversight.settings import REPO_ROOT
from oversight_contracts.events import LeadStatus, ModelProfile

pytestmark = pytest.mark.anyio
LEAD_SCRIPT = REPO_ROOT / "fixtures/fake-model/lead-two-plans.json"


@dataclass
class AfterPlans:
    """Play the first two scripted turns, then gate the finish call."""

    profile: ModelProfile = FAKE_MODEL
    script: Script = field(default_factory=lambda: load_script(LEAD_SCRIPT))
    held: GatedModel = field(default_factory=GatedModel)
    calls: int = 0
    closed: bool = False

    def __post_init__(self) -> None:
        self.inner = ScriptedModel(
            replace(
                self.script, turns=[replace(r, model=self.profile.model) for r in self.script.turns]
            ),
            self.profile,
        )

    async def complete(
        self, messages: Sequence[Message], tools: Sequence[ToolSchema]
    ) -> ModelReply:
        self.calls += 1
        if self.calls == 3:
            await self.held.complete(messages, tools)
        return await self.inner.complete(messages, tools)

    async def aclose(self) -> None:
        self.closed = True


async def current(s: Any, agent_id: str = "agent-1") -> dict[str, Any]:
    response = await s.client.get(f"/leads/{agent_id}")
    assert response.status_code == 200, response.text
    return response.json()


def terminal(s: Any) -> list[Any]:
    return [
        e.payload
        for e in s.events()
        if e.type == "lead.status" and e.payload.state in {"halted", "stopped", "done"}
    ]


async def test_stop_answers_404_unknown_and_409_stale_or_terminal(tmp_path: Path) -> None:
    held = GatedModel()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(two_fakes(tmp_path, max_agents=2), lambda _p, _s: held) as s,
    ):
        assert error_code(await s.client.post("/leads/agent-999/stop", json={"revision": 1})) == (
            404,
            "not_found",
        )
        await s.client.post("/leads", json={"task": "Coordinate"})
        await held.entered.wait()
        latest = await current(s)
        stale = await s.client.post("/leads/agent-1/stop", json={"revision": 1})
        assert error_code(stale) == (409, "wrong_state")
        assert (
            stale.json()["error"]["message"]
            == f"stale lead revision 1; the latest is {latest['revision']}"
        )
        accepted = await s.client.post("/leads/agent-1/stop", json={"revision": latest["revision"]})
        assert accepted.status_code == 202
        await s.until(is_type("lead.status", state="stopped"))
        assert error_code(
            await s.client.post(
                "/leads/agent-1/stop", json={"revision": (await current(s))["revision"]}
            )
        ) == (409, "wrong_state")
        assert len(terminal(s)) == 1 and terminal(s)[0].detail == "Stopped by the person."


async def test_stop_during_a_model_call_ends_stopped_once_with_plans_intact(tmp_path: Path) -> None:
    model = AfterPlans()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(two_fakes(tmp_path, max_agents=2), lambda _p, _s: model) as s,
    ):
        await s.client.post("/leads", json={"task": "Coordinate"})
        await model.held.entered.wait()
        plans = dict(s.live.plans.plans)
        seq = s.store.last_seq
        response = await s.client.post(
            "/leads/agent-1/stop", json={"revision": (await current(s))["revision"]}
        )
        assert response.status_code == 202
        await s.until(is_type("lead.status", state="stopped"))
        assert await model.held.cancelled.wait()
        assert s.live.plans.plans == plans and len(plans) == 2
        assert len(terminal(s)) == 1
        assert terminal(s)[0].detail == "Stopped by the person. Requested plan-1, plan-2."
        assert terminal(s)[0].requested_plan_id is None
        assert not any(
            e.seq > seq
            and e.type in {"plan.updated", "queue.updated", "agent.status", "change.resolved"}
            for e in s.events()
        )
    assert model.closed


async def test_shutdown_during_a_model_call_publishes_one_halted(tmp_path: Path) -> None:
    model = AfterPlans()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(two_fakes(tmp_path, max_agents=2), lambda _p, _s: model) as s,
    ):
        await s.client.post("/leads", json={"task": "Coordinate"})
        await model.held.entered.wait()
        await s.stop()
        assert len(terminal(s)) == 1 and terminal(s)[0].state == "halted"
        assert terminal(s)[0].detail == "The server shut down. Requested plan-1, plan-2."
    assert model.closed


async def test_the_next_run_has_no_lead_and_calls_no_model(tmp_path: Path) -> None:
    built: list[Any] = []

    def factory(profile: Any, _slot: Any) -> Any:
        built.append(profile)
        return GatedModel(profile)

    settings = two_fakes(tmp_path, max_agents=2)
    async with asyncio.timeout(DEADLINE_S), live_server(settings, factory) as s:
        await s.client.post("/leads", json={"task": "Coordinate"})
        await s.until(is_type("lead.status", state="working"))
    assert len(built) == 1
    async with asyncio.timeout(DEADLINE_S), live_server(settings, factory) as s:
        assert not s.live.leads.leads and not any(e.type == "lead.status" for e in s.events())
        assert error_code(await s.client.get("/leads/agent-1")) == (404, "not_found")
        assert len(built) == 1


async def test_the_brake_blocking_mid_run_halts_the_lead(tmp_path: Path) -> None:
    settings = meta_settings(tmp_path)
    script = load_script(LEAD_SCRIPT)
    models: list[ScriptedModel] = []

    def factory(profile: Any, _slot: Any) -> ScriptedModel:
        scope = CALL_SCOPE.get()
        assert scope is not None and scope.role == "lead"
        model = ScriptedModel(
            replace(script, turns=[replace(r, model=profile.model) for r in script.turns]), profile
        )
        models.append(model)
        return model

    async with asyncio.timeout(DEADLINE_S), live_server(settings, factory) as s:
        manager = s.live._manager
        assert manager is not None
        original = manager.spend_received

        def block(receipt: Any) -> None:
            original(receipt)
            s.live.ledger.state = LedgerState(spent_usd=140)

        manager.spend_received = block
        await s.client.post("/leads", json={"task": "Coordinate"})
        await s.until(is_type("lead.status", state="halted"))
        assert terminal(s)[0].detail == "The spending brake stopped the next model call."
        assert len(models[0].requests) == 1 and not s.live.plans.plans


async def test_a_ledger_failure_halts_the_lead_and_reports_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = load_script(LEAD_SCRIPT)

    def factory(profile: Any, _slot: Any) -> Any:
        return ScriptedModel(
            replace(script, turns=[replace(r, model=profile.model) for r in script.turns]), profile
        )

    async with asyncio.timeout(DEADLINE_S), live_server(meta_settings(tmp_path), factory) as s:

        def fail(_state: Any) -> None:
            raise LedgerError("injected write failure")

        monkeypatch.setattr(s.live.ledger, "write", fail)
        await s.client.post("/leads", json={"task": "Coordinate"})
        await s.until(is_type("lead.status", state="halted"))
        assert (
            len(terminal(s)) == 1
            and terminal(s)[0].detail == "A model call could not be recorded in the ledger."
        )
        errors = [e.payload for e in s.events() if e.type == "error" and e.payload.fatal]
        assert len(errors) == 1 and "Lead agent-1" in errors[0].message
        assert s.live._ledger_lost and not any(e.type == "usage.tick" for e in s.events())
        assert error_code(await s.client.post("/leads", json={"task": "Another"})) == (
            403,
            "spend_brake",
        )


def test_nothing_on_the_lead_path_calls_a_decision_or_queue_command() -> None:
    root = REPO_ROOT / "backend/src/oversight"
    forbidden = {
        "approve",
        "reject",
        "request_changes",
        "revert",
        "revoke_grant",
        "enqueue",
        "cancel_queued",
        "start_plan",
        "set_merge_mode",
        "save_file",
        "rescan",
        "dispatch_plan",
        "run_plan_call",
    }
    for relative in ("runtime_leads.py", "agents/_lead.py", "agents/_lead_tools.py"):
        tree = ast.parse((root / relative).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else node.func.id
                    if isinstance(node.func, ast.Name)
                    else None
                )
                assert name not in forbidden, (relative, node.lineno, name)
                assert not (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"start", "stop", "create", "update", "dispatch_plan"}
                ), (relative, node.lineno)


def test_no_source_registers_a_lead_for_revision_requests() -> None:
    root = REPO_ROOT / "backend/src/oversight"
    for path in root.rglob("*.py"):
        if path.relative_to(root).as_posix() != "changes/_evidence.py":
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.keyword):
                    assert node.arg != "registered_leads", path


async def test_stop_while_working_status_commits_prevents_the_first_model_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = ScriptedModel(load_script(LEAD_SCRIPT))
    entered, release = asyncio.Event(), asyncio.Event()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(two_fakes(tmp_path), lambda _p, _s: model) as s,
    ):
        original = s.store.publish

        async def delayed(payload: Any) -> Any:
            if isinstance(payload, LeadStatus) and payload.state == "working":
                entered.set()
                await release.wait()
            return await original(payload)

        monkeypatch.setattr(s.store, "publish", delayed)
        await s.client.post("/leads", json={"task": "Coordinate"})
        await entered.wait()
        response = await s.client.post("/leads/agent-1/stop", json={"revision": 1})
        assert response.status_code == 202
        release.set()
        await s.until(is_type("lead.status", state="stopped"))
        assert not model.requests and len(terminal(s)) == 1
        assert [e.payload.revision for e in s.events() if e.type == "lead.status"] == [1, 2, 3]


async def test_shutdown_waits_for_a_starting_status_before_halting_the_lead(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[Any] = []
    entered, release = asyncio.Event(), asyncio.Event()

    def factory(profile: Any, _slot: Any) -> Any:
        built.append(profile)
        return GatedModel(profile)

    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), factory) as s:
        original = s.store.publish

        async def delayed(payload: Any) -> Any:
            if isinstance(payload, LeadStatus) and payload.state == "starting":
                entered.set()
                await release.wait()
            return await original(payload)

        monkeypatch.setattr(s.store, "publish", delayed)
        starting = asyncio.create_task(s.client.post("/leads", json={"task": "Coordinate"}))
        await entered.wait()
        closing = asyncio.create_task(s.live.leads.close())
        await asyncio.sleep(0)
        release.set()
        assert (await starting).status_code == 201
        await closing
        assert not built and len(terminal(s)) == 1
        assert terminal(s)[0].detail == "The server shut down."
        assert [e.payload.revision for e in s.events() if e.type == "lead.status"] == [1, 2]
