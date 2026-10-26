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
