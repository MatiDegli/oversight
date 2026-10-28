"""Live target arbitration and cancellation, without rewriting historical replay."""

import asyncio
import json
import threading
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from oversight.events import (
    EventStore,
    NeedsYouWait,
    StoreClosedError,
    StoredEvent,
    format_needs_you,
    play,
    read_store,
)
from oversight_contracts.events import ErrorReport, ServerEvent

pytestmark = pytest.mark.anyio


def report(code: str = "policy_invalid", **patch: object) -> ErrorReport:
    return ErrorReport.model_validate(
        dict(
            source="server",
            change_id="change-1",
            agent_id="agent-1",
            fatal=False,
            message=format_needs_you(1, "head", code, "Explain."),
            **patch,
        )
    )


async def test_different_codes_share_one_committed_receipt(tmp_path: Path) -> None:
    async with EventStore(tmp_path / "live.sqlite", "run-20261028-120000") as store:
        one, two = await asyncio.gather(store.publish(report()), store.publish(report("ledger")))
        assert one == two and store.last_seq == 1
        assert await store.publish(report()) == one
        assert await store.publish(report("cancelled")) == one
        other = report("ledger").model_copy(update={"change_id": "change-2"})
        assert (await store.publish(other)).seq == 2
        assert (
            await store.publish(report().model_copy(update={"message": "ordinary error"}))
        ).seq == 3
        assert (
            await store.publish(report().model_copy(update={"message": "ordinary error"}))
        ).seq == 4
    assert len(read_store(store.path)) == 4


async def test_cancelled_winner_releases_before_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with EventStore(tmp_path / "cancel.sqlite", "run-20261028-120000") as store:
        entered, release = asyncio.Event(), asyncio.Event()
        commit = store._commit

        async def held(batch):
            entered.set()
            await release.wait()
            await commit(batch)

        monkeypatch.setattr(store, "_commit", held)
        winner = asyncio.create_task(store.publish(report()))
        await entered.wait()
        loser = asyncio.create_task(store.publish(report("ledger")))
        await asyncio.sleep(0)
        winner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await winner
        release.set()
        receipt = await loser
        assert receipt.seq == 1 and "code=ledger;" in receipt.envelope
        assert store.last_seq == 1


async def test_sql_failure_releases_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with EventStore(tmp_path / "fail.sqlite", "run-20261028-120000") as store:
        insert = store._insert

        def fail(rows):
            raise OSError("injected disk failure")

        monkeypatch.setattr(store, "_insert", fail)
        with pytest.raises(OSError, match="disk failure"):
            await store.publish(report())
        monkeypatch.setattr(store, "_insert", insert)
        assert (await store.publish(report("ledger"))).seq == 1


async def test_cancel_during_sql_keeps_the_committed_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with EventStore(tmp_path / "sql.sqlite", "run-20261028-120000") as store:
        entered, release = threading.Event(), threading.Event()
        insert = store._insert

        def held(rows):
            entered.set()
            assert release.wait(5)
            insert(rows)

        monkeypatch.setattr(store, "_insert", held)
        winner = asyncio.create_task(store.publish(report()))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            winner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await winner
            loser = asyncio.create_task(store.publish(report("ledger")))
            release.set()
            assert "code=policy_invalid;" in (await loser).envelope
            assert store.last_seq == 1
        finally:
            release.set()


async def test_replay_retains_duplicate_envelopes_and_wait_generation(tmp_path: Path) -> None:
    async with EventStore(
        tmp_path / "replay.sqlite", "run-20261028-120000", mode="replay"
    ) as store:
        rows: list[StoredEvent] = []
        for payload in (report(), report("ledger"), report()):
            rows.append(await store.publish(payload, ts="2026-10-28T12:00:00.000Z"))
    assert read_store(store.path) == rows and [row.seq for row in rows] == [1, 2, 3]
    assert [json.loads(row.envelope)["payload"] for row in rows] == [
        payload.model_dump() for payload in (report(), report("ledger"), report())
    ]
    wait = NeedsYouWait("change-1", 1, "head", 9)
    assert wait.accepts(report("ledger"), generation=9)
    assert not wait.accepts(report(), generation=10)

    events = [TypeAdapter(ServerEvent).validate_json(row.envelope) for row in rows]
    async with EventStore(tmp_path / "copy.sqlite", "run-20261028-120000", mode="replay") as copy:
        await play(copy, events, speed=0)
    assert read_store(copy.path) == rows


async def test_writer_failure_releases_waiters_without_requeueing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with EventStore(tmp_path / "closed.sqlite", "run-20261028-120000") as store:

        async def fail(batch):
            raise RuntimeError("writer stopped")

        monkeypatch.setattr(store, "_commit", fail)
        async with asyncio.timeout(1):
            results = await asyncio.gather(
                store.publish(report()), store.publish(report("ledger")), return_exceptions=True
            )
        assert all(isinstance(result, StoreClosedError) for result in results)
