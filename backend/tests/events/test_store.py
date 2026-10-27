"""The event store: seq from the single writer, subscriptions, the graph fold, import and export."""

import asyncio
import contextlib
import json
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from oversight.events import (
    EventStore,
    StoreClosedError,
    StoredEvent,
    export_run,
    read_jsonl,
    read_store,
)
from oversight_contracts.events import AgentStep, RunStarted
from oversight_contracts.graph import GraphDelta, GraphNode, GraphSnapshot

pytestmark = pytest.mark.anyio
DEADLINE_S = 10
FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "events"
RUN_ID = "run-20261027-140312"


class StepClock:
    """A clock that moves 1.5 ms per reading, so every `ts` is known in advance."""

    def __init__(self) -> None:
        self.now = datetime(2026, 10, 27, 14, 3, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(microseconds=1500)
        return self.now


def step(agent: int, number: int) -> AgentStep:
    return AgentStep(
        agent_id=f"agent-{agent}",
        step=number,
        tool="read_file",
        summary=f"Read step {number}",
        result="ok",
        error=None,
        duration_ms=1,
        tests=None,
    )


def node(node_id: str, end_line: int = 5) -> GraphNode:
    module, _, name = node_id.partition(":")
    return GraphNode(
        id=node_id,
        kind="function" if name else "module",
        name=name or module.rsplit(".", 1)[-1],
        qualname=name or module,
        path=module.replace(".", "/") + ".py",
        start_line=1,
        end_line=end_line,
        parent_id=module if name else None,
        is_test=False,
    )


def snapshot(*nodes: GraphNode) -> GraphSnapshot:
    return GraphSnapshot(
        version="v0", commit="c0", nodes=list(nodes), edges=[], parse_errors=[], positions={}
    )


def delta(
    added: list[GraphNode], changed: list[GraphNode], removed: list[str], version: str = "v1"
) -> GraphDelta:
    return GraphDelta(
        from_version="v0",
        to_version=version,
        commit="c1",
        cause="merge",
        nodes_added=added,
        nodes_changed=changed,
        nodes_removed=removed,
        edges_added=[],
        edges_changed=[],
        edges_removed=[],
        parse_errors=[],
    )


def open_store(tmp_path: Path, clock: StepClock | None = None) -> EventStore:
    return EventStore(
        tmp_path / "runs" / f"{RUN_ID}.sqlite", RUN_ID, clock or StepClock(), mode="replay"
    )


async def test_two_concurrent_writers_get_strictly_increasing_seq_with_no_gaps(
    tmp_path: Path,
) -> None:
    async with open_store(tmp_path) as store:

        async def producer(agent: int) -> list[int]:
            return [(await store.publish(step(agent, n))).seq for n in range(1, 101)]

        first, second = await asyncio.gather(producer(1), producer(2))

    assert first == sorted(first) and second == sorted(second)
    assert sorted(first + second) == list(range(1, 201))
    # The two really interleaved: neither producer got one solid block of numbers.
    assert first != list(range(first[0], first[0] + 100))
    rows = read_store(store.path)
    assert [row.seq for row in rows] == list(range(1, 201))
    for row in rows:
        envelope = json.loads(row.envelope)
        assert (envelope["seq"], envelope["ts"], envelope["type"]) == (row.seq, row.ts, row.type)
    # Each agent's own events keep the order it published them in.
    for agent in (1, 2):
        steps = [
            json.loads(row.envelope)["payload"]["step"]
            for row in rows
            if f'"agent-{agent}"' in row.envelope
        ]
        assert steps == list(range(1, 101))


async def test_ts_comes_from_the_injected_clock_unless_given(tmp_path: Path) -> None:
    async with open_store(tmp_path) as store:
        first = await store.publish(step(1, 1))
        second = await store.publish(step(1, 2), ts="2026-10-27T15:00:00.250Z")
        with pytest.raises(ValueError, match="ts must look like"):
            await store.publish(step(1, 3), ts="2026-10-27 15:00:00")
    assert first.ts == "2026-10-27T14:03:12.001Z"
    assert second.ts == "2026-10-27T15:00:00.250Z"
    assert store.last_seq == 2


async def test_a_subscriber_gets_stored_events_after_its_seq_then_live_ones(
    tmp_path: Path,
) -> None:
    async with open_store(tmp_path) as store:
        for n in range(1, 4):
            await store.publish(step(1, n))
        subscription = store.subscribe(after=1)
        assert [(await anext(subscription)).seq for _ in range(2)] == [2, 3]
        waiting = asyncio.ensure_future(anext(subscription))
        published = await store.publish(step(1, 4))
        assert (await waiting) == published
    # Closing the store ends every subscription.
    with pytest.raises(StopAsyncIteration):
        await anext(subscription)


async def test_a_subscriber_ahead_of_the_log_waits_for_its_next_seq(tmp_path: Path) -> None:
    async with open_store(tmp_path) as store:
        await store.publish(step(1, 1))
        waiting = asyncio.ensure_future(anext(store.subscribe(after=2)))
        await store.publish(step(1, 2))
        assert not waiting.done()
        third = await store.publish(step(1, 3))
        assert (await waiting) == third


async def test_the_graph_is_read_with_the_seq_it_reflects(tmp_path: Path) -> None:
    async with open_store(tmp_path) as store:
        assert store.graph() == (0, None)
        await store.publish(snapshot(node("shop.a"), node("shop.a:f"), node("shop.a:g")))
        await store.publish(step(1, 1))
        await store.publish(
            delta(
                added=[node("shop.a:h")],
                changed=[node("shop.a:f", end_line=9)],
                removed=["shop.a:g"],
            )
        )
        seq, graph = store.graph()
    assert seq == 3
    assert graph is not None and graph.version == "v1" and graph.commit == "c1"
    assert [(n.id, n.end_line) for n in graph.nodes] == [
        ("shop.a", 5),
        ("shop.a:f", 9),
        ("shop.a:h", 5),
    ]


async def test_a_run_started_for_another_run_is_refused(tmp_path: Path) -> None:
    started = read_jsonl(FIXTURES / "handwritten-level1.jsonl")[0].payload
    assert isinstance(started, RunStarted)
    async with EventStore(tmp_path / "x.sqlite", "run-20990101-000000", mode="replay") as store:
        with pytest.raises(ValueError, match="this log is run-20990101-000000"):
            await store.publish(started)
    assert store.last_seq == 0


async def test_a_run_file_is_never_reopened_and_a_closed_store_refuses_events(
    tmp_path: Path,
) -> None:
    store = open_store(tmp_path)
    async with store:
        await store.publish(step(1, 1))
    with pytest.raises(StoreClosedError):
        await store.publish(step(1, 2))
    with pytest.raises(FileExistsError):
        await open_store(tmp_path).start()


async def test_the_file_uses_wal_and_a_busy_timeout(tmp_path: Path) -> None:
    async with open_store(tmp_path) as store:
        await store.publish(step(1, 1))
        connection = sqlite3.connect(store.path)
        try:
            assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        finally:
            connection.close()
        assert store._connection is not None  # pyright: ignore[reportPrivateUsage]
        timeout = store._connection.execute("PRAGMA busy_timeout").fetchone()  # pyright: ignore[reportPrivateUsage]
        assert timeout == (5000,)


async def test_a_log_round_trips_through_the_store_with_serialized_defaults(tmp_path: Path) -> None:
    log = FIXTURES / "handwritten-level1.jsonl"
    async with open_store(tmp_path) as store:
        for event in read_jsonl(log):
            await store.publish(event.payload, ts=event.ts)
    out = tmp_path / "exported.jsonl"
    assert export_run(store.path, out) == len(read_jsonl(log))
    expected = "".join(event.model_dump_json() + "\n" for event in read_jsonl(log))
    assert out.read_text(encoding="utf-8") == expected


async def test_close_can_be_repeated(tmp_path: Path) -> None:
    store = open_store(tmp_path)
    await store.start()
    await store.publish(step(1, 1))
    await store.close()
    await store.close()
    assert [row.seq for row in read_store(store.path)] == [1]


async def test_a_cancelled_close_still_commits_what_is_queued_and_a_retry_finishes_it(
    tmp_path: Path,
) -> None:
    store = open_store(tmp_path)
    await store.start()
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    insert = store._insert  # pyright: ignore[reportPrivateUsage]

    def blocked_insert(rows: list[StoredEvent]) -> None:
        # Runs in the writer's worker thread: tell the loop it is here, then wait to be let go.
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(DEADLINE_S)
        insert(rows)

    store._insert = blocked_insert  # type: ignore[method-assign]  # pyright: ignore[reportPrivateUsage]
    publisher = asyncio.create_task(store.publish(step(1, 1)))
    try:
        async with asyncio.timeout(DEADLINE_S):
            await entered.wait()
        closer = asyncio.create_task(store.close())
        await asyncio.sleep(0)  # the closer's first step: it is now waiting for the writer
        with pytest.raises(StoreClosedError):
            await store.publish(step(1, 2))
        closer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await closer
    finally:
        release.set()
    async with asyncio.timeout(DEADLINE_S):
        await store.close()
        committed = await publisher
    assert committed.seq == 1
    # SQLite removes the write-ahead log when its last connection closes: the file is closed.
    assert not store.path.with_name(store.path.name + "-wal").exists()
    assert [row.seq for row in read_store(store.path)] == [1]


async def test_the_file_checks_and_creation_run_off_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    on_loop = spy_on_loop_thread_file_calls(monkeypatch, tmp_path)
    async with open_store(tmp_path):
        pass
    assert on_loop == []


def spy_on_loop_thread_file_calls(monkeypatch: pytest.MonkeyPatch, root: Path) -> list[str]:
    """Record every `Path.exists`, `mkdir` and `unlink` under `root` made on this thread."""
    loop_thread = threading.get_ident()
    on_loop: list[str] = []

    def watch(name: str, original: Callable[..., object]) -> Callable[..., object]:
        def watched(self: Path, *args: object, **kwargs: object) -> object:
            if threading.get_ident() == loop_thread and self.is_relative_to(root):
                on_loop.append(f"{name} {self.name}")
            return original(self, *args, **kwargs)

        return watched

    for name in ("exists", "mkdir", "unlink"):
        monkeypatch.setattr(Path, name, watch(name, getattr(Path, name)))
    return on_loop
