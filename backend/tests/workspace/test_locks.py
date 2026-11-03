import asyncio
import threading

import pytest

from oversight.workspace.errors import LockOrderError
from oversight.workspace.locks import Locks, offload

pytestmark = pytest.mark.anyio


async def test_main_then_one_worktree_in_order_and_reentrant() -> None:
    locks = Locks()
    async with (
        locks.acquire(main=True),
        locks.acquire(agent_id="agent-1"),
        locks.acquire(main=True, agent_id="agent-1"),
    ):
        assert locks.holds(main=True)
        assert locks.holds(agent_id="agent-1")
        assert not locks.holds(agent_id="agent-2")
    assert locks.idle
    assert not locks.holds(main=True)


async def test_out_of_order_acquisition_raises_at_once() -> None:
    locks = Locks()
    async with locks.acquire(agent_id="agent-1"):
        with pytest.raises(LockOrderError):
            async with locks.acquire(main=True):
                pytest.fail("main after a worktree must not be taken")
        with pytest.raises(LockOrderError):
            async with locks.acquire(agent_id="agent-2"):
                pytest.fail("two worktrees must not be taken")
        assert locks.holds(agent_id="agent-1")
    assert locks.idle
    async with locks.acquire(main=True, agent_id="agent-1"):
        with pytest.raises(LockOrderError):
            async with locks.acquire(agent_id="agent-2"):
                pytest.fail("a second worktree under main must not be taken")
    assert locks.idle


async def test_a_failed_second_lock_releases_the_first() -> None:
    locks = Locks()
    async with locks.acquire(agent_id="agent-1"):
        with pytest.raises(LockOrderError):
            async with locks.acquire(main=True, agent_id="agent-1"):
                pytest.fail("unreachable")
        assert locks.holds(agent_id="agent-1")
    assert locks.idle


async def test_two_tasks_exclude_each_other_on_main() -> None:
    locks = Locks()
    entered = asyncio.Event()
    leave = asyncio.Event()
    order: list[str] = []

    async def first() -> None:
        async with locks.acquire(main=True):
            order.append("first in")
            entered.set()
            await leave.wait()
            order.append("first out")

    async def second() -> None:
        await entered.wait()
        async with locks.acquire(main=True):
            order.append("second in")

    tasks = [asyncio.create_task(first()), asyncio.create_task(second())]
    await entered.wait()
    for _ in range(5):
        await asyncio.sleep(0)
    assert order == ["first in"]
    leave.set()
    await asyncio.gather(*tasks)
    assert order == ["first in", "first out", "second in"]


async def test_cancelled_offload_holds_lock_until_thread_ends() -> None:
    locks = Locks()
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def helper() -> str:
        started.set()
        release.wait()
        finished.set()
        return "done"

    async def section() -> None:
        async with locks.acquire(main=True):
            await offload(helper)

    task = asyncio.create_task(section())
    await asyncio.to_thread(started.wait)
    task.cancel()
    for _ in range(5):
        await asyncio.sleep(0)
    assert not task.done(), "the section ended while its thread was still running"
    assert not locks.idle
    release.set()
    await asyncio.wait({task}, timeout=10)
    assert task.cancelled()
    assert finished.is_set()
    assert locks.idle


async def test_offload_returns_and_raises() -> None:
    assert await offload(lambda: 41 + 1) == 42

    def boom() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await offload(boom)


@pytest.mark.parametrize("boundary", ["main", "worktree", "Git lease"])
@pytest.mark.parametrize("race", [False, True], ids=["blocked", "handoff-race"])
async def test_cancel_while_waiting_releases_locks(boundary, race, monkeypatch) -> None:
    from oversight.workspace.locks import QueueWait

    locks = Locks()
    now = 10.0
    monkeypatch.setattr("oversight.workspace.locks.monotonic", lambda: now)
    entered, release = asyncio.Event(), asyncio.Event()
    delivered: list[float] = []
    waiting = asyncio.Event()

    async def holder():
        async with locks.acquire(
            main=boundary == "main",
            agent_id="agent-1"
            if boundary == "worktree"
            else "holder"
            if boundary == "Git lease"
            else None,
        ):
            if boundary == "Git lease":
                async with locks.git_lease():
                    entered.set()
                    await release.wait()
            else:
                entered.set()
                await release.wait()

        if race:
            task.cancel()  # after release, before the awakened acquisition resumes

    original = QueueWait.begin

    def begin(self, category):
        original(self, category)
        waiting.set()

    monkeypatch.setattr(QueueWait, "begin", begin)

    async def queued():
        async with (
            QueueWait(delivered.append) as queue,
            locks.acquire(main=True, agent_id="agent-1", queue=queue),
            locks.git_lease(queue=queue),
        ):
            pytest.fail("cancelled acquisition must not enter")

    holder_task = asyncio.create_task(holder())
    await entered.wait()
    task = asyncio.create_task(queued())
    await waiting.wait()
    now = 17.0
    if race:
        release.set()
        await holder_task  # release wakes the waiter; cancel before it can claim the lock
    task.cancel()
    asyncio.get_running_loop().call_soon(task.cancel)  # repeat during observer cleanup
    with pytest.raises(asyncio.CancelledError):
        await task
    assert delivered == [0.0, 7.0]
    all_locks = [locks._main, locks._agent("agent-1"), locks._git]
    for lock in all_locks:
        assert lock.owner is not task
        assert not lock._lock._waiters
    if not race:
        assert not holder_task.done()
        held = (
            locks._main
            if boundary == "main"
            else (locks._agent("agent-1") if boundary == "worktree" else locks._git)
        )
        assert held.owner is holder_task
    release.set()
    await holder_task
    async with locks.acquire(main=True, agent_id="agent-1"), locks.git_lease():
        assert locks.holds(main=True) and locks.holds(agent_id="agent-1")
    assert locks.idle
    assert not [t for t in asyncio.all_tasks() if t.get_name() == "run_tests queue observer"]


async def test_queue_callback_failure_does_not_strand_an_acquired_lock(monkeypatch):
    from oversight.workspace.locks import QueueWait

    locks = Locks()
    entered, release, waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()
    begin = QueueWait.begin

    def observe(self, category):
        begin(self, category)
        waiting.set()

    monkeypatch.setattr(QueueWait, "begin", observe)

    def report(elapsed):
        if elapsed:
            raise RuntimeError("report failed")

    async def holder():
        async with locks.acquire(main=True):
            entered.set()
            await release.wait()

    async def queued():
        async with QueueWait(report) as queue, locks.acquire(main=True, queue=queue):
            pytest.fail("the callback error must propagate")

    holding = asyncio.create_task(holder())
    await entered.wait()
    task = asyncio.create_task(queued())
    await waiting.wait()
    release.set()
    await holding
    with pytest.raises(RuntimeError, match="report failed"):
        await task
    assert locks.idle
    async with locks.acquire(main=True), locks.git_lease():
        pass
    assert not [t for t in asyncio.all_tasks() if t.get_name() == "run_tests queue observer"]


async def test_cancelled_handoff_does_not_count_as_contention(monkeypatch, caplog):
    from oversight.workspace.locks import QueueWait

    locks = Locks()
    waiting = asyncio.Event()
    begin = QueueWait.begin

    def observe(self, category):
        begin(self, category)
        waiting.set()

    monkeypatch.setattr(QueueWait, "begin", observe)

    async def queued():
        async with QueueWait(None) as queue, locks.acquire(main=True, queue=queue):
            pytest.fail("cancelled waiter cannot acquire")

    async with locks.acquire(main=True):
        waiter = asyncio.create_task(queued())
        await waiting.wait()
    waiter.cancel()  # asyncio cancels its waiter immediately; its cleanup has not run yet
    caplog.clear()
    elapsed = []
    async with QueueWait(elapsed.append) as queue, locks.acquire(main=True, queue=queue):
        assert locks.holds(main=True)
    assert elapsed == [0.0] and not caplog.records
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert locks.idle
    assert not [t for t in asyncio.all_tasks() if t.get_name() == "run_tests queue observer"]
