"""Uncapped Meta runs still price calls and retain durable accounting safeguards."""

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from live_helpers import (
    DEADLINE_S,
    Scripts,
    is_type,
    live_server,
    live_settings,
    read_ledger,
    script_json,
    write_ledger,
)

from oversight.agents import MUSE_SPARK, LedgerState, UsageLedger
from oversight.app import LiveRun
from oversight.settings import Settings
from oversight.setup import missing_settings

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("empty", [False, True])
async def test_uncapped_meta_run_uses_default_prices_and_records_spend(
    tmp_path: Path, empty: bool
) -> None:
    values = (
        dict.fromkeys(
            ("OVERSIGHT_BUDGET_USD", "MUSE_SPARK_INPUT_USD_PER_M", "MUSE_SPARK_OUTPUT_USD_PER_M"),
            "",
        )
        if empty
        else {}
    )
    loaded = Settings.from_values(values | {"META_API_KEY": "test-key"})
    assert loaded.budget_usd is None
    assert (loaded.input_usd_per_m, loaded.output_usd_per_m) == (0.10, 0.20)
    assert missing_settings(loaded, {"A": MUSE_SPARK}) == []
    settings = replace(
        loaded,
        runtime_dir=tmp_path / ".runtime",
        demo_python=live_settings(tmp_path).demo_python,
        max_agents=1,
        spend_brake_percent=70,
    )
    write_ledger(settings, {"spent_usd": 200, "unknown": []})
    script = script_json(
        "Finish",
        [
            {
                "tool_calls": [{"name": "finish", "arguments": {"summary": "Done"}}],
                "usage": {"input_tokens": 1000, "output_tokens": 100, "cached_tokens": 200},
            }
        ],
    )
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(settings, Scripts([script], MUSE_SPARK)) as server,
    ):
        started = next(event.payload for event in server.events() if event.type == "run.started")
        assert started.budget.model_dump() == {
            "budget_usd": None,
            "spent_usd": 200,
            "warn_at": [],
            "block_at": 0.7,
        }
        assert (await server.client.post("/agents", json={"task": "Finish"})).status_code == 201
        events = await server.until(is_type("agent.status", state="ready_for_review"))
        tick = next(event.payload for event in events if event.type == "usage.tick")
        assert tick.budget_usd is None and tick.brake == "ok"
        assert tick.cost_usd == pytest.approx(0.00012)
        assert read_ledger(settings) == {"spent_usd": pytest.approx(200.00012), "unknown": []}


@pytest.mark.parametrize(
    "name", ["OVERSIGHT_BUDGET_USD", "MUSE_SPARK_INPUT_USD_PER_M", "MUSE_SPARK_OUTPUT_USD_PER_M"]
)
@pytest.mark.parametrize("value", ["garbage", "0", "-1", "nan", "inf", " "])
async def test_invalid_amount_refuses_uncapped_meta_start(
    tmp_path: Path, name: str, value: str
) -> None:
    settings = replace(
        Settings.from_values({"META_API_KEY": "test-key", name: value}),
        runtime_dir=tmp_path / ".runtime",
        demo_python=live_settings(tmp_path).demo_python,
    )
    clients = Scripts()
    async with asyncio.timeout(DEADLINE_S), live_server(settings, clients) as server:
        response = await server.client.post("/agents", json={"task": "Finish"})
        assert response.status_code == 503
        assert response.json()["error"]["missing"] == [
            {"name": name, "problem": "invalid", "slots": ["A"]}
        ]
    assert clients.calls == 0


@pytest.mark.parametrize(
    "ledger",
    ["not JSON", {"spent_usd": 200, "unknown": [{"run_id": "r", "agent_id": "a", "ts": "t"}]}],
)
async def test_uncapped_runs_still_refuse_uncertain_ledger(tmp_path: Path, ledger: object) -> None:
    settings = replace(
        Settings.from_values({"META_API_KEY": "test-key"}),
        runtime_dir=tmp_path / ".runtime",
        demo_python=live_settings(tmp_path).demo_python,
    )
    write_ledger(settings, ledger)
    clients = Scripts()
    async with asyncio.timeout(DEADLINE_S), live_server(settings, clients) as server:
        response = await server.client.post("/agents", json={"task": "Finish"})
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "spend_brake"
        assert "ledger" in response.json()["error"]["message"]
    assert clients.calls == 0


def test_no_budget_disables_only_the_numeric_brake(tmp_path: Path) -> None:
    ledger = UsageLedger(None, 999)
    assert ledger.record(1) == 1000 and ledger.brake == "ok"
    live = LiveRun(Settings(runtime_dir=tmp_path / ".runtime"))
    live.ledger.state = LedgerState(999)
    assert live.brake_reason() is None
