"""Shared Meta spending refuses each lead independently; call receipts retain their owner."""

# Fake transports carry provider metadata solely to exercise durable accounting and brakes.
# pyright: reportPrivateUsage=false

import asyncio
from dataclasses import replace
from pathlib import Path
from types import MethodType
from typing import Any

import pytest
from backend.tests.agents.lead_helpers import call, turn
from backend.tests.api.lead_helpers import ThreadModels, idle, start_idle
from backend.tests.api.test_lead_concurrency import HeldScript
from live_helpers import DEADLINE_S, error_code, live_server, meta_settings

from oversight.agents import LedgerError, LedgerState
from oversight.agents.client import CALL_SCOPE
from oversight.runtime_accounting import PlanAccounting
from oversight_contracts.events import UsageTick

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("failure", ["spent", "unknown", "ledger"])
async def test_one_receipt_blocks_the_other_leads_next_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    scripts = ThreadModels(
        [
            [
                {
                    "text": "spender",
                    "usage": {}
                    if failure == "unknown"
                    else {"input_tokens": 1, "output_tokens": 700_000_000, "cached_tokens": 0},
                }
            ],
            [turn(call("status")), {"text": "must never be called"}],
        ]
    )
    built: asyncio.Queue[HeldScript] = asyncio.Queue()
    models = []

    def factory(profile: Any, slot: Any) -> HeldScript:
        scope = CALL_SCOPE.get()
        assert scope is not None and scope.role == "lead"
        assert isinstance(scope.record, MethodType)
        assert isinstance(scope.record.__self__, PlanAccounting)
        owner = scope.record.__self__.agent_id
        assert owner == f"agent-{len(models) + 1}"
        model = HeldScript(scripts(profile, slot))
        models.append(model)
        built.put_nowait(model)
        return model

    async with asyncio.timeout(DEADLINE_S), live_server(meta_settings(tmp_path), factory) as s:
        original_record, original_publish = s.live.ledger.record, s.store.publish
        recorded = []

        async def record(cost: Any, entry: Any) -> Any:
            if failure == "ledger" and entry.agent_id == "agent-1":
                raise LedgerError("injected lead write failure")
            state = await original_record(cost, entry)
            recorded.append(entry.agent_id)
            return state

        async def publish(payload: Any) -> Any:
            if isinstance(payload, UsageTick):
                if payload.agent_id != "agent-3":
                    assert payload.agent_id in recorded  # Ledger commit precedes its tick.
                assert payload.role == "lead" and payload.plan_id is payload.change_id is None
            return await original_publish(payload)

        monkeypatch.setattr(s.live.ledger, "record", record)
        monkeypatch.setattr(s.store, "publish", publish)
        for _ in range(2):
            assert (await s.client.post("/leads", json={"task": "task"})).status_code == 201
            await (await built.get()).entered.wait()
        models[0].release.set()
        lead = s.live.leads.leads["agent-1"]
        assert lead.task is not None
        await lead.task
        assert s.live.brake_reason() is not None
        models[1].release.set()
        await idle(s, agent_id="agent-2")
        reply = [
            e.payload
            for e in s.events()
            if e.type == "lead.reply" and e.payload.agent_id == "agent-2"
        ][-1]
        assert reply.cut == "spend_brake" and len(models[1].requests) == 1
        before = s.store.last_seq, len(models)
        assert error_code(
            await s.client.post("/leads/agent-2/message", json={"text": "again"})
        ) == (403, "spend_brake")
        assert (s.store.last_seq, len(models)) == before
        ticks = [e.payload for e in s.events() if e.type == "usage.tick"]
        assert {t.agent_id for t in ticks} == (
            {"agent-2"} if failure == "ledger" else {"agent-1", "agent-2"}
        )
        # Baseten bypasses the Meta brake and does not append to its spend ledger.
        s.live._settings = replace(s.live._settings, baseten_api_key="test-key")
        response = await s.client.post(
            "/leads", json={"task": "Baseten", "model": "muse-spark-open"}
        )
        assert response.status_code == 201
        model = await built.get()
        model.release.set()
        await idle(s, agent_id="agent-3")
        assert recorded.count("agent-3") == 0


async def test_setup_and_selected_brake_precede_full_capacity(tmp_path: Path) -> None:
    factory = ThreadModels()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(meta_settings(tmp_path, max_leads=1), factory) as s,
    ):
        await start_idle(s)
        s.live.ledger.state = LedgerState(spent_usd=140)
        before = s.store.last_seq, s.live._count, len(factory.built)
        s.live._settings = replace(s.live._settings, meta_api_key=None)
        assert error_code(await s.client.post("/leads", json={"task": "full"})) == (
            503,
            "not_configured",
        )
        s.live._settings = replace(s.live._settings, meta_api_key="test-key")
        assert error_code(await s.client.post("/leads", json={"task": "full"})) == (
            403,
            "spend_brake",
        )
        assert (s.store.last_seq, s.live._count, len(factory.built)) == before
