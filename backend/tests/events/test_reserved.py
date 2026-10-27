"""The single writer and replay player preserve all three newly reserved event payloads."""

import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from oversight.events import EventStore, export_run, play, read_jsonl, read_store
from oversight_contracts.events import ServerEvent

pytestmark = pytest.mark.anyio
ROOT = Path(__file__).resolve().parents[3]


async def test_reserved_events_publish_replay_and_export_without_losing_fields(
    tmp_path: Path,
) -> None:
    adapter = TypeAdapter(ServerEvent)
    events = [read_jsonl(ROOT / "fixtures/events/handwritten-level1.jsonl")[0]]
    examples = json.loads((ROOT / "fixtures/contracts/reserved-1.3.json").read_text())
    events.extend(adapter.validate_python(example) for example in examples)
    async with EventStore(tmp_path / "live.sqlite", "run-20261027-140312", mode="replay") as live:
        for event in events:
            await live.publish(event.payload, ts=event.ts)
        rows = read_store(live.path)
        assert [row.envelope for row in rows] == [event.model_dump_json() for event in events]
    output = tmp_path / "exported.jsonl"
    assert export_run(live.path, output) == 4
    restored = read_jsonl(output)
    assert restored == events
    async with EventStore(
        tmp_path / "replay.sqlite", "run-20261027-140312", mode="replay"
    ) as replay:
        await play(replay, restored, speed=0)
        assert read_store(replay.path) == rows
