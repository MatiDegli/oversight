"""HTTP archives and coding-agent reads share the committed event inventory."""

import asyncio
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from decision_helpers import observed_body
from fastapi import FastAPI
from fastapi.testclient import TestClient
from live_helpers import (
    DEADLINE_S,
    GatedModel,
    error_code,
    is_type,
    live_server,
    live_settings,
    start,
)
from revise_helpers import ready, request, scripted_a

from oversight.api import create_router, install_error_handlers
from oversight.api.commands import NOT_BUILT
from oversight.app import ReplayConfig, create_app
from oversight.changes import build_change_view
from oversight.events import EventStore, play, read_jsonl, read_store
from oversight_contracts.commands import ChangeView
from oversight_contracts.events import AgentStatus, LeadStatus

pytestmark = pytest.mark.anyio
LOG = Path(__file__).resolve().parents[3] / "fixtures/events/scripted-level2.jsonl"


@asynccontextmanager
async def endpoint(tmp_path):
    events = read_jsonl(LOG)
    first = events[0]
    assert first.type == "run.started"
    async with EventStore(
        tmp_path / "run.sqlite", first.payload.run_id, mode="replay", allow_fake_provider=True
    ) as store:
        app = FastAPI()
        app.state.store = store
        app.include_router(create_router(store, "replay"))
        install_error_handlers(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield events, store, client


async def test_fake_model_view_survives_revision_and_worktree_disposal(tmp_path):
    model = scripted_a()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: model) as s,
    ):
        created = (await start(s)).json()
        await ready(s)
        path = f"/changes/{created['change_id']}"
        first = await s.client.get(path)
        assert first.status_code == 200
        view = ChangeView.model_validate(first.json())
        assert view.proposal is not None
        assert view.proposal.revision == 1 and view.facts is not None
        assert view.revision_rounds == 0
        assert (await request(s)).status_code == 202
        await ready(s, after=view.seq)
        revised = ChangeView.model_validate((await s.client.get(path)).json())
        assert revised.proposal is not None
        assert revised.proposal.revision == 2 and revised.revision_rounds == 1
        assert revised.files[0].modified != view.files[0].modified
        assert (
            await s.client.post(path + "/approve", json=observed_body(s, created["change_id"]))
        ).status_code == 202
        await s.until(is_type("agent.status", state="done"))
        response = await s.client.get(path)
        assert response.status_code == 200
        archived = ChangeView.model_validate(response.json())
        assert archived.resolution is not None
        assert archived.resolution.decision == "approved"
        assert archived.files == revised.files and archived.revision_rounds == 1
        assert archived.stages is not None
        assert archived.stages.agent_work.intervals == 2
        assert not archived.stages.agent_work.open
        assert archived.stages.waiting_for_person.intervals == 2
        assert archived.stages.waiting_for_person.ms is not None
        assert not archived.stages.waiting_for_person.open
        inventory = s.store.committed_events()
        assert archived == build_change_view(
            created["change_id"], [e for e in inventory if e.seq <= archived.seq]
        )
        status = await s.client.get(f"/agents/{created['agent_id']}")
        assert status.status_code == 200
        latest = next(
            e.payload
            for e in reversed(inventory)
            if e.type == "agent.status" and e.payload.agent_id == created["agent_id"]
        )
        assert AgentStatus.model_validate(status.json()) == latest
        assert latest.state == "done"


async def test_status_and_change_are_readable_before_a_proposal(tmp_path):
    model = GatedModel()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: model) as s,
    ):
        created = (await start(s)).json()
        await model.entered.wait()
        status = await s.client.get(f"/agents/{created['agent_id']}")
        assert status.status_code == 200 and status.json()["state"] == "working"
        response = await s.client.get(f"/changes/{created['change_id']}")
        assert response.status_code == 200
        view = ChangeView.model_validate(response.json())
        assert view.proposal is None and view.files == []
        assert view.usage is None and view.revision_rounds == 0


@pytest.mark.parametrize("identifier", ["99", "not-an-id"])
async def test_unknown_and_malformed_ids_use_the_existing_404_shape(tmp_path, identifier):
    async with endpoint(tmp_path) as (_, store, client):
        for kind in ("agents", "changes"):
            name = f"{'agent' if kind == 'agents' else 'change'}-{identifier}"
            assert error_code(await client.get(f"/{kind}/{name}")) == (404, "not_found")
        assert store.committed_events() == ()


async def test_a_coordination_lead_is_not_a_coding_agent(tmp_path):
    async with endpoint(tmp_path) as (_, store, client):
        await store.publish(
            LeadStatus(
                agent_id="agent-9",
                revision=1,
                task="Coordinate",
                model="fake",
                state="working",
                step=0,
                detail=None,
            )
        )
        assert error_code(await client.get("/agents/agent-9")) == (404, "not_found")


async def test_replay_reads_are_archived_and_have_no_effects(tmp_path):
    async with endpoint(tmp_path) as (events, store, client):
        await play(store, events, speed=0)
        before = read_store(store.path)
        response = await client.get("/changes/change-2")
        assert response.status_code == 200
        view = ChangeView.model_validate(response.json())
        assert view == build_change_view("change-2", events)
        status = await client.get("/agents/agent-2")
        assert status.status_code == 200 and status.json()["state"] == "done"
        assert read_store(store.path) == before
        assert ("GET", "/changes/{change_id}") not in [(m, p) for m, p, _ in NOT_BUILT]
        assert ("GET", "/agents/{agent_id}") not in [(m, p) for m, p, _ in NOT_BUILT]
        for method, path, _ in NOT_BUILT:
            url = path.format(
                agent_id="agent-2",
                change_id="change-2",
                queued_task_id="queued-task-1",
                plan_id="plan-1",
            )
            assert error_code(await client.request(method, url, content=b"not json")) == (
                501,
                "not_implemented",
            )


def test_production_replay_app_wires_the_read_store(tmp_path):
    app = create_app(
        live_settings(tmp_path), ReplayConfig(log=LOG, speed=0, allow_fake_provider=True)
    )
    with TestClient(app, base_url="http://127.0.0.1:8710") as client:
        # The empty replay prefix is already readable; the player may have committed more.
        response = client.get("/changes/change-99")
        assert error_code(response) == (404, "not_found")
        assert error_code(client.get("/agents/agent-99")) == (404, "not_found")


async def test_seq_and_files_come_from_the_same_single_inventory(tmp_path, monkeypatch):
    async with endpoint(tmp_path) as (events, store, client):
        await play(store, events, speed=0)
        prefix = store.committed_events()[:76]
        calls = 0

        def captured():
            nonlocal calls
            calls += 1
            assert calls == 1
            return prefix

        def forbidden(_self):
            raise AssertionError("the horizon must come from the captured events")

        monkeypatch.setattr(store, "committed_events", captured)
        monkeypatch.setattr(EventStore, "last_seq", property(forbidden))
        monkeypatch.setattr(store, "graph", forbidden)
        response = await client.get("/changes/change-2")
        assert response.status_code == 200
        view = ChangeView.model_validate(response.json())
        assert view == build_change_view("change-2", prefix)
        assert view.proposal is not None
        assert view.seq == 76 and view.proposal.revision == 2
        assert calls == 1


@pytest.mark.parametrize("after_sql_commit", [False, True])
async def test_pending_insert_is_excluded_from_both_reads(tmp_path, monkeypatch, after_sql_commit):
    async with endpoint(tmp_path) as (events, store, client):
        await play(store, events[:53], speed=0)
        original = build_change_view("change-2", store.committed_events())
        entered, release = threading.Event(), threading.Event()
        insert = store._insert  # pyright: ignore[reportPrivateUsage]

        def held(rows):
            if after_sql_commit:
                insert(rows)
            entered.set()
            assert release.wait(10)
            if not after_sql_commit:
                insert(rows)

        update = next(
            e.payload
            for e in events
            if e.type == "change.updated" and e.payload.change_id == "change-2"
        )
        update = update.model_copy(
            update={"file": update.file.model_copy(update={"modified": "pending contents"})}
        )
        status = next(
            e.payload
            for e in reversed(events[:53])
            if e.type == "agent.status" and e.payload.agent_id == "agent-2"
        )
        monkeypatch.setattr(store, "_insert", held)
        pending = asyncio.gather(
            store.publish(update), store.publish(status.model_copy(update={"state": "stopped"}))
        )
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            async with asyncio.timeout(2):
                view_response = await client.get("/changes/change-2")
                status_response = await client.get("/agents/agent-2")
            assert view_response.status_code == status_response.status_code == 200
            assert ChangeView.model_validate(view_response.json()) == original
            assert AgentStatus.model_validate(status_response.json()) == status
        finally:
            release.set()
            await pending
        refreshed = ChangeView.model_validate((await client.get("/changes/change-2")).json())
        assert refreshed.seq == original.seq + 2
        assert any(file.modified == "pending contents" for file in refreshed.files)
        assert (await client.get("/agents/agent-2")).json()["state"] == "stopped"
