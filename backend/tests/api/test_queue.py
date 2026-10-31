"""Standalone FIFO dispatch: real HTTP/git/store, scripted clients, no provider calls."""

# Tests inspect the composition root's owned state to prove slot/lock invariants.
# pyright: reportPrivateUsage=false

import asyncio
from pathlib import Path
from typing import Any

import pytest
from decision_helpers import observed_body
from live_helpers import (
    DEADLINE_S,
    FAKE_SCRIPT,
    GatedModel,
    error_code,
    is_type,
    live_server,
    live_settings,
    meta_settings,
    two_fakes,
)
from revise_helpers import ready, request, scripted_a

from oversight.agents import LedgerState
from oversight.api import ApiError
from oversight.changes import ChangeManager, queue_transition, queued, read_queue
from oversight_contracts.commands import EnqueueTask, ErrorCode
from oversight_contracts.events import AgentStatus, ChangeResolved
from oversight_contracts.queue import QueuedTask

pytestmark = pytest.mark.anyio


def body(**overrides: Any) -> dict[str, Any]:
    return {
        "agent": {"task": "Document the coupon.", "slot": None, "model": None, "zone": None}
        | overrides
    }


async def enqueue(s: Any, **overrides: Any) -> QueuedTask:
    response = await s.client.post("/queue", json=body(**overrides))
    assert response.status_code == 201, response.text
    return QueuedTask.model_validate(response.json())


async def state(s: Any, identifier: str, value: str) -> None:
    events = await s.until(
        lambda e: (
            is_type("queue.updated", queued_task_id=identifier, state=value)(e) or e.type == "error"
        )
    )
    assert events[-1].type == "queue.updated", events[-1]


async def folded(s: Any) -> None:
    async with s.live.dispatcher.queue_lock:
        fold: dict[str, QueuedTask] = {}
        for event in s.events():  # validates every envelope against contracts 1.8.0
            if event.type != "queue.updated":
                continue
            item = event.payload
            previous = fold.get(item.queued_task_id)
            if previous is None:
                assert item == queued(EnqueueTask(agent=item.agent), len(fold) + 1)
            else:
                assert item == queue_transition(
                    previous,
                    item.state,
                    detail=item.detail,
                    agent_id=item.agent_ids[0] if item.state == "running" else None,
                )
                assert item.agent_ids[: len(previous.agent_ids)] == previous.agent_ids
            assert item.started_by is None and item.state != "starting"
            fold[item.queued_task_id] = item
        assert fold == s.live.dispatcher.items
        assert read_queue(s.store).tasks == list(fold.values())
        assert not s.live.dispatcher.launching


async def idle(s: Any) -> None:
    """Wait for a real selection pass to find no dispatchable work."""
    done = asyncio.Event()
    dispatcher = s.live.dispatcher
    original = dispatcher._select

    async def select() -> Any:
        result = await original()
        if result is None:
            done.set()
        return result

    dispatcher._select = select
    try:
        dispatcher.wake.set()
        await done.wait()
    finally:
        dispatcher._select = original


async def test_sixteen_concurrent_items_use_four_slots_and_release_in_fifo(tmp_path: Path) -> None:
    settings = live_settings(
        tmp_path,
        max_agents=4,
        fake_script_b=FAKE_SCRIPT,
        fake_script_c=FAKE_SCRIPT,
        fake_script_d=FAKE_SCRIPT,
    )
    async with asyncio.timeout(DEADLINE_S), live_server(settings, lambda _p, _s: GatedModel()) as s:
        items = await asyncio.gather(*(enqueue(s, task=f"task {i}") for i in range(16)))
        assert len({item.queued_task_id for item in items}) == 16
        await state(s, "queued-task-4", "running")
        snapshot = read_queue(s.store)
        assert [item.state for item in snapshot.tasks] == ["running"] * 4 + ["queued"] * 12
        assert set(s.live._slots.values()) == {f"agent-{i}" for i in range(1, 5)}
        # Race a release with another enqueue: neither can overtake the twelve waiting items.
        stopped, extra = await asyncio.gather(
            s.client.post("/agents/agent-1/stop"), enqueue(s, task="last")
        )
        assert stopped.status_code == 202 and extra.queued_task_id == "queued-task-17"
        await state(s, "queued-task-5", "running")
        assert s.live.dispatcher.items["queued-task-1"].state == "completed"
        assert s.live.dispatcher.items["queued-task-6"].state == "queued"
        assert s.live.dispatcher.items["queued-task-17"].state == "queued"
        statuses = [e.payload for e in s.events() if e.type == "agent.status"]
        assert statuses and all(status.queued_task_id is not None for status in statuses)
        await folded(s)


@pytest.mark.parametrize("decision", ["approve", "reject", "stop"])
async def test_decisions_complete_under_queue_lock_and_start_exactly_one_next(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, decision: str
) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        released = asyncio.Event()
        original = s.live.dispatcher.resolved

        def release(agent_id: str, outcome: Any) -> None:
            original(agent_id, outcome)
            released.set()

        monkeypatch.setattr(s.live.dispatcher, "resolved", release)
        first = await enqueue(s)
        await state(s, first.queued_task_id, "running")
        await ready(s)
        await enqueue(s)
        await enqueue(s)
        async with s.live.dispatcher.queue_lock:
            if decision == "stop":
                response = await s.client.post("/agents/agent-1/stop")
            else:
                response = await s.client.post(
                    f"/changes/change-1/{decision}", json=observed_body(s)
                )
            assert response.status_code == 202
            await released.wait()
            assert s.live._slots["A"] is None
        await state(s, "queued-task-2", "running")
        assert s.live.dispatcher.items["queued-task-3"].state == "queued"
        await folded(s)


async def test_ready_revising_halted_and_conflict_keep_their_slot(tmp_path: Path) -> None:
    model = scripted_a()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: model) as s,
    ):
        await enqueue(s)
        await state(s, "queued-task-1", "running")
        await ready(s)
        await enqueue(s)
        seq = s.store.last_seq
        assert (await request(s)).status_code == 202
        await ready(s, after=seq)
        latest = next(e.payload for e in reversed(s.events()) if e.type == "agent.status")
        assert isinstance(latest, AgentStatus)
        await s.live._publish(latest.model_copy(update={"state": "halted", "detail": "held"}))
        await s.live._publish(
            ChangeResolved.model_validate(
                {
                    "change_id": "change-1",
                    "agent_id": "agent-1",
                    "revision": 2,
                    "decision": "approved",
                    "decided_by": "person",
                    "head_commit": "observed",
                    "reason": None,
                    "comment": None,
                    "merge": "conflict",
                    "merge_commit": None,
                    "conflict": {"kind": "merge", "paths": ["shop/pricing.py"], "tests": None},
                }
            )
        )
        s.live.dispatcher.wake.set()
        async with s.live.dispatcher.queue_lock:
            assert s.live._slots["A"] == "agent-1"
            assert s.live.dispatcher.items["queued-task-2"].state == "queued"
        await folded(s)


async def test_named_head_waits_despite_another_free_slot_and_cancel_never_starts(
    tmp_path: Path,
) -> None:
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(two_fakes(tmp_path), lambda _p, _s: GatedModel()) as s,
    ):
        assert (
            await s.client.post("/agents", json={"task": "hold A", "slot": "A"})
        ).status_code == 201
        first = await enqueue(s, slot="A")
        second = await enqueue(s, slot="B")
        await idle(s)
        assert s.live._slots["B"] is None
        path = f"/queue/{first.queued_task_id}/cancel"
        assert error_code(await s.client.post(path, json={"revision": 2})) == (409, "wrong_state")
        assert (await s.client.post(path, json={"revision": 1})).status_code == 202
        await state(s, second.queued_task_id, "running")
        assert error_code(await s.client.post(path, json={"revision": 2})) == (409, "wrong_state")
        assert not s.live.dispatcher.items[first.queued_task_id].agent_ids
        assert (await s.client.get("/queue/absent")).status_code == 404
        await folded(s)


@pytest.mark.parametrize("code", ["spend_brake", "not_configured", "wrong_state"])
async def test_temporary_refusals_block_then_requeue_without_agents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: ErrorCode
) -> None:
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: GatedModel()) as s,
    ):
        original = s.live._check_start

        def refused(*_args: Any) -> Any:
            raise ApiError(code, "main suspect" if code == "wrong_state" else "braked")

        monkeypatch.setattr(s.live, "_check_start", refused)
        item = await enqueue(s)
        await state(s, item.queued_task_id, "blocked")
        assert not s.live.dispatcher.items[item.queued_task_id].agent_ids
        monkeypatch.setattr(s.live, "_check_start", original)
        s.live.dispatcher.wake.set()
        await state(s, item.queued_task_id, "running")
        assert [e.payload.state for e in s.events() if e.type == "queue.updated"] == [
            "queued",
            "blocked",
            "queued",
            "running",
        ]
        await folded(s)


@pytest.mark.parametrize(
    "refusal", [None, "slot_busy", "spend_brake", "invalid_request", "internal"]
)
async def test_cancel_during_held_launch_and_after_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, refusal: ErrorCode | None
) -> None:
    entered, gate, settled = asyncio.Event(), asyncio.Event(), asyncio.Event()
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
        original_refused = s.live.dispatcher._refused

        async def observe(*args: Any, **kwargs: Any) -> None:
            await original_refused(*args, **kwargs)
            settled.set()

        monkeypatch.setattr(s.live.dispatcher, "_refused", observe)
        item = await enqueue(s)
        await entered.wait()
        try:
            assert s.live.dispatcher.launching == {item.queued_task_id}
            assert read_queue(s.store).tasks[0].state == "queued"
            path = f"/queue/{item.queued_task_id}/cancel"
            response = await s.client.post(path, json={"revision": 1})
            assert error_code(response) == (409, "wrong_state")
            original = s.live._check_start
            if refusal is not None:

                def still_refused(*_args: Any) -> Any:
                    raise ApiError(refusal, "refused launch")

                monkeypatch.setattr(s.live, "_check_start", still_refused)
            gate.set()
            if refusal is None:
                await state(s, item.queued_task_id, "running")
            else:
                await settled.wait()
                async with s.live.dispatcher.queue_lock:
                    assert not s.live.dispatcher.launching
                    current = s.live.dispatcher.items[item.queued_task_id]
                if refusal in {"internal", "invalid_request"}:
                    assert current.state == "failed"
                else:
                    response = await s.client.post(path, json={"revision": current.revision})
                    assert response.status_code == 202
                assert not s.live._agents
                monkeypatch.setattr(s.live, "_check_start", original)
            await folded(s)
        finally:
            gate.set()


async def test_shutdown_cancels_launch_and_restart_starts_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = asyncio.Event()

    async def held(*_args: Any, **_kwargs: Any) -> Any:
        entered.set()
        await asyncio.Event().wait()

    settings = live_settings(tmp_path)
    async with asyncio.timeout(DEADLINE_S):
        async with live_server(settings) as s:
            monkeypatch.setattr(ChangeManager, "open", held)
            await enqueue(s)
            await entered.wait()
        assert not s.live.dispatcher.launching and s.live._slots["A"] is None
        [error] = [e.payload for e in s.events() if e.type == "error"]
        assert not error.fatal and "queued-task-1" in error.message
        assert read_queue(s.store).tasks[0].state == "queued"
        async with live_server(settings) as restarted:
            assert (await restarted.client.get("/queue")).json()["tasks"] == []


async def test_validation_capacity_and_cancel_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        for bad in [
            body(model="unknown"),
            body(slot="F"),
            body(task=" "),
            body(zone={"paths": ["shop/pricing.py"], "node_ids": []}),
        ]:
            assert error_code(await s.client.post("/queue", json=bad)) == (422, "invalid_request")
        assert error_code(
            await s.client.post("/queue", json={"plan_id": "plan-1", "plan_revision": 1})
        ) == (404, "not_found")

        def braked(*_args: Any) -> Any:
            raise ApiError("spend_brake", "braked")

        monkeypatch.setattr(s.live, "_check_start", braked)
        await asyncio.gather(*(enqueue(s) for _ in range(32)))
        assert error_code(await s.client.post("/queue", json=body())) == (409, "wrong_state")
        await state(s, "queued-task-1", "blocked")
        current = s.live.dispatcher.items["queued-task-1"]
        assert (
            await s.client.post("/queue/queued-task-1/cancel", json={"revision": current.revision})
        ).status_code == 202
        await enqueue(s)
        await state(s, "queued-task-33", "blocked")
        await folded(s)


async def test_braked_head_is_skipped_then_keeps_its_place_when_healthy(tmp_path: Path) -> None:
    settings = meta_settings(tmp_path, agent_model_b="fake", fake_script_b=FAKE_SCRIPT)
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(settings, lambda profile, _s: GatedModel(profile)) as s,
    ):
        s.live.ledger.state = LedgerState(136.0, ())
        first = await enqueue(s, slot="A")
        await state(s, first.queued_task_id, "blocked")
        second = await enqueue(s, slot="B")
        await state(s, second.queued_task_id, "running")
        third = await enqueue(s, slot="A")
        await state(s, third.queued_task_id, "blocked")
        assert s.live._slots["A"] is None
        s.live.ledger.state = LedgerState(0.0, ())
        s.live.dispatcher.wake.set()
        await state(s, first.queued_task_id, "running")
        await idle(s)
        assert s.live.dispatcher.items[third.queued_task_id].state == "queued"
        assert not s.live.dispatcher.items[third.queued_task_id].agent_ids
        await folded(s)


async def test_suspect_main_blocks_without_holding_a_slot(tmp_path: Path) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        manager = s.live._manager
        assert manager is not None
        manager._ctx.state.suspect = True
        item = await enqueue(s)
        await state(s, item.queued_task_id, "blocked")
        current = s.live.dispatcher.items[item.queued_task_id]
        assert current.detail is not None and "main" in current.detail
        assert not current.agent_ids and s.live._slots["A"] is None
        response = await s.client.post(
            f"/queue/{item.queued_task_id}/cancel", json={"revision": current.revision}
        )
        assert response.status_code == 202
        await folded(s)
