"""Configuration failures have structured details before any client is constructed."""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import httpx
import pytest
from live_helpers import (
    DEADLINE_S,
    FAKE_SCRIPT_B,
    Scripts,
    meta_settings,
)

from oversight.app import ClientFactory, LiveRun, create_app
from oversight.changes import ChangeManager
from oversight.settings import Settings
from oversight.setup import missing_settings, offered_profiles
from oversight_contracts.commands import ErrorResponse, Health, StartAgent

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
        assert health.ok and health.contract_version == "1.2.0"
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
