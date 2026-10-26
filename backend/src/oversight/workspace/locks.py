"""Locks and thread offload.

One reentrant lock for main and one per worktree, each owned by an asyncio task. The order is main
first, then at most one worktree lock; anything else raises `LockOrderError` at once instead of
risking a deadlock. Two agents share only main, which they always take first.
"""

import asyncio
import contextlib
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

from oversight.workspace.errors import LockOrderError


class _TaskLock:
    def __init__(self, name: str, *, is_main: bool) -> None:
        self.name = name
        self.is_main = is_main
        self.owner: asyncio.Task[Any] | None = None
        self.depth = 0
        self._lock = asyncio.Lock()

    async def acquire(self, task: asyncio.Task[Any]) -> None:
        if self.owner is task:
            self.depth += 1
            return
        await self._lock.acquire()
        self.owner = task
        self.depth = 1

    def release(self, task: asyncio.Task[Any]) -> None:
        if self.owner is not task:
            raise LockOrderError(f"the {self.name} lock is not held by this task")
        self.depth -= 1
        if self.depth == 0:
            self.owner = None
            self._lock.release()


def _current_task() -> asyncio.Task[Any]:
    task = asyncio.current_task()
    if task is None:
        raise LockOrderError("locks are taken from inside a task")
    return task


class Locks:
    def __init__(self) -> None:
        self._main = _TaskLock("main", is_main=True)
        self._agents: dict[str, _TaskLock] = {}
        self._operations = 0

    def _agent(self, agent_id: str) -> _TaskLock:
        if agent_id not in self._agents:
            self._agents[agent_id] = _TaskLock(agent_id, is_main=False)
        return self._agents[agent_id]

    def _all(self) -> list[_TaskLock]:
        return [self._main, *self._agents.values()]

    @property
    def idle(self) -> bool:
        """True when no lock is held and no tree operation is running."""
        return self._operations == 0 and all(lock.owner is None for lock in self._all())

    def holds(self, *, main: bool = False, agent_id: str | None = None) -> bool:
        """True when the current task holds the main lock, or the named agent's lock."""
        task = asyncio.current_task()
        if task is None:
            return False
        lock = self._main if main else self._agent(agent_id) if agent_id is not None else None
        return lock is not None and lock.owner is task

    @contextmanager
    def track(self) -> Generator[None]:
        """Count an operation, so a startup-only call can see that one is running."""
        self._operations += 1
        try:
            yield
        finally:
            self._operations -= 1

    async def _take(self, lock: _TaskLock) -> None:
        task = _current_task()
        held = [other for other in self._all() if other.owner is task]
        if lock not in held:
            if lock.is_main and held:
                raise LockOrderError(f"the main lock comes before {held[0].name}'s")
            if not lock.is_main and any(not other.is_main for other in held):
                raise LockOrderError(f"one worktree lock at a time: {lock.name} while holding one")
        await lock.acquire(task)

    @asynccontextmanager
    async def acquire(
        self, *, main: bool = False, agent_id: str | None = None
    ) -> AsyncGenerator[None]:
        """Take the main lock, then the agent's, in that order; release both on exit."""
        if not main and agent_id is None:
            raise ValueError("name the main lock, an agent's, or both")
        taken: list[_TaskLock] = []
        try:
            if main:
                await self._take(self._main)
                taken.append(self._main)
            if agent_id is not None:
                agent_lock = self._agent(agent_id)
                await self._take(agent_lock)
                taken.append(agent_lock)
            yield
        finally:
            task = _current_task()
            for lock in reversed(taken):
                lock.release(task)


async def offload[T](fn: Callable[[], T]) -> T:
    """Run a blocking helper in a thread. If the caller is cancelled, keep waiting until the thread
    ends, so a lock around the call is held until the delete or replace is over."""
    future = asyncio.ensure_future(asyncio.to_thread(fn))
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        while not future.done():
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.shield(future)
        if not future.cancelled():
            future.exception()
        raise
