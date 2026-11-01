"""Deterministic barriers exercise lead claims, publication rollback and terminal ownership."""

# Race tests deliberately inspect owned tasks and claim state.
# pyright: reportPrivateUsage=false

import asyncio
from pathlib import Path
from typing import Any

import pytest
from backend.tests.api.lead_helpers import ThreadModels, idle, replies, send, start_idle
from live_helpers import DEADLINE_S, error_code, is_type, live_server, two_fakes

from oversight_contracts.events import AgentMessageSent, LeadStatus

pytestmark = pytest.mark.anyio


async def test_two_concurrent_messages_have_exactly_one_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    factory = ThreadModels()
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), factory) as s:
        await start_idle(s)
        original = s.store.publish

        async def publish(payload: Any) -> Any:
            if isinstance(payload, AgentMessageSent):
                entered.set()
                await release.wait()
            return await original(payload)

        monkeypatch.setattr(s.store, "publish", publish)
        after = s.store.last_seq
        first = asyncio.create_task(s.client.post("/leads/agent-1/message", json={"text": "first"}))
        await entered.wait()
        second = await s.client.post("/leads/agent-1/message", json={"text": "second"})
        assert error_code(second) == (409, "wrong_state")
        assert s.live.leads.leads["agent-1"].phase == "working"
        assert len(factory.built) == 1
        release.set()
        assert (await first).status_code == 202
        await idle(s, after)
        assert len([e for e in s.events() if e.type == "agent.message"]) == 1
        assert len(replies(s)) == 2


@pytest.mark.parametrize("action", ["stop", "shutdown"])
@pytest.mark.parametrize("barrier", ["before_message", "after_message", "working"])
async def test_terminal_claim_prevents_working_or_model_after_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str, barrier: str
) -> None:
    entered, release, closing_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    factory = ThreadModels()
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), factory) as s:
        await start_idle(s)
        original = s.store.publish

        async def publish(payload: Any) -> Any:
            message_gate = isinstance(payload, AgentMessageSent) and barrier != "working"
            working_gate = (
                isinstance(payload, LeadStatus)
                and payload.state == "working"
                and barrier == "working"
            )
            if message_gate and barrier == "after_message":
                result = await original(payload)
                entered.set()
                await release.wait()
                return result
            if message_gate or working_gate:
                entered.set()
                await release.wait()
            return await original(payload)

        monkeypatch.setattr(s.store, "publish", publish)
        after = s.store.last_seq
        sending = asyncio.create_task(
            s.client.post("/leads/agent-1/message", json={"text": "next"})
        )
        await entered.wait()
        owned = s.live.leads.leads["agent-1"]
        assert owned.admitting is not None and not owned.admitting.done()
        closing = None
        if action == "stop":
            response = await s.client.post("/leads/agent-1/stop", json={"revision": owned.revision})
            assert response.status_code == 202
        else:

            async def close():
                closing_entered.set()
                await s.live.leads.close()

            closing = asyncio.create_task(close())
            await closing_entered.wait()
            assert not closing.done()
        release.set()
        assert (await sending).status_code == 202
        state = "stopped" if action == "stop" else "halted"
        await s.until(is_type("lead.status", state=state), after)
        if closing:
            await closing
        else:
            assert owned.task is not None
            await owned.task
        assert len(factory.built) == 1 and len(replies(s)) == 1
        events = [e for e in s.events() if e.seq > after]
        assert len([e for e in events if e.type == "agent.message"]) == 1
        statuses = [e.payload for e in events if e.type == "lead.status"]
        assert [x.state for x in statuses] == (
            ["working", state] if barrier == "working" else [state]
        )
        assert owned.status.detail == (
            "Stopped by the person." if action == "stop" else "The server shut down."
        )


@pytest.mark.parametrize("failure", ["message", "after_message", "working"])
async def test_failed_publication_rolls_back_claim_and_preserves_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    factory = ThreadModels()
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), factory) as s:
        await start_idle(s)
        original = s.store.publish
        failed = False

        async def publish(payload: Any) -> Any:
            nonlocal failed
            target = (
                isinstance(payload, AgentMessageSent)
                if failure != "working"
                else isinstance(payload, LeadStatus) and payload.state == "working"
            )
            if target and not failed:
                failed = True
                if failure == "after_message":
                    await original(payload)
                raise RuntimeError("publication failed")
            return await original(payload)

        monkeypatch.setattr(s.store, "publish", publish)
        before = s.store.last_seq
        response = await s.client.post("/leads/agent-1/message", json={"text": "failed turn"})
        assert error_code(response) == (500, "internal")
        owned = s.live.leads.leads["agent-1"]
        assert owned.phase == "idle" and len(factory.built) == 1
        assert s.store.last_seq == before + (failure != "message")
        await send(s, "accepted after rollback")
        assert len(replies(s)) == 2


@pytest.mark.parametrize("action", ["stop", "shutdown"])
async def test_terminal_wins_before_message_claim(tmp_path: Path, action: str) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), ThreadModels()) as s:
        await start_idle(s)
        owned = s.live.leads.leads["agent-1"]
        if action == "stop":
            assert (
                await s.client.post("/leads/agent-1/stop", json={"revision": owned.revision})
            ).status_code == 202
        else:
            await s.live.leads.close()
        before = s.store.last_seq
        response = await s.client.post("/leads/agent-1/message", json={"text": "refused"})
        assert error_code(response) == (409, "wrong_state")
        assert not any(e.type == "agent.message" for e in s.events() if e.seq > before)


async def test_caller_cancellation_does_not_abandon_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), ThreadModels()) as s:
        await start_idle(s)
        original = s.store.publish

        async def publish(payload: Any) -> Any:
            if isinstance(payload, AgentMessageSent):
                entered.set()
                await release.wait()
            return await original(payload)

        monkeypatch.setattr(s.store, "publish", publish)
        after = s.store.last_seq
        caller = asyncio.create_task(s.live.leads.message("agent-1", "durable"))
        await entered.wait()
        caller.cancel()
        release.set()
        await caller
        await idle(s, after)
        assert len(replies(s)) == 2


async def test_shutdown_waits_for_clients_from_preceding_idle_turns(tmp_path: Path) -> None:
    from oversight.agents import ScriptedModel, parse_script

    cleaning, release, closing_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    models = []

    class Client(ScriptedModel):
        closed = False

        async def aclose(self):
            if models[0] is self:
                cleaning.set()
                await release.wait()
            self.closed = True

    def factory(profile, _slot):
        model = Client(
            parse_script('{"task":"task","turns":[{"text":"Answer"}]}', profile), profile
        )
        models.append(model)
        return model

    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), factory) as s:
        await s.client.post("/leads", json={"task": "task"})
        await cleaning.wait()
        await send(s)
        assert len(models) == 2 and models[1].closed

        async def close():
            closing_entered.set()
            await s.live.leads.close()

        closing = asyncio.create_task(close())
        await closing_entered.wait()
        assert not closing.done()
        release.set()
        await closing
        assert all(model.closed for model in models)
        statuses = [e for e in s.events() if e.type == "lead.status"]
        await s.live.leads.close()
        assert [e for e in s.events() if e.type == "lead.status"] == statuses


async def test_message_during_reply_finalization_is_refused_without_waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oversight_contracts.events import LeadReply

    entered, release = asyncio.Event(), asyncio.Event()
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), ThreadModels()) as s:
        original = s.store.publish

        async def publish(payload: Any) -> Any:
            if isinstance(payload, LeadReply):
                entered.set()
                await release.wait()
            return await original(payload)

        monkeypatch.setattr(s.store, "publish", publish)
        await s.client.post("/leads", json={"task": "task"})
        await entered.wait()
        response = await s.client.post("/leads/agent-1/message", json={"text": "too soon"})
        assert error_code(response) == (409, "wrong_state")
        release.set()
        await idle(s)
        assert len(replies(s)) == 1


@pytest.mark.parametrize("action", ["stop", "shutdown"])
async def test_failed_admission_cannot_roll_back_a_terminal_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    entered, release, closing_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), ThreadModels()) as s:
        await start_idle(s)
        original = s.store.publish

        async def publish(payload: Any) -> Any:
            if isinstance(payload, AgentMessageSent):
                entered.set()
                await release.wait()
                raise RuntimeError("failed during terminal claim")
            return await original(payload)

        monkeypatch.setattr(s.store, "publish", publish)
        sending = asyncio.create_task(
            s.client.post("/leads/agent-1/message", json={"text": "next"})
        )
        await entered.wait()
        owned = s.live.leads.leads["agent-1"]
        if action == "stop":
            await s.client.post("/leads/agent-1/stop", json={"revision": owned.revision})
            closing = owned.task
        else:

            async def close():
                closing_entered.set()
                await s.live.leads.close()

            closing = asyncio.create_task(close())
            await closing_entered.wait()
        release.set()
        assert error_code(await sending) == (500, "internal")
        assert closing is not None
        await closing
        assert owned.phase == ("stopped" if action == "stop" else "halted")
        assert not any(e.type == "agent.message" for e in s.events())
