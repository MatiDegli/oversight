"""Observable wake admission, human priority, horizon pruning and durable refusal races."""

# Admission tests inspect the lock and owned claims at controlled publication barriers.
# pyright: reportPrivateUsage=false

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from backend.tests.api.lead_helpers import replies, send
from backend.tests.api.wake_helpers import (
    ControlledClock,
    Models,
    caught_up,
    next_trigger,
    notices,
    owned,
    ready,
    spin,
    triggers,
    wake_done,
)
from backend.tests.events.test_lead_wake import plan, status, suite_execution
from live_helpers import DEADLINE_S, error_code, live_server, two_fakes

from oversight import events
from oversight.agents import MUSE_SPARK
from oversight.runtime_lead_admission import wake
from oversight_contracts.events import AgentMessageSent, LeadReply, LeadStatus

pytestmark = pytest.mark.anyio


async def test_watcher_uses_shared_catalog_and_isolates_leads(tmp_path: Path, monkeypatch):
    catalog = Mock(wraps=events.is_lead_wake_trigger)
    monkeypatch.setattr(events, "is_lead_wake_trigger", catalog)
    factory, clock = Models(), ControlledClock()
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), factory) as s:
        await ready(s, clock)
        await triggers(s, lead_id="agent-9")
        await s.store.publish(status(plan_id=None, state="halted").payload)
        await s.store.publish(suite_execution(phase="agent").payload)
        await s.store.publish(plan(4, state="planning", started_by="agent-1").payload)
        await s.store.publish(suite_execution(outcome="passed").payload)
        await caught_up(s)
        assert not owned(s).watcher.pending
        assert len(factory.built) == 1
        seqs = await triggers(s)
        assert list(owned(s).watcher.pending) == seqs
        await wake_done(s, clock)
        assert replies(s)[-1].payload.woken_by == seqs
        assert len(factory.built) == 2
        assert catalog.call_count >= s.store.last_seq - 1


async def test_first_trigger_sets_fixed_five_second_window(tmp_path: Path):
    clock = ControlledClock()
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), Models()) as s:
        await ready(s, clock)
        seqs = await triggers(s)
        watcher = owned(s).watcher
        assert watcher.deadline == 5
        clock.advance(4)
        seqs.append(await next_trigger(s))
        assert watcher.deadline == 5 and owned(s).wakes == 0
        clock.advance(1)
        await spin(lambda: owned(s).wakes == 1)
        await owned(s).task
        assert replies(s)[-1].payload.woken_by == seqs


async def test_running_turn_coalesces_then_reprunes_reply_horizon(tmp_path: Path, monkeypatch):
    factory, clock = Models(), ControlledClock()
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), factory) as s:
        await ready(s, clock)
        factory.release.clear()
        factory.entered.clear()
        assert (
            await s.client.post("/leads/agent-1/message", json={"text": "Observe"})
        ).status_code == 202
        await factory.entered.wait()
        seqs = await triggers(s)
        original = s.store.publish
        entered, release = asyncio.Event(), asyncio.Event()

        async def publish(payload: Any):
            if isinstance(payload, LeadReply):
                entered.set()
                await release.wait()
            return await original(payload)

        monkeypatch.setattr(s.store, "publish", publish)
        owned(s).run.tools.through_seq = seqs[-1]
        clock.advance(5)
        factory.release.set()
        await entered.wait()
        unseen = await next_trigger(s)
        assert owned(s).wakes == 0 and owned(s).through_seq is None
        release.set()
        await spin(lambda: owned(s).wakes == 1)
        await owned(s).task
        assert replies(s)[-1].payload.woken_by == [unseen]
        assert replies(s)[-2].payload.through_seq == seqs[-1]


async def test_human_launch_takes_pending_only_after_message_commit(tmp_path: Path, monkeypatch):
    factory, clock = Models(), ControlledClock()
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), factory) as s:
        await ready(s, clock)
        seqs = await triggers(s)
        original = s.store.publish
        entered, release = asyncio.Event(), asyncio.Event()

        async def publish(payload: Any):
            if isinstance(payload, AgentMessageSent):
                entered.set()
                await release.wait()
            return await original(payload)

        monkeypatch.setattr(s.store, "publish", publish)
        sending = asyncio.create_task(send(s, "Human instruction"))
        await entered.wait()
        clock.advance(5)
        assert list(owned(s).watcher.pending) == seqs and owned(s).phase == "working"
        release.set()
        await sending
        answer = replies(s)[-1].payload
        assert answer.answers_seq and answer.woken_by is None and answer.through_seq == seqs[-1]
        assert owned(s).wakes == 0 and not owned(s).watcher.pending
        assert any(m["role"] == "tool" and "Server notice:" in str(m) for m in factory.inputs[-1])
        assert factory.inputs[-1][-1] == {"role": "user", "content": "Human instruction"}


async def test_failed_human_publication_preserves_pending_triggers(tmp_path: Path, monkeypatch):
    clock = ControlledClock()
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), Models()) as s:
        await ready(s, clock)
        seqs = await triggers(s)
        original = s.store.publish

        async def publish(payload: Any):
            if isinstance(payload, AgentMessageSent):
                raise OSError("publication failed")
            return await original(payload)

        monkeypatch.setattr(s.store, "publish", publish)
        response = await s.client.post("/leads/agent-1/message", json={"text": "Human"})
        assert response.status_code == 500
        assert list(owned(s).watcher.pending) == seqs
        await wake_done(s, clock)
        assert replies(s)[-1].payload.woken_by == seqs
        assert not any(e.type == "agent.message" for e in s.events())


async def test_waiting_human_wins_before_wake_claim(tmp_path: Path):
    factory, clock = Models(), ControlledClock()
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), factory) as s:
        await ready(s, clock)
        seqs = await triggers(s)
        lead = owned(s)
        await lead.lock.acquire()
        clock.advance(5)
        await spin(lambda: lead.admitting is not None and not lead.admitting.done())
        sending = asyncio.create_task(s.live.leads.message("agent-1", "Human wins"))
        await spin(lambda: lead.humans_waiting == 1)
        lead.lock.release()
        await sending
        await lead.task
        assert lead.wakes == 0 and lead.humans_waiting == 0
        assert lead.run.history.notices[-1].triggers == tuple(seqs)
        assert replies(s)[-1].payload.answers_seq and replies(s)[-1].payload.woken_by is None
        assert len(factory.built) == 2


async def test_subscription_replay_and_human_sync_have_no_gap_or_duplicate(
    tmp_path: Path, monkeypatch
):
    from backend.tests.api.lead_helpers import start_idle

    from oversight.runtime_lead_wakes import LeadWatcher

    original = LeadWatcher._subscribe
    entered, release = asyncio.Event(), asyncio.Event()

    async def subscribe(watcher):
        entered.set()
        await release.wait()
        await original(watcher)

    monkeypatch.setattr(LeadWatcher, "_subscribe", subscribe)
    factory, clock = Models(), ControlledClock()
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), factory) as s:
        s.live.leads.wake_clock = clock
        await start_idle(s)
        await entered.wait()
        await s.store.publish(plan().payload)
        seq = (await s.store.publish(status(state="halted").payload)).seq
        await send(s, "Inspect what happened")
        notice = owned(s).run.history.notices[-1]
        assert notice is not None and notice.triggers == (seq,)
        release.set()
        await caught_up(s)
        clock.advance(5)
        assert not owned(s).watcher.pending and owned(s).wakes == 0
        assert len(replies(s)) == 2


async def test_send_during_wake_returns_409(tmp_path: Path):
    factory, clock = Models(), ControlledClock()
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), factory) as s:
        await ready(s, clock)
        factory.release.clear()
        factory.entered.clear()
        await triggers(s)
        clock.advance(5)
        await factory.entered.wait()
        response = await s.client.post("/leads/agent-1/message", json={"text": "Later"})
        assert error_code(response) == (409, "wrong_state")
        assert len(factory.built) == 2 and owned(s).wakes == 1
        assert not any(e.type == "agent.message" for e in s.events())
        factory.release.set()
        await owned(s).task


async def test_wake_counts_once_and_preserves_last_human_turn(tmp_path: Path):
    clock = ControlledClock()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(two_fakes(tmp_path, lead_max_turns=3), Models()) as s,
    ):
        await ready(s, clock)
        await triggers(s)
        await wake_done(s, clock)
        assert (owned(s).turns, owned(s).wakes) == (2, 1)
        await next_trigger(s)
        clock.advance(5)
        await spin(lambda: len(notices(s)) == 1)
        assert "last turn" in notices(s)[0].payload.detail
        assert (owned(s).turns, owned(s).wakes) == (2, 1)
        after = s.store.last_seq
        assert (
            await s.client.post("/leads/agent-1/message", json={"text": "Last"})
        ).status_code == 202
        await s.until(lambda e: e.type == "lead.status" and e.payload.state == "halted", after)
        await owned(s).task
        assert owned(s).turns == 3 and replies(s)[-1].payload.answers_seq


def refuse(s: Any, reason: str, monkeypatch: pytest.MonkeyPatch) -> None:
    service, lead = s.live.leads, owned(s)
    if reason == "brake":
        lead.profile = MUSE_SPARK  # The existing injected transport remains fake-only.
        monkeypatch.setattr(s.live, "brake_reason", lambda: "Brake")
    elif reason == "cap":
        service.policy = replace(service.policy, wakes=1)
        lead.wakes = 1
    else:
        lead.turns = service.policy.limits.turns - 1


@pytest.mark.parametrize("reason", ["brake", "cap", "last-turn"])
async def test_refusal_decision_and_publish_hold_idle_lock(tmp_path: Path, monkeypatch, reason):
    factory, clock = Models(), ControlledClock()
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), factory) as s:
        await ready(s, clock)
        await triggers(s)
        refuse(s, reason, monkeypatch)
        original = s.store.publish
        entered, release = asyncio.Event(), asyncio.Event()
        revision, state = owned(s).revision, owned(s).status.state
        counters = owned(s).turns, owned(s).wakes

        async def publish(payload: Any):
            if isinstance(payload, LeadStatus) and (payload.detail or "").startswith(
                "Skipped a wake: "
            ):
                entered.set()
                await release.wait()
            return await original(payload)

        monkeypatch.setattr(s.store, "publish", publish)
        clock.advance(5)
        await entered.wait()
        sending = asyncio.create_task(
            s.client.post("/leads/agent-1/message", json={"text": "Human"})
        )
        await spin(lambda: owned(s).humans_waiting == 1)
        assert (
            owned(s).lock.locked()
            and owned(s).phase == "idle"
            and owned(s).humans_waiting == 1
            and not sending.done()
        )
        assert len(factory.built) == 1 and not any(e.type == "agent.message" for e in s.events())
        assert owned(s).revision == revision
        assert (owned(s).turns, owned(s).wakes) == counters
        release.set()
        await spin(lambda: len(notices(s)) == 1)
        notice = notices(s)[0].payload
        assert notice.state == state and notice.revision == revision + 1
        assert notice.detail.startswith("Skipped a wake: ")
        assert (await sending).status_code == (403 if reason == "brake" else 202)


@pytest.mark.parametrize("action", ["stop", "close", "claim"])
async def test_refusal_rechecks_open_and_idle_under_lock(tmp_path: Path, monkeypatch, action):
    factory, clock = Models(), ControlledClock()
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), factory) as s:
        await ready(s, clock)
        await triggers(s)
        refuse(s, "cap", monkeypatch)
        lead = owned(s)
        await lead.lock.acquire()
        clock.advance(5)
        attempted = asyncio.create_task(wake(s.live.leads, lead))
        await asyncio.sleep(0)
        if action == "stop":
            lead.stopping = True
        elif action == "close":
            s.live.leads._closing = True
        else:
            lead.phase = "working"
        lead.lock.release()
        await attempted
        assert not notices(s) and len(factory.built) == 1 and lead.wakes == 1


async def test_refusal_once_per_reason_until_next_turn(tmp_path: Path, monkeypatch):
    clock = ControlledClock()
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), Models()) as s:
        await ready(s, clock)
        await triggers(s)
        refuse(s, "cap", monkeypatch)
        old_revision = owned(s).revision
        clock.advance(5)
        await spin(lambda: len(notices(s)) == 1)
        first_notice = notices(s)[0].payload
        assert first_notice.state == "idle"
        assert first_notice.detail == "Skipped a wake: the thread's wake limit was reached."
        assert first_notice.revision == old_revision + 1
        for _ in range(3):
            await next_trigger(s)
            await wake(s.live.leads, owned(s))
        assert len(notices(s)) == 1
        response = await s.client.post("/leads/agent-1/stop", json={"revision": old_revision})
        assert error_code(response) == (409, "wrong_state")
        await send(s)
        await next_trigger(s)
        clock.advance(5)
        await spin(lambda: len(notices(s)) == 2)
        second_notice = notices(s)[1].payload
        assert second_notice.state == "idle"
        assert second_notice.detail == "Skipped a wake: the thread's wake limit was reached."
        assert second_notice.revision > first_notice.revision


async def test_failed_refusal_publication_keeps_pending_and_retryable_notice(
    tmp_path: Path, monkeypatch
):
    clock = ControlledClock()
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), Models()) as s:
        await ready(s, clock)
        seqs = await triggers(s)
        refuse(s, "cap", monkeypatch)
        original = s.store.publish
        failed = asyncio.Event()

        async def publish(payload: Any):
            if isinstance(payload, LeadStatus) and (payload.detail or "").startswith(
                "Skipped a wake: "
            ):
                failed.set()
                raise OSError("refusal failed")
            return await original(payload)

        monkeypatch.setattr(s.store, "publish", publish)
        clock.advance(5)
        await failed.wait()
        await spin(lambda: owned(s).admitting.done())
        assert list(owned(s).watcher.pending) == seqs and not owned(s).refusals
        assert not notices(s)
        monkeypatch.setattr(s.store, "publish", original)
        await wake(s.live.leads, owned(s))
        assert len(notices(s)) == 1 and owned(s).refusals


@pytest.mark.parametrize("action", ["stop", "shutdown"])
@pytest.mark.parametrize("barrier", ["admission", "reply", "refusal"])
async def test_wake_stop_shutdown_races_preserve_terminal_ownership(
    tmp_path: Path, monkeypatch, action, barrier
):
    factory, clock = Models(), ControlledClock()
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), factory) as s:
        await ready(s, clock)
        await triggers(s)
        if barrier == "refusal":
            refuse(s, "cap", monkeypatch)
        original = s.store.publish
        entered, release = asyncio.Event(), asyncio.Event()

        async def publish(payload: Any):
            if (
                (barrier == "reply" and isinstance(payload, LeadReply))
                or (
                    barrier == "admission"
                    and isinstance(payload, LeadStatus)
                    and payload.state == "working"
                )
                or (
                    barrier == "refusal"
                    and isinstance(payload, LeadStatus)
                    and (payload.detail or "").startswith("Skipped a wake: ")
                )
            ):
                entered.set()
                await release.wait()
            return await original(payload)

        monkeypatch.setattr(s.store, "publish", publish)
        clock.advance(5)
        await entered.wait()
        if action == "stop":
            response = await s.client.post(
                "/leads/agent-1/stop", json={"revision": owned(s).revision}
            )
            assert response.status_code == 202
            closing = owned(s).task
        else:
            closing = asyncio.create_task(s.live.leads.close())
            await spin(lambda: s.live.leads._closing)
        release.set()
        await closing
        await asyncio.gather(*owned(s).tasks)
        assert owned(s).phase == ("stopped" if action == "stop" else "halted")
        assert len(factory.built) == (2 if barrier == "reply" else 1)
        assert owned(s).watching.done() and owned(s).watcher.subscription.done()
        endings = [e.payload.state for e in s.events() if e.type == "lead.status"]
        assert endings[-1] in {"halted", "stopped"}
