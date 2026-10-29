"""The scripted fake publishes independent bounded behavior and caller evidence."""

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
        live_server(
            live_settings(tmp_path, behavior_diff=True, measured_cone=True),
            Scripts([script(edits)]),
        ) as s,
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
        facts = next(e.payload for e in s.events() if e.type == "change.facts_recorded")
        assert facts.behavior_diff is not None
        public = {item.node_id: item for item in facts.behavior_diff}
        assert public[price.node_id].differing_count == price.differing
        assert any("quantity=10" in row.inputs for row in public[price.node_id].rows)
        assert all(row.before.startswith('{"kind":"value"') for row in public[price.node_id].rows)
        assert public["shop.cart:Cart.total"].not_probed == "unsupported node kind"
        assert public["shop.pricing:added"].not_probed == "version unavailable"
        for event in s.events():
            if event.type == "change.facts_recorded":
                assert event.payload.measured_cone is not None
            serialized = event.model_dump_json()
            for private in (
                "method or class (not probed yet)",
                "added (no base version)",
                "fingerprint",
                "base-probe-",
            ):
                assert private not in serialized
        assert_clean(s.live.workspace)


async def test_refactor_compares_without_claiming_equivalence(tmp_path):
    edit = (PRICING, "base = unit_price * quantity", "base = quantity * unit_price")
    async with (
        asyncio.timeout(60),
        live_server(
            live_settings(tmp_path, behavior_diff=True, measured_cone=True),
            Scripts([script([edit])]),
        ) as s,
    ):
        rec = await run(s)
        [result] = rec.behavior_diffs[1].functions
        assert result.compared > 0 and result.differing == 0
        assert result.reason is None
        assert result.measurement == "no difference measured on these inputs"
        from oversight.changes._measured import project_measured
        from oversight_contracts.review import ChangeTarget

        events = s.events()
        facts = next(e.payload for e in events if e.type == "change.facts_recorded")
        proposal = next(e.payload for e in events if e.type == "change.proposed")
        target = ChangeTarget.model_validate(
            facts.model_dump(include=set(ChangeTarget.model_fields))
        )
        assert facts.head_commit == proposal.head_commit and facts.revision == proposal.revision
        assert facts.measured_cone == project_measured(rec.measured_cones[1], target)
        assert {row.node_id for row in facts.measured_cone} == {
            entry.node_id for entry in proposal.impact.entries
        }
        [public] = facts.behavior_diff
        assert public.compared_count > 0 and public.differing_count == 0
        assert public.rows == []
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
        facts = next(e.payload for e in s.events() if e.type == "change.facts_recorded")
        execution = next(e for e in rec.executions.values() if e.phase == "proposal")
        assert (facts.change_id, facts.revision, facts.base_commit, facts.head_commit) == (
            rec.change_id,
            1,
            rec.base_commit,
            rec.head_commit,
        )
        assert facts.main_commit == rec.base_commit
        assert facts.candidate_tree == execution.tested_tree
        assert facts.head_commit == execution.tested_commit
        assert facts.behavior_diff is not None
        [public] = facts.behavior_diff
        assert public.node_id == result.node_id
        assert public.compared_count == public.differing_count == 0
        assert public.rows == [] and public.not_probed == "probe unavailable"
        serialized = facts.model_dump_json()
        assert "isolation unavailable" not in serialized
        assert str(s.live.workspace.tmp_dir) not in serialized
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
        assert rec.measured_cones == {}
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


async def test_caller_setting_is_opt_in_and_uses_environment_style(tmp_path: Path, monkeypatch):
    env_file = tmp_path / "config"
    env_file.write_text("OVERSIGHT_MEASURED_CONE=TrUe\n")
    monkeypatch.delenv("OVERSIGHT_MEASURED_CONE", raising=False)
    assert Settings().measured_cone is False
    assert Settings.load(env_file).measured_cone is True
    monkeypatch.setenv("OVERSIGHT_MEASURED_CONE", "1")
    assert Settings.load(env_file).measured_cone is False


@pytest.mark.parametrize("behavior_diff", [False, True])
@pytest.mark.parametrize("measured_cone", [False, True])
async def test_direct_and_caller_measurement_switches_are_independent(
    tmp_path, monkeypatch, behavior_diff, measured_cone
):
    from oversight.changes._measured import unavailable_measured

    direct = BehaviorDiff(revision=1, functions=())
    probe = AsyncMock(return_value=direct)
    callers = AsyncMock(
        side_effect=lambda _ctx, _rec, *, target, impact: unavailable_measured(
            target, impact, "test recording unavailable"
        )
    )
    monkeypatch.setattr("oversight.changes._proposal.probe_behavior", probe)
    monkeypatch.setattr("oversight.changes._proposal.measure_cone", callers)
    edit = (PRICING, "base = unit_price * quantity", "base = quantity * unit_price")
    settings = live_settings(tmp_path, behavior_diff=behavior_diff, measured_cone=measured_cone)
    async with asyncio.timeout(60), live_server(settings, Scripts([script([edit])])) as s:
        rec = await run(s)
        assert probe.await_count == int(behavior_diff)
        assert callers.await_count == int(measured_cone)
        assert rec.behavior_diffs == ({1: direct} if behavior_diff else {})
        assert set(rec.measured_cones) == ({1} if measured_cone else set())
        facts = next(e.payload for e in s.events() if e.type == "change.facts_recorded")
        assert facts.behavior_diff == ([] if behavior_diff else None)
        if measured_cone:
            assert facts.measured_cone
            assert all("test recording unavailable" in row.problems for row in facts.measured_cone)
        else:
            assert facts.measured_cone is None
        assert_clean(s.live.workspace)
