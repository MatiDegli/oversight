"""The scripted fake on the real demo tree; all measurements stay private."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from live_helpers import Scripts, is_type, live_server, live_settings, script_json

from oversight.changes import BehaviorDiff
from oversight.settings import Settings
from oversight.workspace import IsolationUnavailable

pytestmark = pytest.mark.anyio
PRICING = "shop/pricing.py"


def script(edits: list[tuple[str, str, str]]) -> str:
    turns = []
    for path, old, new in edits:
        turns.extend(
            [
                {"tool_calls": [{"name": "read_file", "arguments": {"path": path}}]},
                {
                    "tool_calls": [
                        {
                            "name": "edit_file",
                            "arguments": {
                                "path": path,
                                "old_text": old,
                                "new_text": new,
                            },
                        }
                    ]
                },
            ]
        )
    turns.append({"tool_calls": [{"name": "finish", "arguments": {"summary": "Measured edit."}}]})
    return script_json("Make a small edit.", turns)


async def run(s):
    response = await s.client.post("/agents", json={"task": "Make a small edit."})
    assert response.status_code == 201, response.text
    agent_id = response.json()["agent_id"]
    await s.until(is_type("agent.status", agent_id=agent_id, state="ready_for_review"))
    return s.live._manager._agents[agent_id]


def assert_clean(workspace):
    assert not list(workspace.tmp_dir.glob("base-probe-*"))
    assert not list(workspace.tmp_dir.glob("behavior-*"))


async def test_boundary_change_has_named_differing_rows_and_private_reasons(tmp_path):
    edits = [
        (PRICING, "if quantity >= minimum:", "if quantity >= minimum and quantity != 10:"),
        ("shop/cart.py", "return sum(self.subtotals())", "return sum(self.subtotals()) + 0"),
        (
            PRICING,
            "return total + total * TAX_PERCENT // 100",
            "return total + total * TAX_PERCENT // 100\n\ndef added(total: int): return total",
        ),
    ]
    async with (
        asyncio.timeout(60),
        live_server(live_settings(tmp_path, behavior_diff=True), Scripts([script(edits)])) as s,
    ):
        rec = await run(s)
        measured = rec.behavior_diffs[1]
        assert isinstance(measured, BehaviorDiff)
        by_id = {item.node_id: item for item in measured.functions}
        price = by_id["shop.pricing:compute_price"]
        assert price.compared > 0 and price.differing > 0, measured
        assert any("quantity=10" in row.call for row in price.rows)
        assert all(not row.unstable for row in price.rows)
        assert len(price.rows) <= 8
        assert by_id["shop.cart:Cart.total"].reason == "method or class (not probed yet)"
        assert by_id["shop.pricing:added"].reason == "added (no base version)"
        assert all("behavior" not in event.model_dump_json() for event in s.events())
        assert_clean(s.live.workspace)


async def test_refactor_compares_without_claiming_equivalence(tmp_path):
    edit = (PRICING, "base = unit_price * quantity", "base = quantity * unit_price")
    async with (
        asyncio.timeout(60),
        live_server(live_settings(tmp_path, behavior_diff=True), Scripts([script([edit])])) as s,
    ):
        rec = await run(s)
        [result] = rec.behavior_diffs[1].functions
        assert result.compared > 0 and result.differing == 0
        assert result.reason is None
        assert result.measurement == "no difference measured on these inputs"
        assert_clean(s.live.workspace)


async def test_missing_isolation_refuses_workers(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "oversight.workspace.capability.check_effect",
        AsyncMock(side_effect=IsolationUnavailable("unavailable")),
    )
    worker = AsyncMock()
    monkeypatch.setattr("oversight.workspace.core.run_behavior_worker", worker)
    edit = (PRICING, "base = unit_price * quantity", "base = quantity * unit_price")
    async with (
        asyncio.timeout(60),
        live_server(live_settings(tmp_path, behavior_diff=True), Scripts([script([edit])])) as s,
    ):
        rec = await run(s)
        [result] = rec.behavior_diffs[1].functions
        assert result.reason == "isolation unavailable"
        worker.assert_not_awaited()
        assert_clean(s.live.workspace)


async def test_global_assignment_is_not_probed(tmp_path, monkeypatch):
    worker = AsyncMock(side_effect=AssertionError("effectful function must not run"))
    monkeypatch.setattr("oversight.workspace.core.run_behavior_worker", worker)
    edit = (
        PRICING,
        "base = unit_price * quantity",
        "global _calls\n    _calls = globals().get('_calls', 0) + 1\n"
        "    base = unit_price * quantity + _calls",
    )
    async with (
        asyncio.timeout(60),
        live_server(live_settings(tmp_path, behavior_diff=True), Scripts([script([edit])])) as s,
    ):
        rec = await run(s)
        [result] = rec.behavior_diffs[1].functions
        assert result.reason == "global or nonlocal assignment"
        assert result.compared == result.differing == 0
        worker.assert_not_awaited()
        assert_clean(s.live.workspace)


async def test_hidden_mutable_default_marks_differing_rows_unstable(tmp_path):
    edits = [
        (PRICING, "extra_percent: int = 0)", "extra_percent: int = 0, _calls: list[int] = [])"),
        (
            PRICING,
            "base = unit_price * quantity",
            "_calls = compute_price.__defaults__[-1]\n    _calls.append(1)\n"
            "    base = unit_price * quantity + len(_calls)",
        ),
    ]
    async with (
        asyncio.timeout(60),
        live_server(live_settings(tmp_path, behavior_diff=True), Scripts([script(edits)])) as s,
    ):
        rec = await run(s)
        [result] = rec.behavior_diffs[1].functions
        assert result.differing > 0 and any(row.unstable for row in result.rows), result
        assert_clean(s.live.workspace)


async def test_module_tier_boundary_is_probed(tmp_path):
    edit = (PRICING, "if quantity >= minimum:", "if quantity >= minimum + 1:")
    async with (
        asyncio.timeout(60),
        live_server(live_settings(tmp_path, behavior_diff=True), Scripts([script([edit])])) as s,
    ):
        rec = await run(s)
        [result] = rec.behavior_diffs[1].functions
        assert any("quantity=50" in row.call for row in result.rows), result
        assert_clean(s.live.workspace)


async def test_default_off_does_not_spawn_or_retain(tmp_path, monkeypatch):
    worker = AsyncMock(side_effect=AssertionError("worker must remain off"))
    monkeypatch.setattr("oversight.workspace.core.run_behavior_worker", worker)
    edit = (PRICING, "base = unit_price * quantity", "base = quantity * unit_price")
    async with (
        asyncio.timeout(60),
        live_server(live_settings(tmp_path), Scripts([script([edit])])) as s,
    ):
        rec = await run(s)
        assert rec.behavior_diffs == {}
        worker.assert_not_awaited()
        assert_clean(s.live.workspace)


async def test_setting_is_opt_in_and_uses_environment_style(tmp_path: Path, monkeypatch):
    env_file = tmp_path / "config"
    env_file.write_text("OVERSIGHT_BEHAVIOR_DIFF=TrUe\n")
    monkeypatch.delenv("OVERSIGHT_BEHAVIOR_DIFF", raising=False)
    assert Settings().behavior_diff is False
    assert Settings.load(env_file).behavior_diff is True
    monkeypatch.setenv("OVERSIGHT_BEHAVIOR_DIFF", "1")
    assert Settings.load(env_file).behavior_diff is False
