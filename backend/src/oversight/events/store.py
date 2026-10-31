"""The append-only event store of one run: a SQLite file, one writer task, and live subscribers.

Producers call `publish` from any task. Only the writer task assigns `seq` and `ts`, so `seq` goes
1, 2, 3 with no gaps even when two agents publish at once. It drains whatever is queued into one
transaction, so a burst of events costs one commit. An event reaches subscribers only after its
transaction commits, so what a client sees is always in the file.
"""

import asyncio
import json
import re
import sqlite3
from collections import ChainMap
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import TypeAdapter

from oversight.events.clock import Clock, format_ts, system_clock
from oversight.events.graph_fold import apply_delta
from oversight.events.needs_you import TerminalClaim, TerminalClaims, terminal_key
from oversight.events.references import validate_references
from oversight_contracts.events import (
    AgentFocus,
    AgentStatus,
    AgentStep,
    ChangeProposed,
    ChangeResolved,
    ChangeUpdated,
    ErrorReport,
    LeadStatus,
    ModelAttemptFailed,
    RevertResult,
    RunStarted,
    ServerEvent,
    SuiteExecution,
    UsageTick,
)
from oversight_contracts.grants import GrantCreated, GrantRevoked
from oversight_contracts.graph import GraphDelta, GraphSnapshot
from oversight_contracts.plans import Plan
from oversight_contracts.policy import ChangeAssessment, MergeModeState
from oversight_contracts.queue import QueuedTask
from oversight_contracts.review import ChangeFacts, CrossReview

# Initialize before any producers or the writer can share its validators.
_SERVER_EVENT_ADAPTER = TypeAdapter(ServerEvent)

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
    | ModelAttemptFailed
    | ErrorReport
    | ChangeAssessment
    | MergeModeState
    | Plan
    | CrossReview
    | ChangeFacts
    | SuiteExecution
    | RevertResult
    | QueuedTask
    | LeadStatus
    | GrantCreated
    | GrantRevoked
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
    ModelAttemptFailed: "model.attempt_failed",
    ErrorReport: "error",
    ChangeAssessment: "change.assessed",
    MergeModeState: "merge_mode.changed",
    Plan: "plan.updated",
    CrossReview: "change.reviewed",
    ChangeFacts: "change.facts_recorded",
    SuiteExecution: "tests.executed",
    RevertResult: "revert.resolved",
    QueuedTask: "queue.updated",
    LeadStatus: "lead.status",
    GrantCreated: "grant.created",
    GrantRevoked: "grant.revoked",
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


class EventRejected(ValueError):
    """A semantic rejection of one event; retrying it cannot succeed."""


class StoreClosedError(RuntimeError):
    """Raised by `publish` once the store is closing."""


@dataclass
class _Pending:
    type: str
    payload: EventPayload
    payload_json: str
    ts: str | None
    done: asyncio.Future[StoredEvent]
    terminal: bool = False
    committing: bool = False


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
        self,
        path: Path,
        run_id: str,
        clock: Clock = system_clock,
        max_batch: int = 256,
        *,
        mode: Literal["live", "replay"] = "live",
        allow_fake_provider: bool = False,
    ) -> None:
        self.mode = mode
        self.allow_fake_provider = allow_fake_provider
        self.path = path
        self.run_id = run_id
        self._clock = clock
        self._max_batch = max_batch
        self._connection: sqlite3.Connection | None = None
        self._queue: asyncio.Queue[_Pending | None] = asyncio.Queue()
        self._writer: asyncio.Task[None] | None = None
        self._events: list[StoredEvent] = []
        self._reference_events: dict[int, ServerEvent] = {}
        self._graph: GraphSnapshot | None = None
        self._committed = asyncio.Event()
        self._shutdown_task: asyncio.Task[None] | None = None
        self._closing = False
        self._closed = False
        self._terminal = TerminalClaims[StoredEvent]()

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
        try:
            if ts is not None and not _TS_FORMAT.match(ts):
                raise ValueError(f"ts must look like 2026-10-27T14:03:12.345Z, got {ts!r}")
            if isinstance(payload, RunStarted) and payload.run_id != self.run_id:
                raise ValueError(
                    f"run.started names {payload.run_id}, but this log is {self.run_id}"
                )
            kind = EVENT_TYPES[type(payload)]
            payload_json = payload.model_dump_json()
            # Validate serialized data, not trusted or model_construct'ed instances. This also
            # freezes nested mutable lists before a producer can change them behind the writer.
            frozen = _SERVER_EVENT_ADAPTER.validate_json(
                json.dumps(
                    {
                        "seq": 1,
                        "ts": ts or "2000-01-01T00:00:00.000Z",
                        "type": kind,
                        "payload": json.loads(payload_json),
                    }
                )
            ).payload
        except (ValueError, TypeError, KeyError) as error:
            raise EventRejected(str(error)) from error
        pending = _Pending(
            type=kind,
            payload=frozen,
            payload_json=frozen.model_dump_json(),
            ts=ts,
            done=asyncio.get_running_loop().create_future(),
        )
        # Live producers claim across codes. Replay retains every historical duplicate.
        if self.mode == "live" and isinstance(frozen, ErrorReport) and terminal_key(frozen):
            return await self._publish_terminal(pending, frozen)
        await self._queue.put(pending)
        return await pending.done

    async def _publish_terminal(self, pending: _Pending, report: ErrorReport) -> StoredEvent:
        while True:
            if self._closing:
                raise StoreClosedError("the event store is not accepting events")
            claim, owner = self._terminal.claim(report)
            if owner:
                break
            receipt = await asyncio.shield(claim.receipt)
            if receipt is not None:
                return receipt
        pending.terminal = True
        self._queue.put_nowait(pending)
        try:
            receipt = await asyncio.shield(pending.done)
        except asyncio.CancelledError:
            if not pending.committing:
                pending.done.cancel()
                self._terminal.release(claim)
            else:
                # SQL already owns the transaction. Its eventual committed receipt wins;
                # never release a claim while an unstoppable insert can still commit.
                pending.done.add_done_callback(lambda done: self._terminal_receipt(claim, done))
            raise
        except BaseException:
            self._terminal.release(claim)
            raise
        self._terminal.acknowledge(claim, receipt)
        return receipt

    def _terminal_receipt(
        self, claim: TerminalClaim[StoredEvent], done: asyncio.Future[StoredEvent]
    ) -> None:
        if done.cancelled() or done.exception() is not None:
            self._terminal.release(claim)
        else:
            self._terminal.acknowledge(claim, done.result())

    @property
    def last_seq(self) -> int:
        return len(self._events)

    def committed_events(self) -> tuple[ServerEvent, ...]:
        """Independent envelopes from this run, after SQL commit only; no disk IO."""
        return tuple(event.model_copy(deep=True) for event in self._reference_events.values())

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
        batch: list[_Pending] = []
        try:
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
                batch = []
        except BaseException as error:
            self._closing = True
            while not self._queue.empty():
                pending = self._queue.get_nowait()
                if pending is not None:
                    batch.append(pending)
            for pending in batch:
                if not pending.done.done():
                    pending.done.set_exception(StoreClosedError(f"event writer stopped: {error}"))
            self._closed = True
            self._notify()

    def _validate_provider_mode(self, event: ServerEvent) -> None:
        if (
            event.type == "change.reviewed"
            and event.payload.reviewer_provider == "fake"
            and not self.allow_fake_provider
        ):
            raise ValueError("fake review provider requires explicit server authorization")

    async def _commit(self, batch: list[_Pending]) -> None:
        rows: list[StoredEvent] = []
        accepted: list[tuple[_Pending, ServerEvent]] = []
        # Batch-local candidates can reference earlier accepted candidates without copying the
        # committed index. Only a successful SQL transaction extends the store's index.
        staged: dict[int, ServerEvent] = {}
        prior = ChainMap(staged, self._reference_events)
        for pending in batch:
            if pending.terminal and pending.done.cancelled():
                continue
            try:
                seq = len(self._events) + len(rows) + 1
                ts = pending.ts or format_ts(self._clock())
                event = _SERVER_EVENT_ADAPTER.validate_python(
                    {
                        "seq": seq,
                        "ts": ts,
                        "type": pending.type,
                        "payload": json.loads(pending.payload_json),
                    },
                    context={"live": True} if self.mode == "live" else None,
                )
                self._validate_provider_mode(event)
                validate_references(event, prior)
                row = StoredEvent(seq, ts, pending.type, event.model_dump_json())
            except (ValueError, TypeError, KeyError) as error:
                if not pending.done.done():
                    pending.done.set_exception(EventRejected(str(error)))
                continue
            pending.committing = True
            rows.append(row)
            accepted.append((pending, event))
            staged[seq] = event
        if not rows:
            return
        try:
            await asyncio.to_thread(self._insert, rows)
        except Exception as error:
            # SQL failure belongs to the accepted transaction, not to rejected candidates.
            for pending, _ in accepted:
                if not pending.done.done():
                    pending.done.set_exception(error)
            return
        for (pending, event), row in zip(accepted, rows, strict=True):
            self._events.append(row)
            self._reference_events[row.seq] = event
            if event.type == "graph.snapshot":
                self._graph = event.payload
            elif event.type == "graph.delta" and self._graph is not None:
                self._graph = apply_delta(self._graph, event.payload)
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
