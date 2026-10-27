"""GET /events, /graph and /health over a real server on a free port (sections 4.6 and 4.7)."""

import asyncio
import contextlib
import json
import socket
import threading
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from oversight.api import create_router, install_error_handlers
from oversight.app import ReplayConfig, create_app
from oversight.events import EventStore, read_jsonl
from oversight.settings import Settings
from oversight_contracts.commands import (
    ERROR_STATUS,
    ErrorCode,
    ErrorResponse,
    GraphResponse,
    Health,
)
from oversight_contracts.events import AgentStep, GraphDeltaEvent
from oversight_contracts.graph import GraphSnapshot

pytestmark = pytest.mark.anyio
LOG = Path(__file__).resolve().parents[3] / "fixtures" / "events" / "handwritten-level1.jsonl"
RUN_ID = "run-20261027-140312"
DEADLINE_S = 10


def step(number: int) -> AgentStep:
    return AgentStep(
        agent_id="agent-1",
        step=number,
        tool="read_file",
        summary=f"Read step {number}",
        result="ok",
        error=None,
        duration_ms=1,
        tests=None,
    )


EMPTY_GRAPH = GraphSnapshot(
    version="v0", commit="c0", nodes=[], edges=[], parse_errors=[], positions={}
)


@contextlib.asynccontextmanager
async def serve(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Uvicorn on a socket bound to a free port before it starts, so no wait for readiness."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=1))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            yield client
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, DEADLINE_S)
        sock.close()


@contextlib.asynccontextmanager
async def store_app(tmp_path: Path) -> AsyncIterator[tuple[EventStore, httpx.AsyncClient]]:
    async with EventStore(tmp_path / f"{RUN_ID}.sqlite", RUN_ID) as store:
        app = FastAPI()
        app.include_router(create_router(store, "live"))
        install_error_handlers(app)
        async with serve(app) as client:
            yield store, client


async def read_messages(
    client: httpx.AsyncClient,
    count: int,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    first_seen: asyncio.Event | None = None,
) -> tuple[httpx.Headers, list[list[str]]]:
    """The first `count` SSE messages, each as its raw lines, within the deadline."""
    messages: list[list[str]] = []
    current: list[str] = []
    async with asyncio.timeout(DEADLINE_S):
        async with client.stream("GET", "/events", params=params, headers=headers) as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                if line:
                    current.append(line)
                    continue
                if current and not current[0].startswith(":"):
                    messages.append(current)
                    if first_seen is not None:
                        first_seen.set()
                current = []
                if len(messages) == count:
                    return response.headers, messages
    raise AssertionError("the stream ended early")


def seqs(messages: list[list[str]]) -> list[int]:
    return [json.loads(m[0].removeprefix("data: "))["seq"] for m in messages]


async def test_events_stream_one_message_per_event_with_id_and_data_only(
    tmp_path: Path,
) -> None:
    async with store_app(tmp_path) as (store, client):
        stored = [await store.publish(step(n)) for n in (1, 2)]
        headers, messages = await read_messages(client, 2)
    assert messages == [[f"data: {e.envelope}", f"id: {e.seq}"] for e in stored]
    assert headers["content-type"].startswith("text/event-stream")
    assert headers["cache-control"] == "no-cache, no-transform"
    assert headers["x-accel-buffering"] == "no"
    assert "content-encoding" not in headers


async def test_events_resume_after_last_event_id_then_after_then_zero(tmp_path: Path) -> None:
    async with store_app(tmp_path) as (store, client):
        for n in range(1, 6):
            await store.publish(step(n))
        _, by_header = await read_messages(
            client, 2, params={"after": "1"}, headers={"Last-Event-ID": "3"}
        )
        _, by_query = await read_messages(client, 2, params={"after": "2"})
        _, from_start = await read_messages(client, 5)
    assert seqs(by_header) == [4, 5]
    assert seqs(by_query) == [3, 4]
    assert seqs(from_start) == [1, 2, 3, 4, 5]


async def test_events_from_another_run_start_at_seq_1(tmp_path: Path) -> None:
    async with store_app(tmp_path) as (store, client):
        for n in range(1, 4):
            await store.publish(step(n))
        _, other_run = await read_messages(
            client, 3, params={"run": "run-20990101-000000"}, headers={"Last-Event-ID": "2"}
        )
        _, same_run = await read_messages(
            client, 1, params={"run": RUN_ID}, headers={"Last-Event-ID": "2"}
        )
    assert seqs(other_run) == [1, 2, 3]
    assert seqs(same_run) == [3]


async def test_a_waiting_stream_gets_live_events(tmp_path: Path) -> None:
    async with store_app(tmp_path) as (store, client):
        await store.publish(step(1))
        first_seen = asyncio.Event()
        reader = asyncio.create_task(read_messages(client, 3, first_seen=first_seen))
        await first_seen.wait()
        # The stream has sent seq 1 and waits: seq 2 does not exist until this publish.
        second = await store.publish(step(2))
        third = await store.publish(step(3))
        _, messages = await reader
    assert seqs(messages) == [1, second.seq, third.seq]


async def test_bad_resume_points_answer_in_the_error_format(tmp_path: Path) -> None:
    async with store_app(tmp_path) as (_, client):
        negative = await client.get("/events", params={"after": "-1"})
        not_a_seq = await client.get("/events", headers={"Last-Event-ID": "abc"})
        missing = await client.get("/nope")
    for response, status, code in (
        (negative, 422, "invalid_request"),
        (not_a_seq, 422, "invalid_request"),
        (missing, 404, "not_found"),
    ):
        assert response.status_code == status
        assert ErrorResponse.model_validate(response.json()).error.code == code


async def test_only_the_resume_value_that_applies_is_checked(tmp_path: Path) -> None:
    async with store_app(tmp_path) as (store, client):
        for n in range(1, 4):
            await store.publish(step(n))
        # Last-Event-ID wins over `after`, so a bad `after` is not read.
        _, by_header = await read_messages(
            client, 1, params={"after": "-1"}, headers={"Last-Event-ID": "2"}
        )
        # Another run's `run` wins over both, so bad values are not read either.
        _, other_run = await read_messages(
            client, 3, params={"after": "-1", "run": "run-20990101-000000"}
        )
        _, other_run_header = await read_messages(
            client,
            3,
            params={"run": "run-20990101-000000"},
            headers={"Last-Event-ID": "abc"},
        )
        bad = [
            await client.get("/events", params={"after": value}) for value in ("-1", "abc", "1.5")
        ]
        # "²" is a digit to `str.isdigit` but not to `int`.
        bad.append(await client.get("/events", headers={b"Last-Event-ID": b"\xb2"}))
        bad.append(await client.get("/events", headers={"Last-Event-ID": "9" * 5000}))
    assert seqs(by_header) == [3]
    assert seqs(other_run) == [1, 2, 3]
    assert seqs(other_run_header) == [1, 2, 3]
    for response in bad:
        assert response.status_code == 422
        assert ErrorResponse.model_validate(response.json()).error.code == "invalid_request"


async def test_every_error_takes_its_status_from_the_contract_table(tmp_path: Path) -> None:
    async with store_app(tmp_path) as (_, client):
        responses = [
            await client.get("/nope"),  # no such path
            await client.post("/graph"),  # a path, but not this method: 405 in Starlette
            await client.get("/graph"),  # no snapshot yet
            await client.get("/events", params={"after": "-1"}),
        ]
    codes: list[ErrorCode] = [ErrorResponse.model_validate(r.json()).error.code for r in responses]
    expected = [ERROR_STATUS[code] for code in codes]
    assert [r.status_code for r in responses] == expected
    assert codes == ["not_found", "invalid_request", "not_found", "invalid_request"]
    assert responses[1].status_code == 422


async def test_graph_is_not_found_before_a_snapshot_then_read_with_its_seq(
    tmp_path: Path,
) -> None:
    async with store_app(tmp_path) as (store, client):
        before = await client.get("/graph")
        await store.publish(EMPTY_GRAPH)
        await store.publish(step(1))
        after = await client.get("/graph")
        health = await client.get("/health")
    assert before.status_code == 404
    assert ErrorResponse.model_validate(before.json()).error.code == "not_found"
    assert GraphResponse.model_validate(after.json()) == GraphResponse(
        run_id=RUN_ID, seq=2, graph=EMPTY_GRAPH
    )
    assert Health.model_validate(health.json()).mode == "live"


async def test_replay_streams_the_whole_log_and_serves_its_final_graph(tmp_path: Path) -> None:
    events = read_jsonl(LOG)
    app = create_app(Settings(runtime_dir=tmp_path), ReplayConfig(log=LOG, speed=0))
    async with serve(app) as client:
        _, messages = await read_messages(client, len(events))
        graph = GraphResponse.model_validate((await client.get("/graph")).json())
        health = Health.model_validate((await client.get("/health")).json())
    lines = [event.model_dump_json() for event in events]
    assert [m[0].removeprefix("data: ") for m in messages] == lines
    assert (health.mode, health.run_id) == ("replay", RUN_ID)
    # The graph the log reached: the snapshot with the merge's delta applied.
    assert graph.seq == len(events)
    delta = events[-2]
    assert isinstance(delta, GraphDeltaEvent)
    assert graph.graph.version == delta.payload.to_version
    assert graph.graph.commit == delta.payload.commit
    assert (tmp_path / "replays" / f"{RUN_ID}.sqlite").exists()


class PlayerFailedError(RuntimeError):
    pass


async def test_the_replay_store_is_closed_even_when_the_player_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failed = asyncio.Event()
    closes: list[str] = []
    close = EventStore.close

    async def failing_player(*_: object) -> None:
        failed.set()  # set and raise in one step: the task is done before the test resumes
        raise PlayerFailedError("the player failed")

    async def watched_close(self: EventStore) -> None:
        await close(self)
        closes.append(self.run_id)

    monkeypatch.setattr("oversight.app.play", failing_player)
    monkeypatch.setattr(EventStore, "close", watched_close)
    app = create_app(Settings(runtime_dir=tmp_path), ReplayConfig(log=LOG, speed=0))
    with pytest.raises(PlayerFailedError):
        async with app.router.lifespan_context(app):
            async with asyncio.timeout(DEADLINE_S):
                await failed.wait()
    assert closes == [RUN_ID]


async def test_the_replay_lifespan_touches_the_disk_off_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    on_loop = spy_on_loop_thread_file_calls(monkeypatch, tmp_path)
    app = create_app(Settings(runtime_dir=tmp_path), ReplayConfig(log=LOG, speed=0))
    async with app.router.lifespan_context(app):
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
