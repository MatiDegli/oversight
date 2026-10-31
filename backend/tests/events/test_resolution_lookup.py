"""Exact resolution lookup sees only this run's acknowledged SQL horizon."""

import asyncio
import sqlite3
import threading

import pytest

from oversight.events import EventStore, read_jsonl, resolution_at
from oversight.settings import REPO_ROOT
from oversight_contracts.events import ChangeResolved

pytestmark = pytest.mark.anyio


def resolution(change_id="change-1"):
    return ChangeResolved(
        change_id=change_id,
        agent_id="agent-1",
        revision=1,
        decision="approved",
        reason=None,
        comment="Exact stored text: ñ",
        merge="merged",
        merge_commit="a" * 40,
        conflict=None,
        decided_by="person",
        head_commit="b" * 40,
    )


async def test_lookup_returns_the_exact_committed_row(tmp_path):
    async with EventStore(tmp_path / "run.sqlite", "run-20261027-140312") as store:
        committed = await store.publish(resolution())
        assert await resolution_at(store, "change-1", committed.seq) == committed


async def test_lookup_refuses_other_types_changes_and_seqs(tmp_path):
    started = read_jsonl(REPO_ROOT / "fixtures/events/handwritten-level1.jsonl")[0]
    async with EventStore(
        tmp_path / "run.sqlite", "run-20261027-140312", allow_fake_provider=True
    ) as store:
        other_type = await store.publish(started.payload)
        other_change = await store.publish(resolution("change-2"))
        for seq in (other_type.seq, other_change.seq, 0, store.last_seq + 1):
            assert await resolution_at(store, "change-1", seq) is None


async def test_lookup_ignores_rows_above_the_horizon(tmp_path, monkeypatch):
    async with EventStore(tmp_path / "run.sqlite", "run-20261027-140312") as store:
        first = await store.publish(resolution())
        insert = store._insert
        entered, release = threading.Event(), threading.Event()

        def held(rows):
            insert(rows)  # Committed on disk, but the writer has not acknowledged it.
            entered.set()
            assert release.wait(10)

        monkeypatch.setattr(store, "_insert", held)
        pending = asyncio.create_task(store.publish(resolution()))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            with sqlite3.connect(store.path) as db:
                assert db.execute("SELECT max(seq) FROM events").fetchone()[0] == first.seq + 1
            assert store.last_seq == first.seq
            assert await resolution_at(store, "change-1", first.seq + 1) is None
            assert await resolution_at(store, "change-1", first.seq) == first
        finally:
            release.set()
            second = await pending
        assert await resolution_at(store, "change-1", second.seq) == second
