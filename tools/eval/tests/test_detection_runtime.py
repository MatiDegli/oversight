"""Development patches exercise the actual product path, including isolation refusal."""

import sys
from pathlib import Path

import pytest
from tools.eval.behavior.catalog import check_baseline, load_patches
from tools.eval.detection.accounting import Budget, task_for
from tools.eval.detection.adapters import Adapter
from tools.eval.detection.config import ARMS, fingerprint
from tools.eval.detection.runtime import measure
from tools.eval.grade import Tariff
from tools.eval.ledger import Ledger


@pytest.mark.anyio
@pytest.mark.parametrize("arm", ARMS, ids=lambda a: a.id)
async def test_dev_patch_product_facts_and_disposable_cleanup(arm, tmp_path, monkeypatch):
    from oversight.workspace import Workspace

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
async def test_dev_actual_bounded_probe_or_real_isolation_unavailability(tmp_path):
    arm = ARMS[3]
    patch = next(p for p in load_patches() if p.id == "d01")
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
        for decision in result.decisions:
            assert decision.difference is not None
            assert decision.difference.differing_count == 1
            assert len(decision.difference.rows) == 1
            assert not decision.difference.rows[0].unstable
    finally:
        ledger.close()
