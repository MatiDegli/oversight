"""History uses only committed envelopes, with typed refusals and a bounded wire body."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from unittest.mock import Mock

import httpx
import pytest
from fastapi import FastAPI

from oversight.api import create_router, install_error_handlers
from oversight.events import EventStore, read_store
from oversight_contracts.commands import ErrorResponse, HistoryPage
from oversight_contracts.events import ChangeResolved

pytestmark = pytest.mark.anyio
RUN = "run-20261027-140312"
CAP = 1024 * 1024


def resolution(comment: str = "ok") -> ChangeResolved:
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
        decided_by="person",
        head_commit="candidate",
        reasons=["person's decision"],
    )


@asynccontextmanager
async def endpoint(tmp_path: Path, mode: Literal["live", "replay"] = "live"):
    async with EventStore(tmp_path / "run.sqlite", RUN, mode=mode) as store:
        commands = Mock() if mode == "live" else None
        app = FastAPI()
        install_error_handlers(app)
        app.include_router(create_router(store, mode, commands))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            yield store, client
        if commands is not None:
            assert commands.mock_calls == []


def success(response: httpx.Response) -> HistoryPage:
    assert response.status_code == 200, response.text
    assert len(response.content) <= CAP
    return HistoryPage.model_validate_json(response.content)


def refused(response: httpx.Response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert ErrorResponse.model_validate_json(response.content).error.code == code


@pytest.mark.parametrize("name", ["after_seq", "through_seq", "limit"])
@pytest.mark.parametrize(
    "value", ["", "-1", "+1", "1.5", " 1", "1\n", "abc", "\u0661", "²", "9" * 5000]
)
async def test_malformed_ascii_integers_are_refused_without_effects(
    tmp_path: Path,
    name: str,
    value: str,
) -> None:
    async with endpoint(tmp_path) as (store, client):
        await store.publish(resolution())
        before = read_store(store.path)
        refused(await client.get("/history", params={name: value}), 422, "invalid_request")
        assert read_store(store.path) == before


@pytest.mark.parametrize(
    "query",
    [
        {"limit": "0"},
        {"limit": "101"},
        {"limit": "999999999999999999"},
        {"through_seq": "2"},
        {"after_seq": "2"},
        {"after_seq": "1", "through_seq": "0"},
    ],
)
async def test_ranges_are_refused_without_clamping(tmp_path: Path, query: dict[str, str]) -> None:
    async with endpoint(tmp_path) as (store, client):
        await store.publish(resolution())
        before = read_store(store.path)
        refused(await client.get("/history", params=query), 422, "invalid_request")
        assert read_store(store.path) == before


async def test_individually_oversized_event_is_typed_413(tmp_path: Path) -> None:
    async with endpoint(tmp_path) as (store, client):
        await store.publish(resolution("界" * (CAP // 3)))
        refused(await client.get("/history"), 413, "too_large")
        assert store.last_seq == 1


async def test_multibyte_pages_shorten_without_skipping(tmp_path: Path) -> None:
    async with endpoint(tmp_path) as (store, client):
        rows = [await store.publish(resolution("界" * 180_000)) for _ in range(3)]
        seen: list[int] = []
        after = 0
        for _ in range(3):
            page = success(
                await client.get("/history", params={"after_seq": after, "through_seq": 3})
            )
            assert len(page.events) == 1
            seen.extend(e.seq for e in page.events)
            if page.next_seq is None:
                break
            after = page.next_seq
        assert seen == [row.seq for row in rows]


@pytest.mark.parametrize("extra", [0, 1])
async def test_complete_body_byte_boundary(tmp_path: Path, extra: int) -> None:
    async with endpoint(tmp_path) as (store, client):
        # ASCII padding keeps serialized growth exactly one byte per character.
        event = resolution("")
        from oversight_contracts.events import ChangeResolvedEvent

        envelope = ChangeResolvedEvent(
            seq=1, ts="2026-10-27T00:00:00.000Z", type="change.resolved", payload=event
        )
        base = HistoryPage(run_id=RUN, through_seq=1, events=[envelope], next_seq=None)
        padding = CAP - len(base.model_dump_json().encode("utf-8"))
        await store.publish(resolution("x" * (padding + extra)), ts=envelope.ts)
        response = await client.get("/history")
        if extra:
            refused(response, 413, "too_large")
        else:
            assert len(response.content) == CAP
            assert success(response).next_seq is None


@pytest.mark.parametrize("mode", ["live", "replay"])
async def test_history_is_explicitly_served_in_both_modes(tmp_path: Path, mode) -> None:
    async with endpoint(tmp_path, mode) as (store, client):
        empty = success(await client.get("/history"))
        assert empty == HistoryPage(run_id=RUN, through_seq=0, events=[], next_seq=None)
        for _ in range(101):
            await store.publish(resolution())
        before = read_store(store.path)
        default = success(await client.get("/history"))
        assert (default.run_id, default.through_seq, len(default.events), default.next_seq) == (
            RUN,
            101,
            50,
            50,
        )
        for limit in (1, 100):
            result = success(await client.get("/history", params={"limit": limit}))
            assert len(result.events) == limit
            assert result.next_seq == limit
            assert [e.model_dump_json() for e in result.events] == [
                row.envelope for row in before[:limit]
            ]
        terminal = success(
            await client.get("/history", params={"after_seq": 100, "through_seq": 101})
        )
        assert [e.seq for e in terminal.events] == [101]
        assert terminal.next_seq is None
        equal = success(await client.get("/history", params={"after_seq": 101, "through_seq": 101}))
        assert equal.events == [] and equal.next_seq is None
        assert read_store(store.path) == before


@pytest.mark.parametrize("failure", ["read", "validation"])
async def test_unexpected_failures_use_the_existing_internal_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    import sqlite3

    from oversight.events import history as history_module

    async with endpoint(tmp_path) as (store, client):
        await store.publish(resolution())
        before = read_store(store.path)

        def fail(*_args):
            if failure == "read":
                raise sqlite3.OperationalError("injected read failure")
            HistoryPage.model_validate({})

        monkeypatch.setattr(history_module, "_read_page", fail)
        refused(await client.get("/history"), 500, "internal")
        assert read_store(store.path) == before


async def test_history_remains_responsive_while_insertion_waits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    import threading

    async with endpoint(tmp_path) as (store, client):
        await store.publish(resolution("committed"))
        entered, release = threading.Event(), threading.Event()
        insert = store._insert  # pyright: ignore[reportPrivateUsage]

        def held(rows):
            entered.set()
            assert release.wait(10)
            insert(rows)

        monkeypatch.setattr(store, "_insert", held)
        pending = asyncio.create_task(store.publish(resolution("pending")))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            result = success(await asyncio.wait_for(client.get("/history"), 2))
            assert result.through_seq == 1
            assert [event.seq for event in result.events] == [1]
            assert result.next_seq is None
        finally:
            release.set()
        await pending
