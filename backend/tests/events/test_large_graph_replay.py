"""A generated large graph crosses make replay's app, SQLite, GET /graph and SSE."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from oversight.app import ReplayConfig, create_app
from oversight.events import read_jsonl, read_store
from oversight.settings import Settings
from oversight_contracts.graph import GraphNode, GraphSnapshot, Point

LOG = Path(__file__).resolve().parents[3] / "fixtures" / "events" / "handwritten-level1.jsonl"


@pytest.mark.anyio
async def test_generated_two_mib_snapshot_replays_through_app(tmp_path: Path) -> None:
    started = read_jsonl(LOG)[0]
    assert started.type == "run.started"
    graph = GraphSnapshot(
        version="v0",
        commit="c0",
        nodes=[
            GraphNode(
                id=f"module_{i}",
                kind="module",
                name="é" * 400,
                qualname=f"module_{i}",
                path=f"module_{i}.py",
                start_line=1,
                end_line=10,
                parent_id=None,
                is_test=False,
            )
            for i in range(2048)
        ],
        edges=[],
        parse_errors=[],
        positions={f"module_{i}": Point(x=i, y=i) for i in range(2048)},
    )
    snapshot = json.dumps(
        {"seq": 2, "ts": started.ts, "type": "graph.snapshot", "payload": graph.model_dump()},
        ensure_ascii=False,
    )
    assert 2 * 1024 * 1024 < len(snapshot.encode()) < 3 * 1024 * 1024
    log = tmp_path / "generated.jsonl"
    log.write_text(started.model_dump_json() + "\n" + snapshot + "\n", encoding="utf-8")
    # The CLI used by make replay calls this same composition root with ReplayConfig.
    app = create_app(Settings(runtime_dir=tmp_path / ".runtime"), ReplayConfig(log=log, speed=0))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8710"
    ) as client:
        async with app.router.lifespan_context(app):
            stream = asyncio.create_task(client.get("/events"))
            async with asyncio.timeout(10):
                while True:
                    response = await client.get("/graph")
                    assert response.status_code in (200, 404), response.text
                    if response.status_code == 200:
                        break
                    await asyncio.sleep(0.01)
            assert response.json()["seq"] == 2
            assert response.json()["graph"] == graph.model_dump(mode="json")
        # Closing the replay store ends the SSE subscription so ASGITransport can return it.
        sse = await asyncio.wait_for(stream, timeout=10)
    assert sse.status_code == 200
    data = [json.loads(line[6:]) for line in sse.text.splitlines() if line.startswith("data: ")]
    assert [event["seq"] for event in data] == [1, 2]
    assert data[1]["payload"] == graph.model_dump(mode="json")
    rows = read_store(tmp_path / ".runtime" / "replays" / f"{started.payload.run_id}.sqlite")
    assert json.loads(rows[1].envelope)["payload"] == data[1]["payload"]
