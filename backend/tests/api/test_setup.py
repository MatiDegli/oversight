"""Configuration failures have structured details before any client is constructed."""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import httpx
import pytest
from live_helpers import (
    DEADLINE_S,
    FAKE_SCRIPT_B,
    Scripts,
    meta_settings,
)

from oversight import app as app_module
from oversight.agents import (
    FAKE_MODEL,
    MUSE_SPARK,
    MUSE_SPARK_OPEN,
    AgentRun,
    AgentTask,
    LedgerState,
)
from oversight.app import LIMITS, ClientFactory, LiveRun, create_app
from oversight.changes import ChangeManager, Revision
from oversight.settings import Settings
from oversight.setup import missing_settings, offered_profiles
from oversight_contracts.commands import ErrorResponse, Health, StartAgent
from oversight_contracts.events import ModelProfile

pytestmark = pytest.mark.anyio


@contextlib.asynccontextmanager
async def client_for(
    settings: Settings, clients: ClientFactory
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, client_factory=clients)
    # Rejected starts need no workspace or running agent. Keep the actual command and error
    # routes, and provide only the idle manager state the guard reads.
    manager = Mock(spec=ChangeManager, main_suspect=False)
    live: LiveRun = app.state.live
    live._manager = manager  # pyright: ignore[reportPrivateUsage]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://localhost"
    ) as client:
        yield client


async def start(client: httpx.AsyncClient, **body: Any) -> httpx.Response:
    return await client.post("/agents", json={"task": "t", **body})


@contextlib.asynccontextmanager
async def proposed_run(
    settings: Settings, profile: ModelProfile
) -> AsyncIterator[tuple[httpx.AsyncClient, LiveRun]]:
    # Seed a kept run so the real route and revision dispatch need no sockets or workspace.
    app = create_app(settings, client_factory=Scripts())
    live: LiveRun = app.state.live
    manager = Mock(spec=ChangeManager, main_suspect=False)
    manager.request_changes.side_effect = lambda _id, comment, **_guard: asyncio.create_task(
        asyncio.sleep(0, result=Revision(comment, None))
    )
    live._manager = manager  # pyright: ignore[reportPrivateUsage]
    task = AgentTask("agent-1", "change-1", "B", "t", LIMITS)
    live._agents["agent-1"] = app_module._Agent(task, profile)  # pyright: ignore[reportPrivateUsage]
    live._kept["agent-1"] = Mock(spec=AgentRun, steps=1)  # pyright: ignore[reportPrivateUsage]
    live.ledger.state = LedgerState(0.0, ())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://localhost"
    ) as client:
        yield client, live


@pytest.mark.parametrize(
    ("overrides", "names"),
    [
        ({"budget_usd": None}, ["OVERSIGHT_BUDGET_USD"]),
        ({"input_usd_per_m": None}, ["MUSE_SPARK_INPUT_USD_PER_M"]),
        ({"output_usd_per_m": None}, ["MUSE_SPARK_OUTPUT_USD_PER_M"]),
        (
            {"meta_api_key": None, "budget_usd": None, "input_usd_per_m": None},
            ["META_API_KEY", "OVERSIGHT_BUDGET_USD", "MUSE_SPARK_INPUT_USD_PER_M"],
        ),
    ],
)
async def test_metered_revision_lists_setup_problems_and_starts_no_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, overrides: dict[str, Any], names: list[str]
) -> None:
    settings = meta_settings(tmp_path)
    async with asyncio.timeout(DEADLINE_S), proposed_run(settings, MUSE_SPARK) as (client, live):
        before = live.store.last_seq
        manager = live._manager  # pyright: ignore[reportPrivateUsage]
        assert manager is not None
        monkeypatch.setattr(live, "_settings", replace(settings, **overrides))
        response = await client.post(
            "/changes/change-1/request-changes",
            json={"comment": "Revise, please.", "revision": 1, "head_commit": "seeded-head"},
        )
        assert response.status_code == 503, response.text
        assert response.json()["error"] == {
            "code": "not_configured",
            "message": "Server setup is incomplete.",
            "missing": [{"name": name, "problem": "missing", "slots": ["B"]} for name in names],
        }
        cast(Mock, manager.request_changes).assert_not_called()
        assert live._runs == {} and live._decisions == {}  # pyright: ignore[reportPrivateUsage]
        assert live.store.last_seq == before


@pytest.mark.parametrize("profile", [MUSE_SPARK_OPEN, FAKE_MODEL], ids=["baseten", "fake"])
async def test_unmetered_revision_ignores_missing_meta_setup(
    tmp_path: Path, profile: ModelProfile
) -> None:
    settings = meta_settings(
        tmp_path,
        agent_model_b=profile.id,
        fake_script_b=FAKE_SCRIPT_B,
        baseten_api_key="test-baseten",
        meta_api_key=None,
        budget_usd=None,
        input_usd_per_m=None,
        output_usd_per_m=None,
    )
    async with asyncio.timeout(DEADLINE_S), proposed_run(settings, profile) as (client, live):
        response = await client.post(
            "/changes/change-1/request-changes",
            json={"comment": "Revise, please.", "revision": 1, "head_commit": "seeded-head"},
        )
        assert response.status_code == 202, response.text
        await asyncio.wait({live._runs["agent-1"]})  # pyright: ignore[reportPrivateUsage]
        run = cast(Mock, live._kept["agent-1"])  # pyright: ignore[reportPrivateUsage]
        run.revise.assert_awaited_once_with("Revise, please.", None)


@pytest.mark.parametrize("attribute", ["budget_usd", "input_usd_per_m", "output_usd_per_m"])
def test_brake_itself_fails_closed_without_budget_or_prices(tmp_path: Path, attribute: str) -> None:
    live = LiveRun(meta_settings(tmp_path, **{attribute: None}))
    live.ledger.state = LedgerState(0.0, ())
    assert live.brake_reason() is not None


@pytest.mark.parametrize(
    ("name", "attribute", "value", "problem"),
    [
        ("META_API_KEY", "meta_api_key", None, "missing"),
        ("META_API_KEY", "meta_api_key", "invalid key", "invalid"),
        ("OVERSIGHT_BUDGET_USD", "budget_usd", None, "missing"),
        ("MUSE_SPARK_INPUT_USD_PER_M", "input_usd_per_m", None, "missing"),
        ("MUSE_SPARK_OUTPUT_USD_PER_M", "output_usd_per_m", None, "missing"),
        ("OVERSIGHT_BUDGET_USD", "budget_usd", 0.0, "invalid"),
    ],
)
async def test_start_returns_only_the_selected_slots_setup_problems(
    tmp_path: Path, name: str, attribute: str, value: str | float | None, problem: str
) -> None:
    settings = meta_settings(tmp_path, **{attribute: value})
    clients = Scripts()
    async with asyncio.timeout(DEADLINE_S), client_for(settings, clients) as client:
        response = await start(client, slot="B")
        error = ErrorResponse.model_validate(response.json()).error
        assert (response.status_code, error.code) == (503, "not_configured")
        assert response.json()["error"] == {
            "code": "not_configured",
            "message": "Server setup is incomplete.",
            "missing": [{"name": name, "problem": problem, "slots": ["B"]}],
        }
        health = Health.model_validate((await client.get("/health")).json())
        assert health.ok and health.contract_version == "1.4.0"
        assert health.missing_settings == missing_settings(settings, offered_profiles(settings))
        assert all(entry.slots == ["A", "B"] for entry in health.missing_settings)
    assert clients.calls == 0


async def test_health_lists_both_providers_and_start_returns_all_unmet_requirements(
    tmp_path: Path,
) -> None:
    settings = meta_settings(
        tmp_path,
        meta_api_key=None,
        baseten_api_key=None,
        budget_usd=None,
        input_usd_per_m=None,
        output_usd_per_m=None,
        agent_model_b="muse-spark-open",
    )
    clients = Scripts()
    async with asyncio.timeout(DEADLINE_S), client_for(settings, clients) as client:
        health = Health.model_validate((await client.get("/health")).json())
        assert health.missing_settings == missing_settings(settings, offered_profiles(settings))
        assert len(health.missing_settings) == 5
        a = ErrorResponse.model_validate((await start(client, slot="A")).json()).error
        assert a.missing is not None and len(a.missing) == 4
        for body in ({"slot": "B"}, {"slot": "A", "model": "muse-spark-open"}):
            response = await start(client, **body)
            assert response.status_code == 503
            assert response.json()["error"]["missing"] == [
                {"name": "BASETEN_API_KEY", "problem": "missing", "slots": [body["slot"]]}
            ]
    assert clients.calls == 0


async def test_health_distinguishes_ready_from_missing_and_omitted_slot_can_use_fake(
    tmp_path: Path,
) -> None:
    settings = meta_settings(
        tmp_path, budget_usd=None, agent_model_b="fake", fake_script_b=FAKE_SCRIPT_B
    )
    clients = Scripts()
    async with asyncio.timeout(DEADLINE_S), client_for(settings, clients) as client:
        health = Health.model_validate((await client.get("/health")).json())
        assert [entry.slots for entry in health.missing_settings] == [["A"]]
    live = LiveRun(settings, clients)
    slot, profile = live._check(  # pyright: ignore[reportPrivateUsage]
        StartAgent(task="t"), Mock(spec=ChangeManager, main_suspect=False)
    )
    assert (slot, profile.provider) == ("B", "fake")
    assert clients.calls == 0


async def test_lost_ledger_is_still_spend_brake_and_health_has_no_setup_problems(
    tmp_path: Path,
) -> None:
    settings = meta_settings(tmp_path)
    clients = Scripts()
    async with asyncio.timeout(DEADLINE_S), client_for(settings, clients) as client:
        response = await start(client)
        assert response.status_code == 403
        assert ErrorResponse.model_validate(response.json()).error.code == "spend_brake"
        assert "missing" not in response.json()["error"]
        assert Health.model_validate((await client.get("/health")).json()).missing_settings == []
    assert clients.calls == 0
