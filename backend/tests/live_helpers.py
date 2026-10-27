"""Helpers for the tests that run the live app on a real uvicorn server, in the test's own loop:
settings with a runtime in `tmp_path`, the server, waits on events, and scripted model clients."""

import asyncio
import contextlib
import json
import os
import socket
import sys
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from pydantic import TypeAdapter

from oversight.agents import (
    FAKE_MODEL,
    Message,
    ModelClient,
    ModelReply,
    Script,
    ScriptedModel,
    ToolSchema,
    load_script,
    parse_script,
)
from oversight.app import ClientFactory, LiveRun, create_app
from oversight.events import EventStore
from oversight.settings import REPO_ROOT, Settings
from oversight_contracts.commands import ErrorResponse
from oversight_contracts.common import Slot
from oversight_contracts.events import ModelProfile, ServerEvent

DEADLINE_S = 60
SERVER_EVENT: TypeAdapter[Any] = TypeAdapter(ServerEvent)
FAKE_SCRIPT = REPO_ROOT / "fixtures" / "fake-model" / "document-apply-coupon.json"
FAKE_SCRIPT_B = REPO_ROOT / "fixtures" / "fake-model" / "document-restock.json"
LAYOUT = REPO_ROOT / "fixtures" / "layouts" / "demo-repo.json"


def live_settings(tmp_path: Path, **overrides: Any) -> Settings:
    """The fake model, the demo repo, the preset layout, this interpreter for the tests (no venv
    build), and a runtime directory inside `tmp_path`."""
    values: dict[str, Any] = {
        "runtime_dir": tmp_path / ".runtime",
        "layout": LAYOUT,
        "agent_model": "fake",
        "fake_script": FAKE_SCRIPT,
        "demo_python": Path(sys.executable),
    }
    return Settings(**(values | overrides))


def two_fakes(tmp_path: Path, **overrides: Any) -> Settings:
    """Both slots on the fake model, each with its own script."""
    return live_settings(tmp_path, fake_script_b=FAKE_SCRIPT_B, **overrides)


def meta_settings(tmp_path: Path, **overrides: Any) -> Settings:
    """The Meta profile with a fake key and the documented budget and prices. Tests pair it with a
    scripted client factory: nothing reaches Meta (and the root conftest blocks it anyway)."""
    values: dict[str, Any] = {
        "agent_model": "muse-spark",
        "meta_api_key": "test-key",
        "budget_usd": 150.0,
        "input_usd_per_m": 0.10,
        "output_usd_per_m": 0.20,
    }
    return live_settings(tmp_path, **(values | overrides))


def script_json(task: str, turns: list[dict[str, Any]]) -> str:
    return json.dumps({"task": task, "turns": turns})


async def start(s: "LiveServer", **body: Any) -> httpx.Response:
    """POST /agents with the task "t" and `body`'s keys."""
    return await s.client.post("/agents", json={"task": "t", **body})


class Answer(Protocol):
    """What `error_code` reads: an httpx response, or the test client's."""

    status_code: int

    def json(self) -> Any: ...


def error_code(response: Answer) -> tuple[int, str]:
    """The status and the contract's error code of a refused request."""
    return response.status_code, ErrorResponse.model_validate(response.json()).error.code


def write_ledger(settings: Settings, body: object) -> None:
    settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    text = body if isinstance(body, str) else json.dumps(body)
    (settings.runtime_dir / "ledger.json").write_text(text)


def read_ledger(settings: Settings) -> dict[str, Any]:
    return json.loads((settings.runtime_dir / "ledger.json").read_text())


class Scripts:
    """A client factory that hands out one scripted model per start, in order, and counts calls.
    `built` lists what each start asked for: the profile's id and the slot."""

    def __init__(self, scripts: Sequence[str] = (), profile: ModelProfile = FAKE_MODEL) -> None:
        self._scripts = [parse_script(text) for text in scripts] or [load_script(FAKE_SCRIPT)]
        self._profile = profile
        self.calls = 0
        self.built: list[tuple[str, Slot]] = []

    def __call__(self, profile: ModelProfile, slot: Slot) -> ModelClient:
        script = self._scripts[min(self.calls, len(self._scripts) - 1)]
        self.calls += 1
        self.built.append((profile.id, slot))
        return ScriptedModel(script, self._profile)


class BySlot:
    """A client factory that hands each slot its own client, built at the start by `make`; `built`
    lists what each start asked for, as `Scripts.built` does."""

    def __init__(self, make: Callable[[ModelProfile, Slot], ModelClient]) -> None:
        self._make = make
        self.built: list[tuple[str, Slot]] = []

    def __call__(self, profile: ModelProfile, slot: Slot) -> ModelClient:
        self.built.append((profile.id, slot))
        return self._make(profile, slot)


@dataclass
class GatedModel:
    """A model whose every call waits for `gate`; `entered` is set once a call is waiting."""

    profile: ModelProfile = FAKE_MODEL
    gate: asyncio.Event = field(default_factory=asyncio.Event)
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)

    async def complete(
        self, messages: Sequence[Message], tools: Sequence[ToolSchema]
    ) -> ModelReply:
        self.entered.set()
        try:
            await self.gate.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return ModelReply(
            text=None,
            tool_calls=[],
            model=self.profile.model,
        )


class Stepped:
    """A scripted model whose every call waits until the test lets one call through."""

    def __init__(self, script: Script, profile: ModelProfile = FAKE_MODEL) -> None:
        self._inner = ScriptedModel(script, profile)
        self.permits = asyncio.Semaphore(0)

    @property
    def profile(self) -> ModelProfile:
        return self._inner.profile

    async def complete(
        self, messages: Sequence[Message], tools: Sequence[ToolSchema]
    ) -> ModelReply:
        await self.permits.acquire()
        return await self._inner.complete(messages, tools)


@dataclass
class LiveServer:
    app: FastAPI
    client: httpx.AsyncClient
    server: uvicorn.Server
    task: "asyncio.Task[None]"

    @property
    def store(self) -> EventStore:
        return self.app.state.store

    @property
    def live(self) -> LiveRun:
        return self.app.state.live

    async def until(self, predicate: Callable[[Any], bool], after: int = 0) -> list[Any]:
        """The events after `after`, each validated against `ServerEvent`, up to and including
        the first that matches."""
        seen: list[Any] = []
        async for stored in self.store.subscribe(after):
            event = SERVER_EVENT.validate_json(stored.envelope)
            seen.append(event)
            if predicate(event):
                return seen
        raise AssertionError("the store closed before the event came")

    def events(self) -> list[Any]:
        return [SERVER_EVENT.validate_json(e.envelope) for e in self.store._events]  # pyright: ignore[reportPrivateUsage]

    async def stop(self) -> None:
        self.server.should_exit = True
        await asyncio.wait_for(asyncio.shield(self.task), DEADLINE_S)


def is_type(kind: str, **payload: Any) -> Callable[[Any], bool]:
    def match(event: Any) -> bool:
        if event.type != kind:
            return False
        return all(getattr(event.payload, k) == v for k, v in payload.items())

    return match


@contextlib.asynccontextmanager
async def live_server(
    settings: Settings, client_factory: ClientFactory | None = None
) -> AsyncIterator[LiveServer]:
    """Uvicorn on a socket bound before it starts, so requests wait for the lifespan's startup."""
    app = create_app(settings, client_factory=client_factory)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=1))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", timeout=DEADLINE_S
        ) as client:
            running = LiveServer(app, client, server, task)
            assert (await client.get("/health")).status_code == 200
            yield running
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, DEADLINE_S)
        finally:
            sock.close()


FAKE_UV = """#!{python}
import os, socket, sys
if len(sys.argv) > 2 and sys.argv[2] == "venv":
    os.makedirs(sys.argv[-1], exist_ok=True)
connection = socket.create_connection(("127.0.0.1", {port}))
connection.sendall(f"{{os.getpid()}}\\n".encode())
connection.recv(1)  # blocks until the test lets it go, or kills it
"""


class FakeUv:
    """A `uv` first on PATH that reports its pid to a loopback server, then blocks until the test
    sends it a byte. Released ones exit 0, so the venv build goes on to its next step. Leaving the
    context closes every connection, so the server's own close does not wait on a dead child."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.pids: asyncio.Queue[int] = asyncio.Queue()
        self.release_all = False
        self._writers: list[asyncio.StreamWriter] = []
        self._tmp_path = tmp_path
        self._monkeypatch = monkeypatch
        self._server: asyncio.Server | None = None

    async def __aenter__(self) -> "FakeUv":
        self._server = await asyncio.start_server(self._connected, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        bin_dir = self._tmp_path / "bin"
        bin_dir.mkdir()
        uv = bin_dir / "uv"
        uv.write_text(FAKE_UV.format(python=sys.executable, port=port))
        uv.chmod(0o755)
        self._monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        return self

    async def __aexit__(self, *_: object) -> None:
        for writer in self._writers:
            writer.close()
        if self._server is not None:
            self._server.close()
            await asyncio.wait_for(self._server.wait_closed(), DEADLINE_S)

    async def _connected(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        pid = int((await reader.readline()).decode())
        self._writers.append(writer)
        await self.pids.put(pid)
        if self.release_all:
            await self.release()

    async def release(self) -> None:
        self.release_all = True
        for writer in self._writers:
            if not writer.is_closing():
                writer.write(b"x")
                await writer.drain()
                writer.close()
