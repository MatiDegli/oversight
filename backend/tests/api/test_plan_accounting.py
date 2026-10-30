"""Planner receipts reach Meta's ledger before publication and drain through shutdown."""

# Tests inspect the live ledger and retained store after shutdown.
# pyright: reportPrivateUsage=false

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from live_helpers import (
    DEADLINE_S,
    error_code,
    live_server,
    live_settings,
    meta_settings,
)

from oversight.agents import MUSE_SPARK, LedgerError, Usage, _plan_call
from oversight.app import LiveRun
from oversight_contracts.commands import CreatePlan
from oversight_contracts.events import UsageTick

from .test_plan_call import TASK, ClosableGatedModel, FailingCloseModel, PlanModel, create

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("known", [False, True])
async def test_ledger_precedes_tick_and_unknown_cost_stays_null(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    known: bool,
) -> None:
    models: list[PlanModel] = []

    def factory(profile: Any, _slot: Any) -> PlanModel:
        assert profile == MUSE_SPARK and profile.reasoning == "low"
        model = PlanModel(profile, usage=Usage(100, 200, 0) if known else Usage())
        models.append(model)
        return model

    async with asyncio.timeout(DEADLINE_S), live_server(meta_settings(tmp_path), factory) as s:
        order: list[str] = []
        record, publish = s.live.ledger.record, s.store.publish

        async def ledger(*args: Any) -> Any:
            result = await record(*args)
            order.append("ledger")
            return result

        async def event(payload: Any) -> Any:
            if isinstance(payload, UsageTick):
                assert order == ["ledger"]
                state = s.live.ledger.read()
                assert state.spent_usd == payload.spent_usd
                assert bool(state.unknown) is (not known)
                order.append("tick")
            return await publish(payload)

        monkeypatch.setattr(s.live.ledger, "record", ledger)
        monkeypatch.setattr(s.store, "publish", event)
        plan = await create(s)
        assert order == ["ledger", "tick"] and len(models[0].calls) == 1
        [tick] = [e.payload for e in s.events() if e.type == "usage.tick"]
        assert tick.role == "plan" and tick.plan_id == plan.plan_id and tick.change_id is None
        assert tick.cost_usd == (pytest.approx(0.00005) if known else None)
        assert (s.live.brake_reason() is None) is known
        assert not any(e.type == "agent.status" for e in s.events())
        if not known:
            response = await s.client.post("/plans", json={"task": TASK})
            assert error_code(response) == (403, "spend_brake")
            assert len(models) == 1


@pytest.mark.parametrize("failure", [LedgerError, OSError])
@pytest.mark.parametrize("close_fails", [False, True])
async def test_failed_ledger_closes_meta_admission_and_preserves_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: type[Exception],
    close_fails: bool,
) -> None:
    models: list[PlanModel] = []

    def factory(profile: Any, _slot: Any) -> PlanModel:
        model_type = FailingCloseModel if close_fails else PlanModel
        model = model_type(profile, usage=Usage(100, 200, 0))
        models.append(model)
        return model

    async with asyncio.timeout(DEADLINE_S), live_server(meta_settings(tmp_path), factory) as s:

        async def failed(*args: Any) -> Any:
            raise failure("injected")

        monkeypatch.setattr(s.live.ledger, "record", failed)
        plan = await create(s)
        assert plan.subtasks and plan.subtasks[0].task == TASK
        assert plan.detail and "LedgerError" in plan.detail
        assert s.live._ledger_lost and s.live.brake_reason() is not None
        assert not any(e.type == "usage.tick" for e in s.events())
        [error] = [e.payload for e in s.events() if e.type == "error"]
        assert error.fatal and error.agent_id == "agent-1" and error.change_id is None
        assert plan.plan_id in error.message and "model=" + MUSE_SPARK.model in error.message
        assert "provider=meta" in error.message and "Stop the server" in error.message
        assert "settle with Meta's console total before the next run" in error.message
        assert "uv run python -m oversight ledger settle --spent-usd X" in error.message
        for path in ("/plans", "/agents"):
            assert error_code(await s.client.post(path, json={"task": TASK})) == (
                403,
                "spend_brake",
            )
        assert len(models) == 1 and not s.live._ledger_lost_agents
        if close_fails:
            assert isinstance(models[0], FailingCloseModel) and models[0].close_attempts == 1
        else:
            assert models[0].closed


async def test_timeout_proposes_verbatim_subtask_through_the_http_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    held = ClosableGatedModel()
    limits: list[asyncio.Timeout] = []

    def timeout_at(deadline: float) -> asyncio.Timeout:
        assert 89 < deadline - asyncio.get_running_loop().time() <= 90
        limit = asyncio.timeout_at(deadline)
        limits.append(limit)
        return limit

    monkeypatch.setattr(
        _plan_call,
        "asyncio",
        SimpleNamespace(
            **vars(asyncio)
            | {
                "timeout_at": timeout_at,
            }
        ),
    )
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: held) as s,
    ):
        pending = asyncio.create_task(create(s))
        await held.entered.wait()
        limits[0].reschedule(asyncio.get_running_loop().time())
        plan = await pending
        assert plan.subtasks and plan.subtasks[0].task == TASK
        assert plan.detail and "90-second" in plan.detail and held.cancelled.is_set()
        assert held.closed
        assert not any(e.type == "queue.updated" for e in s.events())


async def test_shutdown_cancels_held_call_and_lists_the_planning_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    held = ClosableGatedModel()
    live = LiveRun(live_settings(tmp_path), lambda _p, _s: held)
    closed_before_global: list[bool] = []
    original_close = live.plans.close

    async def close_plans() -> None:
        await original_close()
        closed_before_global.append(held.closed)

    monkeypatch.setattr(live.plans, "close", close_plans)
    async with asyncio.timeout(DEADLINE_S):
        await live.startup()
        pending = asyncio.create_task(live.create_plan(CreatePlan(task=TASK)))
        await held.entered.wait()
        try:
            await live.shutdown()
            result = await asyncio.gather(pending, return_exceptions=True)
            assert isinstance(result[0], asyncio.CancelledError) and held.cancelled.is_set()
            assert closed_before_global == [True]
            assert live.plans.get("plan-1").state == "planning"
            events = [json.loads(event.envelope) for event in live.store._events]
            assert any(e["type"] == "error" and "plan-1" in e["payload"]["message"] for e in events)
            assert not any(e["type"] == "queue.updated" for e in events)
        finally:
            if not live._closing:
                await live.shutdown()


async def test_shutdown_drains_ledger_and_tick_without_proposing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    live = LiveRun(meta_settings(tmp_path), lambda p, _s: PlanModel(p, usage=Usage(100, 200, 0)))
    async with asyncio.timeout(DEADLINE_S):
        await live.startup()
        original = live.ledger.record

        async def held_record(*args: Any) -> Any:
            entered.set()
            await release.wait()
            return await original(*args)

        monkeypatch.setattr(live.ledger, "record", held_record)
        pending = asyncio.create_task(live.create_plan(CreatePlan(task=TASK)))
        await entered.wait()
        closing = asyncio.create_task(live.shutdown())
        try:
            await asyncio.sleep(0)
            assert not closing.done()
        finally:
            release.set()
            await closing
            await asyncio.gather(pending, return_exceptions=True)
        assert live.plans.get("plan-1").state == "planning"
        assert live.ledger.read().spent_usd == pytest.approx(0.00005)
        events = [json.loads(event.envelope) for event in live.store._events]
        [tick] = [e["payload"] for e in events if e["type"] == "usage.tick"]
        assert tick["role"] == "plan" and tick["plan_id"] == "plan-1"
        assert any(e["type"] == "error" and "plan-1" in e["payload"]["message"] for e in events)


@pytest.mark.parametrize("failure", [LedgerError, OSError])
async def test_shutdown_reports_held_ledger_failure_before_client_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: type[Exception]
) -> None:
    entered, release, plans_closing = asyncio.Event(), asyncio.Event(), asyncio.Event()
    model = FailingCloseModel(MUSE_SPARK, usage=Usage(100, 200, 0))
    live = LiveRun(meta_settings(tmp_path), lambda _p, _s: model)
    original_close = live.plans.close

    async def close_plans() -> None:
        plans_closing.set()
        await original_close()
        assert model.close_attempts == 1  # Before TaskClients retries the failed close.

    async def held_record(*args: Any) -> Any:
        entered.set()
        await release.wait()
        raise failure("injected held ledger failure")

    close_model = model.aclose

    async def checked_close() -> None:
        # The fatal receipt must already be committed, even when this close then fails.
        events = [json.loads(event.envelope) for event in live.store._events]
        assert any(
            e["type"] == "error" and e["payload"]["fatal"] and "settle" in e["payload"]["message"]
            for e in events
        )
        await close_model()

    monkeypatch.setattr(live.plans, "close", close_plans)
    monkeypatch.setattr(live.ledger, "record", held_record)
    monkeypatch.setattr(model, "aclose", checked_close)
    async with asyncio.timeout(DEADLINE_S):
        await live.startup()
        pending = asyncio.create_task(live.create_plan(CreatePlan(task=TASK)))
        await entered.wait()
        closing = asyncio.create_task(live.shutdown())
        try:
            await plans_closing.wait()  # _create has been cancelled while its receipt drains.
            assert not closing.done()
        finally:
            release.set()
            await closing
            result = await asyncio.gather(pending, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
        assert live._ledger_lost and live.brake_reason() is not None
        assert live.plans.get("plan-1").state == "planning"
        assert live.ledger.read().spent_usd == 0
        events = [json.loads(event.envelope) for event in live.store._events]
        [error] = [e["payload"] for e in events if e["type"] == "error" and e["payload"]["fatal"]]
        assert error["agent_id"] == "agent-1" and "plan-1" in error["message"]
        assert "Stop the server and settle with Meta's console total" in error["message"]
        assert "uv run python -m oversight ledger settle --spent-usd X" in error["message"]
        assert not any(e["type"] == "usage.tick" for e in events)
