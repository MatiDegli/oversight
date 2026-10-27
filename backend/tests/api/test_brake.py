"""The spending brake: configured or closed, the ledger across restarts, warnings, settling."""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest
from live_helpers import (
    DEADLINE_S,
    Scripts,
    is_type,
    live_server,
    live_settings,
    meta_settings,
    script_json,
)

from oversight.agents import MUSE_SPARK, LedgerFile, LedgerState, UnknownUsage
from oversight.app import ledger_command
from oversight.settings import Settings
from oversight.workspace import WorkspaceBusy
from oversight_contracts.commands import ErrorResponse

pytestmark = pytest.mark.anyio

USAGE = {"input_tokens": 1000, "output_tokens": 100, "cached_tokens": 0}
METERED = script_json(
    "Finish at once.",
    [{"tool_calls": [{"name": "finish", "arguments": {"summary": "Done."}}], "usage": USAGE}],
)
NO_USAGE = script_json(
    "Finish at once.", [{"tool_calls": [{"name": "finish", "arguments": {"summary": "Done."}}]}]
)


def write_ledger(settings: Settings, body: object) -> None:
    settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    text = body if isinstance(body, str) else json.dumps(body)
    (settings.runtime_dir / "ledger.json").write_text(text)


def read_ledger(settings: Settings) -> dict[str, Any]:
    return json.loads((settings.runtime_dir / "ledger.json").read_text())


async def start_code(settings: Settings, scripts: Scripts) -> tuple[int, str | None]:
    async with live_server(settings, scripts) as s:
        response = await s.client.post("/agents", json={"task": "t"})
        if response.status_code == 201:
            await s.until(is_type("agent.status", state="ready_for_review"))
            return 201, None
        return response.status_code, ErrorResponse.model_validate(response.json()).error.code


@pytest.mark.parametrize(
    ("overrides", "ledger"),
    [
        ({"budget_usd": None}, None),
        ({"output_usd_per_m": None}, None),
        ({}, "not json"),
        ({}, {"spent_usd": "12", "unknown": []}),
        ({}, {"spent_usd": 1.0, "unknown": [{"run_id": "r", "agent_id": "agent-1", "ts": "t"}]}),
        ({}, {"spent_usd": 135.01, "unknown": []}),
    ],
    ids=["no-budget", "no-price", "not-json", "malformed", "unknown-entry", "above-90"],
)
async def test_a_closed_brake_refuses_before_any_client(
    tmp_path: Path, overrides: dict[str, Any], ledger: object
) -> None:
    settings = meta_settings(tmp_path, **overrides)
    if ledger is not None:
        write_ledger(settings, ledger)
    scripts = Scripts([METERED], MUSE_SPARK)
    async with asyncio.timeout(DEADLINE_S):
        assert await start_code(settings, scripts) == (403, "spend_brake")
    assert scripts.calls == 0


async def test_an_unreadable_ledger_is_reported_at_start(tmp_path: Path) -> None:
    settings = meta_settings(tmp_path)
    write_ledger(settings, "not json")
    async with asyncio.timeout(DEADLINE_S), live_server(settings, Scripts([], MUSE_SPARK)) as s:
        errors = [e for e in s.events() if e.type == "error"]
        assert [e.payload.source for e in errors] == ["server"]
        assert "ledger" in errors[0].payload.message
    assert (settings.runtime_dir / "ledger.json").read_text() == "not json"


@pytest.mark.parametrize(
    ("spent", "brake"), [(75.0, "warn_50"), (120.0, "warn_80"), (135.0, "blocked")]
)
async def test_from_half_the_budget_runs_start_and_ticks_warn(
    tmp_path: Path, spent: float, brake: str
) -> None:
    # 135 of 150 is exactly 90%: it starts, and its first call takes it past, so the tick says
    # blocked while the run goes on.
    settings = meta_settings(tmp_path)
    write_ledger(settings, {"spent_usd": spent, "unknown": []})
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(settings, Scripts([METERED], MUSE_SPARK)) as s,
    ):
        assert (await s.client.post("/agents", json={"task": "t"})).status_code == 201
        [tick] = [e for e in await s.until(is_type("usage.tick")) if e.type == "usage.tick"]
    # 1000 input and 100 output tokens at 0.10 and 0.20 per million.
    cost = (1000 * 0.10 + 100 * 0.20) / 1_000_000
    assert tick.payload.brake == brake
    assert tick.payload.cost_usd == pytest.approx(cost)
    assert tick.payload.spent_usd == pytest.approx(spent + cost)
    assert read_ledger(settings) == {"spent_usd": pytest.approx(spent + cost), "unknown": []}


async def test_replies_without_usage_refuse_the_next_start_until_settled(tmp_path: Path) -> None:
    settings = meta_settings(tmp_path)
    async with asyncio.timeout(DEADLINE_S):
        async with live_server(settings, Scripts([NO_USAGE], MUSE_SPARK)) as s:
            created = (await s.client.post("/agents", json={"task": "t"})).json()
            await s.until(is_type("agent.status", state="ready_for_review"))
            [entry] = read_ledger(settings)["unknown"]
            assert entry["run_id"] == s.live.run_id and entry["agent_id"] == "agent-1"
            assert (
                await s.client.post(f"/changes/{created['change_id']}/reject")
            ).status_code == 202
            await s.until(is_type("agent.status", state="done"))
            refused = await s.client.post("/agents", json={"task": "t"})
            assert ErrorResponse.model_validate(refused.json()).error.code == "spend_brake"
            with pytest.raises(WorkspaceBusy):
                await asyncio.to_thread(ledger_command, settings, 0.5)
        # A restart, and its reset, keep the ledger.
        assert await start_code(settings, Scripts([NO_USAGE], MUSE_SPARK)) == (403, "spend_brake")
        printed = await asyncio.to_thread(ledger_command, settings, 0.5)
        assert json.loads(printed) == {"spent_usd": 0.5, "unknown": []}
        assert await start_code(settings, Scripts([METERED], MUSE_SPARK)) == (201, None)


async def test_the_fake_model_is_never_braked(tmp_path: Path) -> None:
    settings = live_settings(tmp_path)
    write_ledger(settings, "not json")
    async with asyncio.timeout(DEADLINE_S):
        assert await start_code(settings, Scripts([NO_USAGE])) == (201, None)
    assert (settings.runtime_dir / "ledger.json").read_text() == "not json"


def test_a_ledger_write_failing_before_the_replace_keeps_the_old_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = LedgerFile(tmp_path / "ledger.json")
    ledger.write(LedgerState(2.0, ()))
    before = ledger.path.read_text()

    def broken_fsync(fd: int) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", broken_fsync)
    entry = UnknownUsage("run-20261026-000000", "agent-1", None, "2026-10-26T00:00:00.000Z")
    with pytest.raises(OSError, match="disk full"):
        ledger.write(LedgerState(3.0, (entry,)))
    assert ledger.path.read_text() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ledger.json"]
    assert ledger.read() == LedgerState(2.0, ())


def test_settling_sets_the_spend_and_clears_unknown_calls(tmp_path: Path) -> None:
    settings = live_settings(tmp_path)
    write_ledger(
        settings,
        {
            "spent_usd": 9.0,
            "unknown": [{"run_id": "r", "agent_id": "agent-1", "model": None, "ts": "t"}],
        },
    )
    assert json.loads(ledger_command(settings, None))["spent_usd"] == 9.0
    assert json.loads(ledger_command(settings, 40.0)) == {"spent_usd": 40.0, "unknown": []}
