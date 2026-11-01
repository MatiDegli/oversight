"""Memory holds through real HTTP and queue dispatch, with no provider traffic."""

# Admission and selection invariants belong to the composition root.
# pyright: reportPrivateUsage=false

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from live_helpers import (
    DEADLINE_S,
    GatedModel,
    LiveServer,
    error_code,
    is_type,
    live_server,
    live_settings,
)

from oversight_contracts.queue import QueuedTask

pytestmark = pytest.mark.anyio
REASON = "Waiting: memory at 90% or more"


@pytest.fixture
def reading(monkeypatch: pytest.MonkeyPatch) -> list[int | None]:
    value: list[int | None] = [90]
    monkeypatch.setattr("oversight.app.memory_used", lambda: value[0])
    return value


async def enqueue(s: LiveServer) -> QueuedTask:
    response = await s.client.post(
        "/queue",
        json={"agent": {"task": "Document the coupon.", "slot": None, "model": None, "zone": None}},
    )
    assert response.status_code == 201, response.text
    return QueuedTask.model_validate(response.json())


async def state(s: LiveServer, item: QueuedTask, value: str) -> None:
    events = await s.until(
        is_type("queue.updated", queued_task_id=item.queued_task_id, state=value)
    )
    assert events[-1].payload.state == value


def states(s: LiveServer, item: QueuedTask) -> list[str]:
    return [
        e.payload.state
        for e in s.events()
        if e.type == "queue.updated" and e.payload.queued_task_id == item.queued_task_id
    ]


def selected(s: LiveServer, monkeypatch: pytest.MonkeyPatch, passes: int = 1) -> asyncio.Event:
    done = asyncio.Event()
    original = s.live.dispatcher._select
    count = 0

    async def select() -> Any:
        nonlocal count
        result = await original()
        if result is None:
            count += 1
            if count >= passes:
                done.set()
        return result

    monkeypatch.setattr(s.live.dispatcher, "_select", select)
    return done


async def test_direct_start_held_then_runs_without_reserving_early(
    tmp_path: Path, reading: list[int | None]
) -> None:
    model = GatedModel()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: model) as s,
    ):
        body = {"task": "Document the coupon."}
        response = await s.client.post("/agents", json=body)
        assert error_code(response) == (409, "wrong_state")
        assert response.json()["error"]["message"] == REASON
        assert all(holder is None for holder in s.live._slots.values())
        assert not any(e.type == "agent.status" for e in s.events())
        assert not model.entered.is_set()
        reading[0] = 89
        assert (await s.client.post("/agents", json=body)).status_code == 201
        await model.entered.wait()
        assert any(e.type == "agent.status" for e in s.events())


async def test_unknown_reading_never_holds(tmp_path: Path, reading: list[int | None]) -> None:
    reading[0] = None
    model = GatedModel()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: model) as s,
    ):
        assert (await s.client.post("/agents", json={"task": "Work."})).status_code == 201
        await model.entered.wait()


async def test_empty_task_wins_over_memory_hold(tmp_path: Path, reading: list[int | None]) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        response = await s.client.post("/agents", json={"task": " "})
        assert error_code(response) == (422, "invalid_request")
        assert response.json()["error"]["message"] == "the task is empty"


async def test_queue_rechecks_memory_without_wake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reading: list[int | None]
) -> None:
    monkeypatch.setattr("oversight.runtime_queue.MEMORY_RECHECK_S", 0.01)
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: GatedModel()) as s,
    ):
        passes = selected(s, monkeypatch, 3)
        item = await enqueue(s)
        wake_set = Mock(wraps=s.live.dispatcher.wake.set)
        monkeypatch.setattr(s.live.dispatcher.wake, "set", wake_set)
        await state(s, item, "blocked")
        await passes.wait()
        held = s.live.dispatcher.items[item.queued_task_id]
        assert held.detail == REASON and not held.agent_ids
        assert not any(e.type == "agent.status" for e in s.events())
        assert not s.live.dispatcher.wake.is_set()
        reading[0] = 89
        await state(s, item, "running")
        assert states(s, item) == ["queued", "blocked", "queued", "running"]
        wake_set.assert_not_called()


async def test_rechecks_do_not_spam_queue_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reading: list[int | None]
) -> None:
    monkeypatch.setattr("oversight.runtime_queue.MEMORY_RECHECK_S", 0.01)
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        passes = selected(s, monkeypatch, 3)
        item = await enqueue(s)
        await passes.wait()
        assert states(s, item) == ["queued", "blocked"]
        assert s.live.dispatcher.items[item.queued_task_id].detail == REASON
        assert not any(e.type == "agent.status" for e in s.events())


async def test_memory_drop_respects_persons_agent_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reading: list[int | None]
) -> None:
    monkeypatch.setattr("oversight.runtime_queue.MEMORY_RECHECK_S", 0.01)
    model = GatedModel()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path, max_agents=1), lambda _p, _s: model) as s,
    ):
        first, second = await enqueue(s), await enqueue(s)
        await state(s, second, "blocked")
        done = selected(s, monkeypatch)
        reading[0] = 89
        await state(s, first, "running")
        await done.wait()
        # The selection after launch clears the second item's old memory refusal.
        async with s.live.dispatcher.queue_lock:
            remaining = s.live.dispatcher.items[second.queued_task_id]
            assert remaining.state == "queued" and remaining.detail is None
            assert not remaining.agent_ids
            assert list(s.live._slots.values()) == ["agent-1"]
            assert not s.live.dispatcher._held
        await model.entered.wait()
        assert states(s, first) == ["queued", "blocked", "queued", "running"]
        assert states(s, second) == ["queued", "blocked", "queued"]


async def test_shutdown_cancels_memory_wait_without_dispatcher_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reading: list[int | None]
) -> None:
    async with asyncio.timeout(DEADLINE_S):
        async with live_server(live_settings(tmp_path)) as s:
            done = selected(s, monkeypatch)
            item = await enqueue(s)
            await state(s, item, "blocked")
            await done.wait()
            task = s.live.dispatcher._task
            assert task is not None and not task.done()
            assert s.live.dispatcher._held
        assert task.cancelled()
        # Preserve the queue's existing shutdown notice for unresumed work.
        assert [e.payload.message for e in s.events() if e.type == "error"] == [
            f"Queue ended without resuming: {item.queued_task_id}"
        ]
