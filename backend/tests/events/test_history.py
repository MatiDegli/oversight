"""Bounded SQL history: original evidence, fixed horizons and committed-only visibility."""

import asyncio
import json
import sqlite3
import threading
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from oversight.events import (
    EventRejected,
    EventStore,
    HistoryTooLarge,
    read_history,
    read_jsonl,
    read_store,
)
from oversight.events import history as history_module
from oversight.settings import REPO_ROOT
from oversight_contracts.commands import HistoryPage
from oversight_contracts.events import ChangeResolved, ErrorReport, ServerEvent

pytestmark = pytest.mark.anyio
RUN = "run-20261027-140312"
ALLOWED = {
    "change.proposed",
    "change.assessed",
    "change.reviewed",
    "change.facts_recorded",
    "tests.executed",
    "change.resolved",
    "revert.resolved",
    "merge_mode.changed",
    "plan.updated",
    "queue.updated",
    "lead.status",
}


def resolution(comment: str = "ok", *, historical: bool = False) -> ChangeResolved:
    return ChangeResolved(
        change_id="change-1",
        agent_id="agent-1",
        revision=1,
        decision="rejected",
        reason=None,
        comment=comment,
        merge=None,
        merge_commit=None,
        conflict=None,
        decided_by=None if historical else "person",
        head_commit="candidate",
        reasons=["recorded reason"],
    )


def excluded() -> ErrorReport:
    return ErrorReport(
        source="server", message="excluded", agent_id=None, change_id=None, fatal=False
    )


async def page(store: EventStore, **kwargs: int) -> HistoryPage:
    return HistoryPage.model_validate_json(await read_history(store, **kwargs))


async def test_all_allowed_types_and_original_payloads_survive_sql(tmp_path: Path) -> None:
    adapter = TypeAdapter(ServerEvent)
    examples = [
        adapter.validate_python(raw)
        for name in ("reserved-1.4.json", "reserved-1.3.json")
        for raw in json.loads((REPO_ROOT / "fixtures/contracts" / name).read_text())
    ]
    recorded = read_jsonl(REPO_ROOT / "fixtures/events/handwritten-level1.jsonl")
    async with EventStore(
        tmp_path / "run.sqlite", RUN, mode="replay", allow_fake_provider=True
    ) as store:
        for event in [*examples, *recorded]:
            await store.publish(event.payload, ts=event.ts)
        before = read_store(store.path)
        result = await page(store)
        expected = [json.loads(row.envelope) for row in before if row.type in ALLOWED]
        assert result.model_dump(mode="json")["events"] == expected
        assert {event.type for event in result.events} == ALLOWED
        assert result.next_seq is None
        assert result.through_seq == store.last_seq
        assert read_store(store.path) == before
        # Check the exact allowlist against contract acceptance, including every excluded type.
        for event in [*examples, *recorded]:
            if event.type in ALLOWED:
                HistoryPage(run_id=RUN, through_seq=store.last_seq, events=[event], next_seq=None)
            else:
                with pytest.raises(ValidationError):
                    HistoryPage(
                        run_id=RUN, through_seq=store.last_seq, events=[event], next_seq=None
                    )


@pytest.mark.parametrize("limit", [1, 50, 100])
async def test_limits_filter_before_pagination_and_end_with_null(
    tmp_path: Path, limit: int
) -> None:
    async with EventStore(tmp_path / "run.sqlite", RUN) as store:
        expected = []
        for _ in range(101):
            await store.publish(excluded())
            expected.append((await store.publish(resolution())).seq)
        await store.publish(excluded())
        seen: list[int] = []
        after = 0
        while True:
            result = await page(store, limit=limit, after_seq=after)
            seen.extend(event.seq for event in result.events)
            assert len(result.events) <= limit
            if result.next_seq is None:
                break
            assert len(result.events) == limit
            assert result.next_seq == result.events[-1].seq
            after = result.next_seq
        assert seen == expected
        assert result.events[-1].seq < result.through_seq


async def test_frozen_horizon_traversal_with_appends_and_equality(tmp_path: Path) -> None:
    async with EventStore(tmp_path / "run.sqlite", RUN) as store:
        for _ in range(7):
            await store.publish(resolution())
            await store.publish(excluded())
        first = await page(store, limit=2)
        horizon = first.through_seq
        seen = [event.seq for event in first.events]
        after = first.next_seq
        while after is not None:
            await store.publish(resolution("after the horizon"))
            result = await page(store, after_seq=after, through_seq=horizon, limit=2)
            assert result.through_seq == horizon
            seen.extend(event.seq for event in result.events)
            after = result.next_seq
        assert seen == list(range(1, 14, 2))
        empty = await page(store, after_seq=horizon, through_seq=horizon)
        assert empty.events == [] and empty.next_seq is None
        assert empty.through_seq == horizon


@pytest.mark.parametrize("disk_committed", [False, True])
async def test_pending_rejected_and_failed_events_never_enter_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    disk_committed: bool,
) -> None:
    async with EventStore(tmp_path / "run.sqlite", RUN) as store:
        await store.publish(resolution("committed"))
        insert = store._insert  # pyright: ignore[reportPrivateUsage]
        entered, release = threading.Event(), threading.Event()

        def held(rows):
            if disk_committed:
                insert(rows)
            entered.set()
            assert release.wait(10)
            if not disk_committed:
                insert(rows)

        monkeypatch.setattr(store, "_insert", held)
        pending = asyncio.create_task(store.publish(resolution("pending")))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            result = await asyncio.wait_for(page(store), 2)
            assert [event.seq for event in result.events] == [1]
            assert result.through_seq == 1
        finally:
            release.set()
        await pending
        with pytest.raises(EventRejected):
            await store.publish(resolution(historical=True))

        def failed(_rows):
            raise sqlite3.OperationalError("injected insertion failure")

        monkeypatch.setattr(store, "_insert", failed)
        with pytest.raises(sqlite3.OperationalError):
            await store.publish(resolution("failed"))
        assert [
            event.payload.comment
            for event in (await page(store)).events
            if event.type == "change.resolved"
        ] == [
            "committed",
            "pending",
        ]
        assert store.last_seq == 2


async def test_horizon_is_captured_before_awaiting_disk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with EventStore(tmp_path / "run.sqlite", RUN) as store:
        await store.publish(resolution())
        read = history_module._read_page
        entered, release = threading.Event(), threading.Event()

        def held(*args):
            entered.set()
            assert release.wait(10)
            return read(*args)

        monkeypatch.setattr(history_module, "_read_page", held)
        pending = asyncio.create_task(page(store))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            await store.publish(resolution("new"))
        finally:
            release.set()
        result = await pending
        assert result.through_seq == 1
        assert [event.seq for event in result.events] == [1]
        assert result.next_seq is None


async def test_sql_is_read_only_off_loop_bounded_and_does_not_fetch_large_payloads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with EventStore(tmp_path / "run.sqlite", RUN) as store:
        await store.publish(resolution())
        await store.publish(resolution("界" * 400_000))
        await store.publish(resolution())
        connect = sqlite3.connect
        loop_thread = threading.get_ident()
        statements: list[str] = []

        def traced(database, **kwargs):
            assert threading.get_ident() != loop_thread
            assert database.endswith("?mode=ro") and kwargs == {"uri": True}
            db = connect(database, **kwargs)
            db.set_trace_callback(statements.append)
            return db

        def forbidden():
            raise AssertionError("whole-log memory readers are forbidden")

        monkeypatch.setattr(sqlite3, "connect", traced)
        monkeypatch.setattr(store, "committed_events", forbidden)
        first = await page(store, limit=1)
        assert first.next_seq == 1
        assert sum(sql.startswith("SELECT envelope") for sql in statements) == 1
        assert any("LIMIT 2" in sql and "type IN" in sql for sql in statements)
        statements.clear()
        shortened = await page(store)
        assert [event.seq for event in shortened.events] == [1]
        assert shortened.next_seq == 1
        assert sum(sql.startswith("SELECT envelope") for sql in statements) == 1
        statements.clear()
        with pytest.raises(HistoryTooLarge):
            await read_history(store, after_seq=1)
        assert not any(sql.startswith("SELECT envelope") for sql in statements)
