"""Live thread admission, producer correlation, budgets and replay refusal."""

# Tests inspect owned run state; every model remains scripted, even with provider metadata.
# pyright: reportPrivateUsage=false

import asyncio
from pathlib import Path

import pytest
from backend.tests.agents.lead_helpers import call, plan, turn
from backend.tests.api.lead_helpers import ThreadModels, replies, send, start_idle
from fastapi.testclient import TestClient
from live_helpers import (
    DEADLINE_S,
    error_code,
    is_type,
    live_server,
    live_settings,
    meta_settings,
    two_fakes,
)

from oversight.agents import LedgerState
from oversight.app import ReplayConfig, create_app
from oversight_contracts.events import LeadStatus

pytestmark = pytest.mark.anyio


async def test_message_reply_order_correlation_masking_and_cumulative_steps(tmp_path: Path) -> None:
    factory = ThreadModels(
        [
            [turn(call("graph")), {"text": "Opening answer", "usage": {}}],
            [turn(call("status")), {"text": "Current answer", "usage": {}}],
            [{"text": "Without status", "usage": {}}],
        ]
    )
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), factory) as s:
        await start_idle(s)
        assert replies(s)[0].payload.answers_seq is None
        assert replies(s)[0].payload.through_seq is None
        after = await send(s, "Check Bearer private-secret")
        events = [e for e in s.events() if e.seq > after]
        assert events[0].type == "agent.message" and events[0].payload.text == "Check [redacted]"
        assert events[1].type == "lead.status" and events[1].payload.state == "working"
        answer = replies(s)[-1]
        assert answer.payload.answers_seq == events[0].seq
        assert events[1].seq <= answer.payload.through_seq < answer.seq
        assert "private-secret" not in str(factory.built[1].requests)
        after = await send(s)
        assert replies(s)[-1].payload.through_seq is None
        steps = [e.payload.step for e in s.events() if e.type == "lead.status"]
        assert steps == sorted(steps) and steps[-1] == 2
        thread = [
            (e.type, e.payload.agent_id)
            for e in s.events()
            if e.type in {"agent.message", "lead.reply"}
        ]
        assert thread == [
            ("lead.reply", "agent-1"),
            ("agent.message", "agent-1"),
            ("lead.reply", "agent-1"),
            ("agent.message", "agent-1"),
            ("lead.reply", "agent-1"),
        ]


@pytest.mark.parametrize(
    "body", [{"text": " "}, {"text": "x" * 4097}, {"text": 1}, {}, {"text": "ok", "extra": True}]
)
async def test_invalid_messages_are_not_stored(tmp_path: Path, body: object) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), ThreadModels()) as s:
        await start_idle(s)
        before = s.store.last_seq
        response = await s.client.post("/leads/agent-1/message", json=body)
        assert error_code(response) == (422, "invalid_request")
        assert before == s.store.last_seq
        assert error_code(await s.client.post("/leads/agent-999/message", json={"text": "ok"})) == (
            404,
            "not_found",
        )


async def test_admission_brake_refuses_without_message_or_model(tmp_path: Path) -> None:
    factory = ThreadModels()
    async with asyncio.timeout(DEADLINE_S), live_server(meta_settings(tmp_path), factory) as s:
        await start_idle(s)
        before = s.store.last_seq
        s.live.ledger.state = LedgerState(spent_usd=140)
        response = await s.client.post("/leads/agent-1/message", json={"text": "Next"})
        assert error_code(response) == (403, "spend_brake")
        assert s.store.last_seq == before and len(factory.built) == 1
        assert s.live.leads.leads["agent-1"].phase == "idle"


async def test_three_plans_across_turns_fourth_errors_then_answers(tmp_path: Path) -> None:
    factory = ThreadModels([[turn(call("request_plan", **plan())), {"text": "Ready", "usage": {}}]])
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), factory) as s:
        await start_idle(s)
        for _ in range(3):
            await send(s, "Another plan")
        assert len(s.live.plans.plans) == 3 and len(replies(s)) == 4
        assert "already requested 3 plans" in str(factory.built[-1].requests)
        assert all(r.payload.cut is None for r in replies(s))


async def test_50_turns_and_large_history_no_lifetime_cap(tmp_path: Path) -> None:
    factory = ThreadModels()
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), factory) as s:
        await start_idle(s)
        for i in range(48):
            await send(s, f"human-{i}:" + "h" * 4000)
        after = s.store.last_seq
        response = await s.client.post("/leads/agent-1/message", json={"text": "turn 50"})
        assert response.status_code == 202
        await s.until(is_type("lead.status", state="halted"), after)
        events = [e for e in s.events() if e.seq > after]
        assert len(replies(s)) == 50
        assert (
            events[-2].type == "lead.reply"
            and events[-1].payload.detail == "Reached the limit of 50 turns."
        )
        assert not any(e.type == "lead.status" and e.payload.state == "idle" for e in events)
        assert error_code(await s.client.post("/leads/agent-1/message", json={"text": "51"})) == (
            409,
            "wrong_state",
        )
        messages = factory.built[-1].requests[0]
        assert sum(len(str(m.get("content") or "")) for m in messages) <= 24_000
        assert messages[1]["content"] == "Retained opening task"
        assert "human-0:" not in str(messages) and "human-47:" in str(messages)
        assert all(m["role"] != "tool" for m in messages)
        assert (await s.client.post("/leads", json={"task": "replacement"})).status_code == 201


async def test_idle_stop_shutdown_and_terminal_preservation(tmp_path: Path) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), ThreadModels()) as s:
        await start_idle(s)
        owned = s.live.leads.leads["agent-1"]
        response = await s.client.post("/leads/agent-1/stop", json={"revision": owned.revision})
        assert response.status_code == 202
        assert owned.task is not None
        await owned.task
        stopped = owned.status
        assert stopped.state == "stopped"
        assert (await s.client.post("/leads", json={"task": "replacement"})).status_code == 201
        await s.until(is_type("lead.status", agent_id="agent-2", state="idle"))
        await s.live.leads.close()
        assert owned.status == stopped
        assert s.live.leads.leads["agent-2"].status.state == "halted"
        assert s.live.leads.leads["agent-2"].status.detail == "The server shut down."
        assert len(replies(s)) == 2


async def test_thread_replay_rejects_message_before_parsing_and_rebuilds_events(
    tmp_path: Path,
) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), ThreadModels()) as s:
        await start_idle(s)
        await send(s)
        log = tmp_path / "thread.jsonl"
        expected = [e.model_dump() for e in s.events() if e.type in {"agent.message", "lead.reply"}]
        # Historical done remains readable alongside the new live idle lifecycle.
        await s.store.publish(
            LeadStatus(
                agent_id="agent-2",
                revision=1,
                task="historical",
                model="fake",
                state="done",
                step=0,
                detail=None,
            )
        )
        log.write_text("\n".join(e.model_dump_json() for e in s.events()) + "\n")
    app = create_app(
        live_settings(tmp_path / "replay"), ReplayConfig(log=log, speed=0, allow_fake_provider=True)
    )
    with TestClient(app, base_url="http://127.0.0.1:8710") as client:
        assert error_code(client.post("/leads/agent-1/message", content=b"invalid")) == (
            501,
            "not_implemented",
        )

        async def completed():
            async for row in app.state.store.subscribe(0):
                if row.type == "lead.status" and '"done"' in row.envelope:
                    return

        assert client.portal is not None
        client.portal.call(completed)
        events = app.state.store.committed_events()
        assert [
            e.model_dump() for e in events if e.type in {"agent.message", "lead.reply"}
        ] == expected
        assert client.get("/leads/agent-2").json()["state"] == "done"
