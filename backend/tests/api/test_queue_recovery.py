"""Queue cancellation and durable launch publication across dispatcher failure and close."""

# Tests inject failures at the runtime's queue/launch synchronization boundaries.
# pyright: reportPrivateUsage=false

import asyncio
from pathlib import Path
from typing import Any

import pytest
from live_helpers import DEADLINE_S, GatedModel, is_type, live_settings
from plan_helpers import plan_server as live_server

from oversight_contracts.commands import EnqueueTask
from oversight_contracts.queue import QueuedAgentTask

pytestmark = pytest.mark.anyio


def request() -> EnqueueTask:
    return EnqueueTask(agent=QueuedAgentTask(task="Wait", slot=None, model=None, zone=None))


@pytest.mark.parametrize("blocked", [False, True])
async def test_queued_and_blocked_items_can_be_cancelled_after_dispatcher_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, blocked: bool
) -> None:
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: GatedModel()) as s,
    ):
        await s.client.post("/agents", json={"task": "Hold A"})
        if blocked:
            from oversight.api import ApiError

            def braked(*_args: Any) -> Any:
                raise ApiError("spend_brake", "braked")

            monkeypatch.setattr(s.live, "_check_start", braked)
        item = await s.live.dispatcher.enqueue(request())
        if blocked:
            await s.until(is_type("queue.updated", state="blocked"))
        async with s.live.dispatcher.queue_lock:

            async def failed() -> Any:
                raise RuntimeError("injected dispatcher failure")

            monkeypatch.setattr(s.live.dispatcher, "_select", failed)
        s.live.dispatcher.wake.set()
        await s.until(
            is_type("error", source="server", message="Queue dispatcher failed: RuntimeError")
        )
        assert s.live.dispatcher._task is not None
        await s.live.dispatcher._task
        assert s.live.dispatcher._closed and not s.live._closing
        item = s.live.dispatcher.items[item.queued_task_id]
        response = await s.client.post(
            f"/queue/{item.queued_task_id}/cancel", json={"revision": item.revision}
        )
        assert response.status_code == 202
        assert s.live.dispatcher.items[item.queued_task_id].state == "cancelled"


@pytest.mark.parametrize("plan_item", [False, True])
async def test_shutdown_after_launch_publishes_running_while_route_holds_queue_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plan_item: bool
) -> None:
    launched, release, recording = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: GatedModel()) as s,
    ):
        dispatcher = s.live.dispatcher
        original_launch = s.live._launch
        original_record = dispatcher._record_launch

        async def launch(*args: Any, **kwargs: Any) -> Any:
            created = await original_launch(*args, **kwargs)
            launched.set()
            await release.wait()
            return created

        async def record(*args: Any, **kwargs: Any) -> None:
            recording.set()
            await original_record(*args, **kwargs)

        monkeypatch.setattr(s.live, "_launch", launch)
        monkeypatch.setattr(dispatcher, "_record_launch", record)
        identifier: str | None = None
        if plan_item:
            created = await s.client.post("/plans", json={"task": "Wait"})
            identifier = created.json()["plan_id"]
            body = {"plan_id": identifier, "plan_revision": 2}
        else:
            body = request().model_dump()
        item = (await s.client.post("/queue", json=body)).json()
        await launched.wait()
        closing: asyncio.Task[None] | None = None
        try:
            async with dispatcher.queue_lock:
                release.set()
                await recording.wait()  # _launch has returned; publication is waiting on us.
                closing = asyncio.create_task(dispatcher.close())
                await asyncio.sleep(0)  # Deliver close's cancellation to the dispatcher.
                assert dispatcher._closed and not closing.done()
            await closing
            current = dispatcher.items[item["queued_task_id"]]
            assert current.state == "running" and current.agent_ids == [
                "agent-2" if plan_item else "agent-1"
            ]
            assert not dispatcher.launching
            if plan_item:
                assert identifier is not None
                assert s.live.plans.get(identifier).state == "running"
        finally:
            release.set()
            if closing is not None:
                await closing


async def test_item_numbers_remain_monotonic_after_removing_a_terminal_item(tmp_path: Path) -> None:
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: GatedModel()) as s,
    ):
        await s.client.post("/agents", json={"task": "Hold A"})
        item = await s.live.dispatcher.enqueue(request())
        response = await s.client.post(f"/queue/{item.queued_task_id}/cancel", json={"revision": 1})
        assert response.status_code == 202
        async with s.live.dispatcher.queue_lock:
            del s.live.dispatcher.items[item.queued_task_id]
        second = await s.live.dispatcher.enqueue(request())
        assert second.queued_task_id == "queued-task-2"


async def test_all_ready_slots_are_reserved_or_none_are(
    tmp_path: Path,
) -> None:
    from live_helpers import two_fakes

    from oversight.api import ApiError
    from oversight.changes import plan_core, queued
    from oversight_contracts.plans import SubtaskDraft

    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(two_fakes(tmp_path), lambda _p, _s: GatedModel()) as s,
    ):
        await s.client.post("/agents", json={"task": "Hold A", "slot": "A"})
        _, graph = s.store.graph()
        assert graph is not None
        plan = plan_core.propose(
            plan_core.planning("Two tasks", graph, 1),
            [SubtaskDraft(task=f"task {i}", zone=plan_core.whole_graph(graph)) for i in range(2)],
        ).model_copy(update={"started_by": "agent-99"})
        plan = plan_core.link(plan, 2, "queued-task-1")
        await s.live.plans.save(plan)
        item = queued(EnqueueTask(plan_id=plan.plan_id, plan_revision=3), 1)
        async with s.live.dispatcher.queue_lock:
            with pytest.raises(ApiError) as caught:
                s.live.dispatcher._reserve(item)
            assert caught.value.code == "slot_busy"
            assert s.live._slots == {"A": "agent-1", "B": None}
        assert not s.live.dispatcher.launching
