"""Live saves use startup parsing and only affect the next admitted work; no paid calls."""

# Inspect the captured runtime dependencies as well as the HTTP/event contract.
# pyright: reportPrivateUsage=false

import asyncio
import os
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from live_helpers import (
    BySlot,
    GatedModel,
    Scripts,
    error_code,
    is_type,
    live_server,
    meta_settings,
    script_json,
    start,
    write_ledger,
)

from oversight.agents import MUSE_SPARK, DecisionUnavailable, LedgerState, Pricing, Usage
from oversight.app import ReplayConfig, create_app
from oversight.settings import REPO_ROOT, Settings
from oversight_contracts.commands import StartAgent

pytestmark = pytest.mark.anyio


@pytest.fixture
def configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    for name in tuple(os.environ):
        if name.startswith(("OVERSIGHT_", "MUSE_SPARK_", "BASETEN_")) or name == "META_API_KEY":
            monkeypatch.delenv(name)
    path = tmp_path / "settings.env"
    path.write_text(
        "META_API_KEY=synthetic-key\nBASETEN_API_KEY=synthetic-baseten\n"
        "OVERSIGHT_BUDGET_USD=10\nOVERSIGHT_SPEND_BRAKE_PERCENT=90\n"
        "MUSE_SPARK_INPUT_USD_PER_M=1\nMUSE_SPARK_OUTPUT_USD_PER_M=2\n"
        "OVERSIGHT_BASETEN_BUDGET_USD=10\nBASETEN_INPUT_USD_PER_M=3\n"
        "BASETEN_OUTPUT_USD_PER_M=4\nOVERSIGHT_MAX_AGENTS=2\n"
    )
    parsed = Settings.load(path)
    return replace(
        meta_settings(tmp_path),
        env_file=path,
        loaded_file_values=parsed.loaded_file_values,
        meta_api_key=parsed.meta_api_key,
        baseten_api_key=parsed.baseten_api_key,
        budget_usd=parsed.budget_usd,
        input_usd_per_m=parsed.input_usd_per_m,
        output_usd_per_m=parsed.output_usd_per_m,
        baseten_budget_usd=parsed.baseten_budget_usd,
        baseten_input_usd_per_m=parsed.baseten_input_usd_per_m,
        baseten_output_usd_per_m=parsed.baseten_output_usd_per_m,
    )


async def save(client: httpx.AsyncClient, **values: str) -> httpx.Response:
    response = await client.put("/settings", json={"values": values})
    assert response.status_code == 200, response.text
    return response


async def test_saved_budget_in_next_session_started_event(configured: Settings) -> None:
    app = create_app(configured, client_factory=Scripts())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://localhost:8710"
    ) as client:
        response = await save(client, OVERSIGHT_BUDGET_USD="20")
        assert not response.json()["restart_required"]
        assert app.state.settings is app.state.live._settings
        async with app.router.lifespan_context(app):
            started = app.state.store.committed_events()[0].payload
            assert started.budget.budget_usd == 20


async def test_running_budget_and_prices_kept_next_agent_uses_saved_values(
    configured: Settings,
) -> None:
    model = GatedModel(MUSE_SPARK)
    async with live_server(configured, BySlot(lambda _p, _s: model)) as s:
        assert (await start(s, slot="A")).status_code == 201
        await asyncio.wait_for(model.entered.wait(), 10)
        old = s.live._kept["agent-1"]
        response = await save(
            s.client,
            OVERSIGHT_BUDGET_USD="20",
            OVERSIGHT_SPEND_BRAKE_PERCENT="50",
            MUSE_SPARK_INPUT_USD_PER_M="5",
            MUSE_SPARK_OUTPUT_USD_PER_M="6",
            BASETEN_INPUT_USD_PER_M="7",
            BASETEN_OUTPUT_USD_PER_M="8",
            OVERSIGHT_BASETEN_BUDGET_USD="30",
        )
        assert not response.json()["restart_required"]
        assert (await start(s, slot="B")).status_code == 201
        new = s.live._kept["agent-2"]
        assert (old._ledger.budget_usd, old._ledger._block_at) == (10, 0.9)
        assert (new._ledger.budget_usd, new._ledger._block_at) == (20, 0.5)
        assert old._pricing == Pricing(1, 2, 1)
        assert new._pricing == Pricing(5, 6, 5)
        old._ledger.record(2)
        assert new._ledger.spent_usd == old._ledger.spent_usd == 2
        assert s.app.state.settings is s.live._settings
        assert s.live._settings.baseten_budget_usd == 30
        assert s.live._price_for(MUSE_SPARK.model_copy(update={"provider": "baseten"})) == Pricing(
            7, 8, 7
        )
        # Previously committed events remain historical, never rewritten on save.
        assert s.events()[0].payload.budget.budget_usd == 10


@pytest.mark.parametrize("spent,admitted", [(6, False), (4, True)])
async def test_saved_share_controls_next_start(
    configured: Settings, spent: float, admitted: bool
) -> None:
    write_ledger(configured, {"spent_usd": spent, "unknown": []})
    async with live_server(configured, BySlot(lambda _p, _s: GatedModel(MUSE_SPARK))) as s:
        await save(s.client, OVERSIGHT_SPEND_BRAKE_PERCENT="50")
        response = await start(s)
        if admitted:
            assert response.status_code == 201
        else:
            assert error_code(response) == (403, "spend_brake")
            assert "50%" in response.json()["error"]["message"]


async def test_invalid_unsent_share_fails_closed_after_successful_save(
    configured: Settings,
) -> None:
    async with live_server(configured, Scripts()) as s:
        assert configured.env_file is not None
        await asyncio.to_thread(
            configured.env_file.write_text, "OVERSIGHT_SPEND_BRAKE_PERCENT=bad\n"
        )
        await save(
            s.client,
            OVERSIGHT_BUDGET_USD="20",
            MUSE_SPARK_INPUT_USD_PER_M="1",
            MUSE_SPARK_OUTPUT_USD_PER_M="2",
        )
        response = await start(s)
        assert error_code(response) == (403, "spend_brake")
        assert "OVERSIGHT_SPEND_BRAKE_PERCENT" in response.json()["error"]["message"]
        assert s.live._settings.setting_problems["OVERSIGHT_SPEND_BRAKE_PERCENT"] == "invalid"
        bank = s.live.difference_decisions.accounting
        await bank.load()
        with pytest.raises(DecisionUnavailable, match="budget_unconfigured"):
            await bank.admit()
        await save(s.client, OVERSIGHT_SPEND_BRAKE_PERCENT="90")
        assert "OVERSIGHT_SPEND_BRAKE_PERCENT" not in s.live._settings.setting_problems


@pytest.mark.parametrize(
    "name", ["OVERSIGHT_BUDGET_USD", "MUSE_SPARK_INPUT_USD_PER_M", "MUSE_SPARK_OUTPUT_USD_PER_M"]
)
async def test_invalid_unsent_amount_fails_setup(configured: Settings, name: str) -> None:
    async with live_server(configured, Scripts()) as s:
        assert configured.env_file is not None
        with configured.env_file.open("a") as file:
            file.write(f"{name}=invalid\n")
        await save(s.client, BASETEN_INPUT_USD_PER_M="7")
        response = await start(s)
        assert error_code(response) == (503, "not_configured")
        assert any(
            entry["name"] == name and entry["problem"] == "invalid"
            for entry in response.json()["error"]["missing"]
        )
        health = (await s.client.get("/health")).json()
        assert any(entry["name"] == name for entry in health["missing_settings"])


@pytest.mark.parametrize(
    "values",
    [
        {"META_API_KEY": "replacement"},
        {"BASETEN_API_KEY": "replacement"},
        {"OVERSIGHT_MAX_AGENTS": "8"},
        {"OVERSIGHT_CROSS_REVIEW": "true"},
    ],
)
async def test_restart_fields_keep_runtime_dependencies(
    configured: Settings, values: dict[str, str]
) -> None:
    async with live_server(configured, Scripts()) as s:
        redactor, factory, slots = s.live._redactor, s.live._client_factory, s.live._slots
        response = await save(s.client, **values)
        assert response.json()["restart_required"]
        current = s.live._settings
        assert (
            current.meta_api_key,
            current.baseten_api_key,
            current.max_agents,
            current.cross_review,
        ) == (configured.meta_api_key, configured.baseten_api_key, 2, False)
        assert (s.live._redactor, s.live._client_factory, s.live._slots) == (
            redactor,
            factory,
            slots,
        )


async def test_environment_budget_wins(
    configured: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OVERSIGHT_BUDGET_USD", "25")
    configured = replace(configured, budget_usd=25)
    async with live_server(configured, Scripts()) as s:
        await save(s.client, OVERSIGHT_BUDGET_USD="20")
        assert s.live._settings.budget_usd == 25


async def test_decision_allowance_refreshes_without_replacing_ledger(configured: Settings) -> None:
    async with live_server(configured, Scripts()) as s:
        bank = s.live.difference_decisions.accounting
        await bank.load()
        bank.ledger.state = LedgerState(6)
        await save(s.client, OVERSIGHT_SPEND_BRAKE_PERCENT="50")
        with pytest.raises(DecisionUnavailable, match="budget_blocked"):
            await bank.admit()
        await save(s.client, OVERSIGHT_BASETEN_BUDGET_USD="20", BASETEN_INPUT_USD_PER_M="9")
        await bank.admit()
        assert bank.budget == 20 and bank.pricing == Pricing(9, 4, 9)
        assert bank.ledger.state == LedgerState(6)


async def test_next_receipt_uses_saved_prices_and_budget(configured: Settings) -> None:
    script = script_json(
        "Finish",
        [
            {
                "tool_calls": [{"name": "finish", "arguments": {"summary": "Done"}}],
                "usage": {"input_tokens": 1000, "output_tokens": 100},
            }
        ],
    )
    async with live_server(configured, Scripts([script], MUSE_SPARK)) as s:
        await save(s.client, OVERSIGHT_BUDGET_USD="20", MUSE_SPARK_INPUT_USD_PER_M="5")
        assert (await start(s)).status_code == 201
        events = await s.until(is_type("usage.tick"))
        tick = events[-1].payload
        assert tick.budget_usd == 20
        assert tick.cost_usd == pytest.approx(Pricing(5, 2, 5).cost(Usage(1000, 100)))
        await asyncio.gather(*tuple(s.live._runs.values()))


async def test_replay_refuses_save_and_keeps_snapshot(configured: Settings) -> None:
    app = create_app(
        configured, ReplayConfig(REPO_ROOT / "fixtures/events/handwritten-level1.jsonl")
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://localhost:8710"
    ) as client:
        response = await client.put("/settings", json={"values": {"OVERSIGHT_BUDGET_USD": "20"}})
        assert error_code(response) == (501, "not_implemented")
        assert app.state.settings is configured


async def test_launch_awaiting_workspace_keeps_admission_snapshot(configured: Settings) -> None:
    async with live_server(configured, BySlot(lambda _p, _s: GatedModel(MUSE_SPARK))) as s:
        admission = s.live._admit(StartAgent(task="t", slot="A"))
        await save(s.client, OVERSIGHT_BUDGET_USD="20", MUSE_SPARK_INPUT_USD_PER_M="5")
        assert (await s.live._launch(admission)).agent_id == "agent-1"
        run = s.live._kept["agent-1"]
        assert run._ledger.budget_usd == 10 and run._pricing == Pricing(1, 2, 1)
        assert (await start(s, slot="B")).status_code == 201
        assert s.live._kept["agent-2"]._ledger.budget_usd == 20


async def test_revision_gate_and_capture_use_new_settings(
    configured: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from revise_helpers import HeldRequests, metered, ready, request, revisable

    configured = replace(configured, harness_pack=False)
    async with live_server(configured, Scripts([metered(revisable())], MUSE_SPARK)) as s:
        assert (await start(s)).status_code == 201
        await ready(s)
        await asyncio.gather(*tuple(s.live._runs.values()))
        original = s.live._kept["agent-1"]._ledger
        s.live.ledger.state = LedgerState(6)
        await save(s.client, OVERSIGHT_SPEND_BRAKE_PERCENT="50")
        refused = await request(s)
        assert error_code(refused) == (403, "spend_brake")
        assert "50%" in refused.json()["error"]["message"]
        await save(s.client, OVERSIGHT_BUDGET_USD="20", MUSE_SPARK_INPUT_USD_PER_M="5")
        async with HeldRequests(s.store, monkeypatch) as held:
            assert (await request(s)).status_code == 202
            await asyncio.wait_for(held.at["agent-1"].wait(), 10)
            await save(s.client, OVERSIGHT_BUDGET_USD="30", MUSE_SPARK_INPUT_USD_PER_M="9")
            held.open("agent-1")
        await asyncio.gather(*tuple(s.live._runs.values()))
        revised = s.live._kept["agent-1"]
        assert original.budget_usd == 10
        assert revised._ledger.budget_usd == 20 and revised._pricing == Pricing(5, 2, 5)
        ticks = [e.payload for e in s.events() if e.type == "usage.tick"]
        assert ticks[-1].budget_usd == 20


async def test_cancelled_save_still_swaps_and_releases_lock(
    configured: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from oversight import settings_file as module

    app = create_app(configured, client_factory=Scripts())
    entered, release = threading.Event(), threading.Event()
    write = module._atomic_write

    def blocked(path: Path, content: str) -> None:
        entered.set()
        assert release.wait(10)
        write(path, content)

    monkeypatch.setattr(module, "_atomic_write", blocked)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://localhost:8710"
    ) as client:
        pending = asyncio.create_task(save(client, OVERSIGHT_BUDGET_USD="20"))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            pending.cancel()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert app.state.settings is app.state.live._settings
        assert app.state.settings.budget_usd == 20
        await save(client, OVERSIGHT_BUDGET_USD="30")
        assert app.state.settings.budget_usd == 30
