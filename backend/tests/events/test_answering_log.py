"""Historical provenance survives SQL, export, reconnect and playback without admission."""

import asyncio
import json
import threading
from pathlib import Path

import pytest
from answering_helpers import origin, resolution

from oversight.events import EventRejected, EventStore, export_run, play, read_jsonl, read_store
from oversight.settings import REPO_ROOT
from oversight_contracts.events import AgentStatus, ChangeResolved, ErrorReport

pytestmark = pytest.mark.anyio
RUN = read_jsonl(REPO_ROOT / "fixtures/events/handwritten-level1.jsonl")[0]
assert RUN.type == "run.started"
RUN_ID = RUN.payload.run_id


async def test_sql_export_replay_and_reconnect_keep_the_whole_reference(tmp_path: Path) -> None:
    async with EventStore(tmp_path / "live.sqlite", RUN_ID) as store:
        await store.publish(RUN.payload)
        ref = await origin(store)
        expected = ref.model_dump(mode="json")
        # The source closes; subsequent statuses still name their historical origin.
        await store.publish(ChangeResolved.model_validate(resolution()))
        for state in (
            "starting",
            "working",
            "ready_for_review",
            "revising",
            "halted",
            "stopped",
            "done",
        ):
            await store.publish(
                AgentStatus.model_validate(
                    dict(
                        agent_id="agent-1",
                        change_id="change-1",
                        slot="A",
                        task="answer",
                        model="fake",
                        state=state,
                        step=1,
                        max_steps=30,
                        detail=None,
                        base_commit="base",
                        answering=ref,
                    )
                )
            )
    assert await asyncio.to_thread(export_run, store.path, tmp_path / "run.jsonl") == store.last_seq
    events = await asyncio.to_thread(read_jsonl, tmp_path / "run.jsonl")
    async with EventStore(tmp_path / "replay.sqlite", RUN_ID, mode="replay") as replay:
        await play(replay, events, speed=0)
    rows = await asyncio.to_thread(read_store, replay.path)
    statuses = [json.loads(row.envelope)["payload"] for row in rows if row.type == "agent.status"]
    assert all(p["answering"] == expected for p in statuses if p["agent_id"] == "agent-1")
    reconnect = [row async for row in replay.subscribe(after=4)]
    assert [row.envelope for row in reconnect] == [row.envelope for row in rows[4:]]
    # Public readers cannot mutate the store's evidence or exported envelopes.
    copy = replay.committed_events()
    object.__setattr__(copy[0], "seq", 999)
    assert replay.committed_events()[0].seq == 1


async def test_pending_and_rejected_events_are_not_confirmed_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with EventStore(tmp_path / "run.sqlite", RUN_ID) as store:
        await store.publish(RUN.payload)
        real_insert = store._insert  # pyright: ignore[reportPrivateUsage]
        entered, release = threading.Event(), threading.Event()

        def held_insert(rows: object) -> None:
            entered.set()
            assert release.wait(10)
            real_insert(rows)  # pyright: ignore[reportArgumentType]

        monkeypatch.setattr(store, "_insert", held_insert)
        pending = asyncio.create_task(
            store.publish(
                ErrorReport(
                    source="server", message="pending", agent_id=None, change_id=None, fatal=False
                )
            )
        )
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            assert len(store.committed_events()) == 1
        finally:
            release.set()
        receipt = await pending
        assert store.committed_events()[-1].seq == receipt.seq
        with pytest.raises(EventRejected):
            await store.publish(ErrorReport.model_construct(source="invalid"))
        assert len(store.committed_events()) == 2


async def test_old_recording_still_plays_with_null_answering(tmp_path: Path) -> None:
    events = read_jsonl(REPO_ROOT / "fixtures/events/handwritten-level1.jsonl")
    async with EventStore(tmp_path / "old.sqlite", RUN_ID, mode="replay") as store:
        await play(store, events, speed=0)
    assert all(
        event.payload.answering is None
        for event in store.committed_events()
        if event.type == "agent.status"
    )
