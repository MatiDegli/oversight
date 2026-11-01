"""GET /usage reads one committed inventory in both live and replay modes."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from unittest.mock import Mock

import httpx
import pytest
from fastapi import FastAPI

from oversight.api import create_router, install_error_handlers
from oversight.events import EventStore, build_usage_view, play, read_jsonl, read_store
from oversight_contracts.usage import UsageView

pytestmark = pytest.mark.anyio
ROOT = Path(__file__).resolve().parents[3]
DEMO = ROOT / "fixtures/events/recorded-demo.jsonl"
RUN = "run-20261027-185214"


@asynccontextmanager
async def endpoint(tmp_path: Path, mode: Literal["live", "replay"]):
    async with EventStore(tmp_path / "run.sqlite", RUN, mode=mode) as store:
        commands = Mock() if mode == "live" else None
        app = FastAPI()
        install_error_handlers(app)
        app.include_router(create_router(store, mode, commands))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield store, client
        if commands is not None:
            assert commands.mock_calls == []


@pytest.mark.parametrize("mode", ["live", "replay"])
async def test_usage_is_served_in_both_modes(tmp_path: Path, mode) -> None:
    async with endpoint(tmp_path, mode) as (store, client):
        response = await client.get("/usage")
        assert response.status_code == 200
        empty = UsageView.model_validate_json(response.content)
        assert (empty.run_id, empty.seq, empty.cost.coverage, empty.cost.usd) == (
            RUN,
            0,
            "complete",
            0.0,
        )
        assert empty.hours.agent_ms == 0 and empty.hours.parallelism is None
        assert empty.teams == [] and empty.teams_add_up and empty.revision_rounds == 0
        events = read_jsonl(DEMO)
        for event in events[:6]:
            await store.publish(event.payload, ts=event.ts)
        before = read_store(store.path)
        response = await client.get("/usage")
        assert response.status_code == 200
        assert response.json() == build_usage_view(
            store.run_id, store.committed_events()
        ).model_dump(mode="json")
        assert read_store(store.path) == before


async def test_replayed_demo_usage_has_no_effects(tmp_path: Path) -> None:
    async with endpoint(tmp_path, "replay") as (store, client):
        await play(store, read_jsonl(DEMO), speed=0)
        before = read_store(store.path)
        response = await client.get("/usage")
        assert response.status_code == 200
        view = UsageView.model_validate_json(response.content)
        assert view.seq == 69 and view.run_id == RUN
        assert view.cost.known_usd == pytest.approx(0.000977554, abs=1e-12)
        assert view.cost.usd is None and view.cost.accepted_changes == 2
        assert (view.hours.agent_ms, view.hours.active_ms) == (32982, 21457)
        assert [(t.team, t.ms) for t in view.teams] == [("agent-1", 21457), ("agent-2", 11525)]
        assert not view.teams_add_up and view.revision_rounds == 0
        assert read_store(store.path) == before


async def test_usage_captures_the_inventory_once(tmp_path: Path, monkeypatch) -> None:
    async with endpoint(tmp_path, "replay") as (store, client):
        await play(store, read_jsonl(DEMO), speed=0)
        prefix = store.committed_events()[:20]
        captured = Mock(return_value=prefix)
        monkeypatch.setattr(store, "committed_events", captured)
        response = await client.get("/usage")
        assert response.status_code == 200
        assert UsageView.model_validate_json(response.content) == build_usage_view(RUN, prefix)
        captured.assert_called_once_with()


def test_proposal_documents_usage_route() -> None:
    proposal = (ROOT / "docs/proposal.md").read_text()
    assert (
        "| `GET /usage` | none | 200 `UsageView`; the committed log, live or in a replay | |"
    ) in proposal
