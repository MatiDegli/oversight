"""The append-only event store of one run: a SQLite file, one writer task, and live subscribers.

Producers call `publish` from any task. Only the writer task assigns `seq` and `ts`, so `seq` goes
1, 2, 3 with no gaps even when two agents publish at once. It drains whatever is queued into one
transaction, so a burst of events costs one commit. An event reaches subscribers only after its
transaction commits, so what a client sees is always in the file.
"""

import asyncio
import re
import sqlite3
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from oversight.events.clock import Clock, format_ts, system_clock
from oversight.events.graph_fold import apply_delta
from oversight_contracts.events import (
    AgentFocus,
    AgentStatus,
    AgentStep,
    ChangeProposed,
    ChangeResolved,
    ChangeUpdated,
    ErrorReport,
    RunStarted,
    UsageTick,
)
from oversight_contracts.graph import GraphDelta, GraphSnapshot

type EventPayload = (
    RunStarted
    | GraphSnapshot
    | GraphDelta
    | AgentStatus
    | AgentStep
    | AgentFocus
    | ChangeUpdated
    | ChangeProposed
    | ChangeResolved
    | UsageTick
    | ErrorReport
)

EVENT_TYPES: dict[type[EventPayload], str] = {
    RunStarted: "run.started",
    GraphSnapshot: "graph.snapshot",
    GraphDelta: "graph.delta",
    AgentStatus: "agent.status",
    AgentStep: "agent.step",
    AgentFocus: "agent.focus",
    ChangeUpdated: "change.updated",
    ChangeProposed: "change.proposed",
    ChangeResolved: "change.resolved",
    UsageTick: "usage.tick",
    ErrorReport: "error",
}
"""The event type each payload travels under; the store derives `type` from the payload."""

_TS_FORMAT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS run (run_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    envelope TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class StoredEvent:
    """One committed event; `envelope` is its JSON, exactly as stored and sent."""

    seq: int
    ts: str
    type: str
    envelope: str


class StoreClosedError(RuntimeError):
    """Raised by `publish` once the store is closing."""


@dataclass
class _Pending:
    type: str
    payload: EventPayload
    payload_json: str
    ts: str | None
    done: asyncio.Future[StoredEvent]


def _connect(path: Path) -> sqlite3.Connection:
    # The writer task is the only user, through one worker thread at a time.
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def read_store(path: Path) -> list[StoredEvent]:
    """Every event in a run's file, in `seq` order, read without writing to it."""
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT seq, ts, type, envelope FROM events ORDER BY seq")
        return [StoredEvent(seq, ts, type_, envelope) for seq, ts, type_, envelope in rows]
    finally:
        connection.close()


class EventStore:
    """The log of one run, kept in `path` (`.runtime/runs/{run_id}.sqlite`) and in memory.

    A run's log is small, so subscribers read from the in-memory copy of what was committed.
    """

    def __init__(
        self, path: Path, run_id: str, clock: Clock = system_clock, max_batch: int = 256
    ) -> None:
        self.path = path
        self.run_id = run_id
        self._clock = clock
        self._max_batch = max_batch
        self._connection: sqlite3.Connection | None = None
        self._queue: asyncio.Queue[_Pending | None] = asyncio.Queue()
        self._writer: asyncio.Task[None] | None = None
        self._events: list[StoredEvent] = []
        self._graph: GraphSnapshot | None = None
        self._committed = asyncio.Event()
        self._shutdown_task: asyncio.Task[None] | None = None
        self._closing = False
        self._closed = False

    async def start(self) -> None:
        """Create the run's file and start the writer; a run's file is never reopened to write."""
        self._connection = await asyncio.to_thread(self._create)
        self._writer = asyncio.create_task(self._write_loop(), name=f"event-writer-{self.run_id}")

    def _create(self) -> sqlite3.Connection:
        # All of it runs in a worker thread: the event loop never waits on the disk.
        if self.path.exists():
            raise FileExistsError(f"the event store of {self.run_id} already exists")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = _connect(self.path)
        with connection:
            connection.executescript(_SCHEMA)
            connection.execute("INSERT INTO run (run_id) VALUES (?)", (self.run_id,))
        return connection

    async def close(self) -> None:
        """Commit what is queued, stop the writer, end every subscription and close the file.

        The shutdown runs as a task of its own, so cancelling a caller does not stop it half way:
        queued events still commit and the file still closes. Calling `close` again, also after a
        cancelled call, waits for that same shutdown.
        """
        self._closing = True
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.create_task(
                self._shutdown(), name=f"event-store-close-{self.run_id}"
            )
        await asyncio.shield(self._shutdown_task)

    async def _shutdown(self) -> None:
        try:
            if self._writer is not None:
                await self._queue.put(None)
                await self._writer
        finally:
            if self._connection is not None:
                await asyncio.to_thread(self._connection.close)
            self._closed = True
            self._notify()

    async def __aenter__(self) -> "EventStore":
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def publish(self, payload: EventPayload, *, ts: str | None = None) -> StoredEvent:
        """Append one event and return it once committed, with its `seq` and `ts`.

        `ts` defaults to the clock at commit time; the replay player passes the recorded one.
        """
        if self._closing or self._writer is None:
            raise StoreClosedError("the event store is not accepting events")
        if ts is not None and not _TS_FORMAT.match(ts):
            raise ValueError(f"ts must look like 2026-10-27T14:03:12.345Z, got {ts!r}")
        if isinstance(payload, RunStarted) and payload.run_id != self.run_id:
            raise ValueError(f"run.started names {payload.run_id}, but this log is {self.run_id}")
        pending = _Pending(
            type=EVENT_TYPES[type(payload)],
            payload=payload,
            payload_json=payload.model_dump_json(),
            ts=ts,
            done=asyncio.get_running_loop().create_future(),
        )
        await self._queue.put(pending)
        return await pending.done

    @property
    def last_seq(self) -> int:
        return len(self._events)

    def graph(self) -> tuple[int, GraphSnapshot | None]:
        """Main's graph and the last committed `seq`, read together; None before a snapshot."""
        return len(self._events), self._graph

    async def subscribe(self, after: int = 0) -> AsyncIterator[StoredEvent]:
        """The committed events after `after`, then live ones as they commit, in `seq` order.

        It ends when the store closes. `seq` n sits at index n - 1, since `seq` has no gaps.
        """
        index = max(after, 0)
        while True:
            while index < len(self._events):
                yield self._events[index]
                index += 1
            if self._closed:
                return
            await self._committed.wait()

    def _notify(self) -> None:
        # Wake every waiting subscriber, and give later waits a fresh event.
        committed, self._committed = self._committed, asyncio.Event()
        committed.set()

    async def _write_loop(self) -> None:
        stopping = False
        while not stopping:
            first = await self._queue.get()
            if first is None:
                return
            batch = [first]
            while len(batch) < self._max_batch:
                try:
                    item = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if item is None:
                    stopping = True
                    break
                batch.append(item)
            await self._commit(batch)

    async def _commit(self, batch: list[_Pending]) -> None:
        rows: list[StoredEvent] = []
        for offset, pending in enumerate(batch):
            seq = len(self._events) + offset + 1
            ts = pending.ts or format_ts(self._clock())
            envelope = (
                f'{{"seq":{seq},"ts":"{ts}","type":"{pending.type}",'
                f'"payload":{pending.payload_json}}}'
            )
            rows.append(StoredEvent(seq, ts, pending.type, envelope))
        try:
            await asyncio.to_thread(self._insert, rows)
        except Exception as error:
            # Nothing was committed, so the next batch reuses these seq numbers: still no gaps.
            for pending in batch:
                if not pending.done.done():
                    pending.done.set_exception(error)
            return
        for pending, row in zip(batch, rows, strict=True):
            self._events.append(row)
            if isinstance(pending.payload, GraphSnapshot):
                self._graph = pending.payload
            elif isinstance(pending.payload, GraphDelta) and self._graph is not None:
                self._graph = apply_delta(self._graph, pending.payload)
            if not pending.done.done():
                pending.done.set_result(row)
        self._notify()

    def _insert(self, rows: list[StoredEvent]) -> None:
        assert self._connection is not None
        with self._connection:
            self._connection.executemany(
                "INSERT INTO events (seq, ts, type, envelope) VALUES (?, ?, ?, ?)",
                [(row.seq, row.ts, row.type, row.envelope) for row in rows],
            )
