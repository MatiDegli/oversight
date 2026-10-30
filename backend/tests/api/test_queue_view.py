"""Committed queue reads work in replay; queue commands refuse bodies before parsing."""

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from oversight.api import create_router, install_error_handlers
from oversight.changes import read_queue
from oversight.events import EventStore, play, read_jsonl
from oversight.settings import REPO_ROOT
from oversight_contracts.queue import QueuedTask, QueueSnapshot

pytestmark = pytest.mark.anyio


async def test_replay_queue_reads_snapshot_and_item_at_committed_seq(tmp_path: Path) -> None:
    events = read_jsonl(REPO_ROOT / "fixtures/events/handwritten-queue.jsonl")
    async with EventStore(
        tmp_path / "queue.sqlite", "run-20261027-140312", mode="replay", allow_fake_provider=True
    ) as store:
        app = FastAPI()
        app.state.store = store
        app.include_router(create_router(store, "replay"))
        install_error_handlers(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            assert (await client.get("/queue")).json()["tasks"] == []
            await play(store, events, speed=0)
            response = await client.get("/queue")
            assert response.status_code == 200
            snapshot = QueueSnapshot.model_validate(response.json())
            assert snapshot == read_queue(store)
            assert snapshot.seq == 5
            assert [(item.revision, item.state) for item in snapshot.tasks] == [
                (2, "blocked"),
                (1, "queued"),
            ]
            single = await client.get("/queue/queued-task-1")
            assert QueuedTask.model_validate(single.json()) == snapshot.tasks[0]
            assert (await client.get("/queue/plan-1")).status_code == 404
            for path in ["/queue", "/queue/queued-task-1/cancel"]:
                response = await client.post(path, content=b"malformed json")
                assert response.status_code == 501
            assert read_queue(store) == snapshot
