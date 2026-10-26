"""The composition root: builds the FastAPI app and wires the modules together."""

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI

from oversight.api import create_router, install_error_handlers
from oversight.events import EventStore, play, read_jsonl
from oversight.settings import Settings
from oversight_contracts.events import RunStartedEvent


@dataclass(frozen=True)
class ReplayConfig:
    log: Path
    speed: float = 1.0
    """1 follows the log's gaps, each capped at 2 seconds; 2 is twice as fast; 0 has no pauses."""


def create_app(settings: Settings | None = None, replay: ReplayConfig | None = None) -> FastAPI:
    """Without `replay`, the live app; its run, store and agents are wired by a later front."""
    settings = settings or Settings.load()
    app = FastAPI(title="Oversight") if replay is None else _replay_app(settings, replay)
    app.state.settings = settings
    install_error_handlers(app)
    return app


def _replay_app(settings: Settings, replay: ReplayConfig) -> FastAPI:
    events = read_jsonl(replay.log)
    if not events or not isinstance(events[0], RunStartedEvent):
        raise ValueError(f"{replay.log.name} must start with run.started")
    run_id = events[0].payload.run_id
    # A replay gets its own file, so replaying an exported run never touches that run's own log.
    path = settings.runtime_dir / "replays" / f"{run_id}.sqlite"
    store = EventStore(path, run_id)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        for stale in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
            stale.unlink(missing_ok=True)
        await store.start()
        player = asyncio.create_task(play(store, events, replay.speed), name="replay-player")
        try:
            yield
        finally:
            player.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await player
            await store.close()

    app = FastAPI(title="Oversight (replay)", lifespan=lifespan)
    app.include_router(create_router(store, "replay"))
    return app
