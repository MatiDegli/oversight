"""Locks and thread offload.

One reentrant lock for main and one per worktree, each owned by an asyncio task. The order is main
first, then at most one worktree lock; anything else raises `LockOrderError` at once instead of
risking a deadlock. Two agents share only main, which they always take first.
"""

import asyncio
import contextlib
import logging
from asyncio import sleep
from collections.abc import AsyncGenerator, Awaitable, Callable, Generator
from contextlib import asynccontextmanager, contextmanager
from time import monotonic
from typing import Any
from uuid import uuid4

from oversight.workspace.errors import LockOrderError


class QueueWait:
    """Call-scoped observation only: the caller still acquires every lock itself."""

    def __init__(self, report: Callable[[float], None] | None, *, enabled: bool = True) -> None:
        self._report = report if enabled else None
        self._enabled = enabled
        self._call = uuid4().hex
        self._elapsed = 0.0
        self._started: float | None = None
        self._category: str | None = None
        self._last_log: float | None = None
        self._timer: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "QueueWait":
        if self._report is not None:
            self._report(0.0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._timer is None:
            return
        self._timer.cancel()
        cancelled = False
        while not self._timer.done():
            try:
                await asyncio.shield(self._timer)
            except asyncio.CancelledError:
                # Drain even when Stop is repeated. Never abandon the logging observer.
                cancelled = True
        task = asyncio.current_task()
        if cancelled and exc[0] is None and task is not None and task.cancelling():
            raise asyncio.CancelledError

    def begin(self, category: str) -> None:
        if not self._enabled:
            return
        self._started = monotonic()
        self._category = category
        self._log_if_due()
        if self._timer is None:
            self._timer = asyncio.create_task(self._observe(), name="run_tests queue observer")

    def end(self) -> None:
        if self._started is None:
            return
        self._elapsed += monotonic() - self._started
        self._started = None
        self._category = None
        if self._report is not None:
            self._report(self._elapsed)

    def _log_if_due(self) -> None:
        now = monotonic()
        if self._category is not None and (self._last_log is None or now - self._last_log >= 30):
            logging.getLogger(__name__).warning(
                "run_tests call=%s waiting for %s lock", self._call, self._category
            )
            self._last_log = now

    async def _observe(self) -> None:
        while True:
            assert self._last_log is not None
            await sleep(max(0.001, self._last_log + 30 - monotonic()))
            self._log_if_due()
            if self._category is None:
                self._timer = None
                return


class _TaskLock:
    def __init__(self, name: str, *, is_main: bool) -> None:
        self.name = name
        self.is_main = is_main
        self.owner: asyncio.Task[Any] | None = None
        self.depth = 0
        self._lock = asyncio.Lock()
        self._pending: dict[asyncio.Task[Any], int] = {}

    async def acquire(
        self, task: asyncio.Task[Any], queue: QueueWait | None = None, category: str = ""
    ) -> None:
        if self.owner is task:
            self.depth += 1
            return
        # A released lock can still have a live waiter entitled to its next acquisition.
        # Cancelled waiters no longer contend, even before their finally blocks have run.
        pending = any(waiter.cancelling() <= count for waiter, count in self._pending.items())
        blocked = queue is not None and (self._lock.locked() or pending)
        if blocked:
            assert queue is not None
            queue.begin(category)
        self._pending[task] = task.cancelling()
        try:
            await self._lock.acquire()
            self.owner = task
            self.depth = 1
        finally:
            del self._pending[task]
            if blocked:
                assert queue is not None
                try:
                    queue.end()
                except BaseException:
                    # A reporting callback must not strand an acquisition the caller never got.
                    if self.owner is task:
                        self.release(task)
                    raise

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
        self._git = _TaskLock("Git lease", is_main=False)
        self._operations = 0
        self._git_hook: Callable[[], Awaitable[None]] | None = None

    def set_git_hook(self, hook: Callable[[], Awaitable[None]]) -> None:
        """One session hook, installed before admission; never replace a live barrier."""
        if self._git_hook is not None:
            raise LockOrderError("the Git admission hook is already installed")
        self._git_hook = hook

    def _agent(self, agent_id: str) -> _TaskLock:
        if agent_id not in self._agents:
            self._agents[agent_id] = _TaskLock(agent_id, is_main=False)
        return self._agents[agent_id]

    def _all(self) -> list[_TaskLock]:
        return [self._main, *self._agents.values()]

    @property
    def idle(self) -> bool:
        """True when no lock is held and no tree operation is running."""
        return self._operations == 0 and all(
            lock.owner is None for lock in [*self._all(), self._git]
        )

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

    async def _take(self, lock: _TaskLock, queue: QueueWait | None) -> None:
        task = _current_task()
        held = [other for other in self._all() if other.owner is task]
        if lock not in held:
            if self._git.owner is task:
                raise LockOrderError("tree locks come before the Git lease")
            if lock.is_main and held:
                raise LockOrderError(f"the main lock comes before {held[0].name}'s")
            if not lock.is_main and any(not other.is_main for other in held):
                raise LockOrderError(f"one worktree lock at a time: {lock.name} while holding one")
        await lock.acquire(task, queue, "main" if lock.is_main else "worktree")

    @asynccontextmanager
    async def git_lease(self, *, queue: QueueWait | None = None) -> AsyncGenerator[None]:
        """Serialize all server Git with suites, including cancellation cleanup.

        Order: main, worktree (or temporary), mode when applicable, then this lease.
        Reentrancy is task-owned; a child task must acquire its own locks and lease.
        """
        task = _current_task()
        if not any(lock.owner is task for lock in self._all()):
            raise LockOrderError("the Git lease requires a tree lock")
        await self._git.acquire(task, queue, "Git lease")
        try:
            if self._git.depth == 1 and self._git_hook is not None:
                await self._git_hook()
            yield
        finally:
            self._git.release(task)

    @asynccontextmanager
    async def acquire(
        self, *, main: bool = False, agent_id: str | None = None, queue: QueueWait | None = None
    ) -> AsyncGenerator[None]:
        """Take the main lock, then the agent's, in that order; release both on exit."""
        if not main and agent_id is None:
            raise ValueError("name the main lock, an agent's, or both")
        taken: list[_TaskLock] = []
        try:
            if main:
                await self._take(self._main, queue)
                taken.append(self._main)
            if agent_id is not None:
                agent_lock = self._agent(agent_id)
                await self._take(agent_lock, queue)
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
