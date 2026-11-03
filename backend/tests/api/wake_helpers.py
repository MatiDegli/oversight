"""Controlled deadlines, recorded inputs and committed-event barriers for wake tests."""

# Tests observe runtime ownership without waiting for real coalescing deadlines.
# pyright: reportPrivateUsage=false

import asyncio
from collections.abc import Callable, Sequence
from typing import Any

from backend.tests.api.lead_helpers import ThreadModels
from backend.tests.events.test_lead_wake import plan, status, suite_execution
from live_helpers import DEADLINE_S

from oversight.agents import Message, ModelReply, ToolSchema
from oversight.runtime_lead_wakes import WakeClock

# Allow loaded runners to progress while bounding stuck barriers below the live-test deadline.
BARRIER_TIMEOUT_S = DEADLINE_S / 2


class ControlledClock(WakeClock):
    def __init__(self) -> None:
        self.time = 0.0
        self.waiters: set[asyncio.Event] = set()
        self.waited = asyncio.Event()

    def now(self) -> float:
        return self.time

    def advance(self, seconds: float) -> None:
        self.time += seconds
        for event in self.waiters:
            event.set()

    async def wait(self, changed: asyncio.Event, deadline: float | None) -> None:
        self.waiters.add(changed)
        self.waited.set()
        try:
            if deadline is None or self.time < deadline:
                await changed.wait()
        finally:
            self.waiters.discard(changed)


class Models(ThreadModels):
    def __init__(self, scripts: list[list[dict[str, Any]]] | None = None) -> None:
        super().__init__(scripts)
        self.inputs: list[list[Message]] = []
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.release.set()
        self.closed: list[int] = []

    def __call__(self, profile: Any, slot: Any) -> Any:
        inner = super().__call__(profile, slot)
        owner, index = self, len(self.built)

        class Client:
            async def complete(
                self, messages: Sequence[Message], tools: Sequence[ToolSchema]
            ) -> ModelReply:
                owner.inputs.append(list(messages))
                owner.entered.set()
                await owner.release.wait()
                return await inner.complete(messages, tools)

            async def aclose(self) -> None:
                owner.closed.append(index)

        return Client()


async def spin(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(BARRIER_TIMEOUT_S):
        while not predicate():  # noqa: ASYNC110 -- scheduler-only barrier, no elapsed-time sleep.
            await asyncio.sleep(0)


def owned(s: Any) -> Any:
    return s.live.leads.leads["agent-1"]


async def caught_up(s: Any) -> None:
    through = s.store.last_seq
    await spin(lambda: owned(s).watcher.cursor >= through)


async def triggers(s: Any, *, lead_id: str = "agent-1", detail: str = "Failed") -> list[int]:
    plan_id = "plan-1" if lead_id == "agent-1" else "plan-9"
    await s.store.publish(plan(started_by=lead_id, plan_id=plan_id).payload)
    await s.store.publish(status(plan_id=plan_id).payload)
    rows = [await s.store.publish(suite_execution().payload)]
    rows.append(
        await s.store.publish(status(state="halted", detail=detail, plan_id=plan_id).payload)
    )
    rows.append(await s.store.publish(plan(3, state="completed", plan_id=plan_id).payload))
    await caught_up(s)
    return [row.seq for row in rows]


async def next_trigger(s: Any) -> int:
    row = await s.store.publish(status(state="halted").payload)
    await caught_up(s)
    return row.seq


def notices(s: Any) -> list[Any]:
    return [
        e
        for e in s.events()
        if e.type == "lead.status" and (e.payload.detail or "").startswith("Skipped a wake: ")
    ]


async def ready(s: Any, clock: ControlledClock) -> None:
    from backend.tests.api.lead_helpers import start_idle

    s.live.leads.wake_clock = clock
    await start_idle(s)
    await caught_up(s)


async def wake_done(s: Any, clock: ControlledClock) -> None:
    before = owned(s).wakes
    clock.advance(5)
    await spin(lambda: owned(s).wakes > before)
    task = owned(s).task
    assert task is not None
    await task
