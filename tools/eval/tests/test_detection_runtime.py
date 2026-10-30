"""Development patches exercise the actual product path, including isolation refusal."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from tools.eval.behavior.catalog import check_baseline, load_patches
from tools.eval.detection.accounting import Budget, task_for
from tools.eval.detection.adapters import Adapter
from tools.eval.detection.config import ARMS, Assumptions, fingerprint
from tools.eval.detection.engine import run_attempt
from tools.eval.detection.reporting import repetition
from tools.eval.detection.runtime import measure
from tools.eval.grade import Result, Tariff
from tools.eval.ledger import Ledger


@pytest.mark.anyio
@pytest.mark.parametrize("arm", ARMS, ids=lambda a: a.id)
async def test_dev_patch_product_facts_and_disposable_cleanup(arm, tmp_path, monkeypatch):
    from oversight.changes import ChangeManager
    from oversight.workspace import Workspace

    real_manager = ChangeManager

    def manager(*args, **kwargs):
        assert kwargs["review_card_limits"] is arm.limits
        return real_manager(*args, **kwargs)

    monkeypatch.setattr("tools.eval.detection.runtime.ChangeManager", manager)
    runtime_roots = []
    real_acquire = Workspace.acquire_runtime

    async def acquire(workspace):
        runtime_roots.append(workspace.runtime_root)
        await real_acquire(workspace)

    monkeypatch.setattr(Workspace, "acquire_runtime", acquire)
    patch = next(p for p in load_patches() if p.id == ("d04" if arm.behavior else "d01"))
    rates = {p: Tariff(input=0, output=0) for p in ("meta", "baseten")}
    config = arm.configuration(True, rates)
    ledger = Ledger(tmp_path / "results.jsonl", 20, None, multi_tariff=True)
    try:
        record = ledger.reserve(
            task_for(patch), arm.profile, "test", 1, arm_id=arm.id, config_hash=fingerprint(config)
        )
        budget = Budget(ledger, record, Tariff(input=0, output=0), 0.001, fake=True)
        adapter = Adapter(arm, budget, fake_choice="asked_for" if arm.profile != "none" else None)
        result = await measure(
            patch, arm, config, adapter, tmp_path / "attempt", Path(sys.executable), None
        )
        assert result.tests_passed and result.facts
        assert runtime_roots and all(not root.exists() for root in runtime_roots)
        assert (tmp_path / "attempt/events.sqlite").exists()
        if arm.behavior:
            assert result.facts[-1].behavior_diff is not None
            assert any(
                n.not_probed == "unsupported node kind" for n in result.facts[-1].behavior_diff
            )
            assert result.question and not result.let_through
        elif arm.profile == "none":
            assert result.let_through and not result.decisions and budget.sends == 0
        else:
            assert result.let_through and len(result.decisions) == 1
        check_baseline()
    finally:
        ledger.close()


@pytest.mark.anyio
@pytest.mark.parametrize("patch_id, calls_model", [("d01", True), ("d02", False)])
async def test_dev_actual_bounded_probe_or_real_isolation_unavailability(
    tmp_path, patch_id, calls_model
):
    arm = ARMS[3]
    patch = next(p for p in load_patches() if p.id == patch_id)
    rates = {p: Tariff(input=0, output=0) for p in ("meta", "baseten")}
    config = arm.configuration(True, rates)
    ledger = Ledger(tmp_path / "results.jsonl", 20, None, multi_tariff=True)
    try:
        rec = ledger.reserve(task_for(patch), arm.profile, "test", 1, arm_id=arm.id)
        budget = Budget(ledger, rec, rates["baseten"], 0.001, fake=True)
        adapter = Adapter(arm, budget, fake_choice="cannot_tell")
        result = await measure(
            patch, arm, config, adapter, tmp_path / "attempt", Path(sys.executable), None
        )
        nodes = result.facts[-1].behavior_diff
        assert nodes
        assert any(n.compared_count > 0 or n.not_probed for n in nodes)
        assert result.question
        if any(reason.startswith("isolation unavailable:") for reason in result.unavailable):
            pytest.skip("real isolation unavailable; model reachability cannot be measured")
        assert bool(budget.sends) is calls_model
        assert bool(result.decisions) is calls_model
        if not calls_model:
            assert result.unavailable == ["no stable selected difference to classify"]
            assert budget.sends == budget.receipts == 0
        for decision in result.decisions:
            assert decision.diagnostics is not None
            assert decision.difference is not None
            assert decision.difference.differing_count == 1
            assert len(decision.difference.rows) == 1
            assert not decision.difference.rows[0].unstable
    finally:
        ledger.close()


@pytest.mark.anyio
@pytest.mark.parametrize("arm", ARMS[2:], ids=lambda a: a.id)
async def test_forced_isolation_unavailable_is_recorded_and_counted(arm, tmp_path, monkeypatch):
    from oversight.workspace import IsolationUnavailable, Workspace

    reason = "forced test isolation refusal"
    effect = AsyncMock(side_effect=IsolationUnavailable(reason))
    monkeypatch.setattr("oversight.workspace.capability.check_effect", effect)
    check_isolation = Workspace.check_isolation

    async def check_unavailable(workspace):
        capability = await check_isolation(workspace)
        assert workspace.isolation.available is False
        return capability

    monkeypatch.setattr(Workspace, "check_isolation", check_unavailable)
    patch = next(p for p in load_patches() if p.id == "d01")
    rates = {p: Tariff(input=0, output=0) for p in ("meta", "baseten")}
    config = arm.configuration(True, rates)
    config["assumptions"] = Assumptions(input_tokens=1000, output_tokens=512, calls=40).model_dump()
    out = tmp_path / "results.jsonl"
    ledger = Ledger(out, 20, None, multi_tariff=True)
    try:
        await run_attempt(
            ledger,
            (patch, arm, config, 1),
            Assumptions(input_tokens=1000, output_tokens=512, calls=40),
            Path(sys.executable),
            fake_choice="asked_for",
            pilot=False,
            labels_hash=None,
            factory=None,
        )
        effect.assert_awaited_once()
        stored = Result.model_validate_json(out.read_text())
        result = stored.detection
        assert result is not None and result.tests_passed
        assert "isolation unavailable: " + reason in result.unavailable
        assert result.question and not result.let_through
        assert result.unprobed and not result.decisions
        assert ledger.state.records[0].status == "finished"
        # Synthetic labels exercise both denominators without consulting the labels sheet.
        for answer in ("unasked_difference", "only_asked"):
            report = repetition(ledger.state.records, {patch.id: answer})
            group = report["classification"]["groups"][answer]
            assert group["denominator"] == 10 and group["executed"] == 1
            assert group["questions"] == group["unavailable_questions"] == group["unprobed"] == 1
            assert group["let_through"] == 0
            assert "unavailable questions 1/10" in " ".join(report["criteria"])
            assert report["attempts"][0]["result"]["detection"] == result.model_dump(mode="json")
    finally:
        ledger.close()
