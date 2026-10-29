"""Pilots, histories, cleanup and immutable resumptions fail closed before execution."""

import sys
from pathlib import Path

import pytest
from tools.eval.behavior.catalog import load_patches
from tools.eval.detection.accounting import pilot_projection, task_for
from tools.eval.detection.config import Assumptions
from tools.eval.detection.engine import jobs_for, run_plan
from tools.eval.detection_types import DetectionEvidence
from tools.eval.grade import Tariff
from tools.eval.ledger import Ledger
from tools.eval.tasks import BenchError


@pytest.mark.anyio
async def test_missing_pilots_refuse_before_measurement(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tools.eval.detection.engine.measure", lambda *a, **k: pytest.fail("measure")
    )
    ledger = Ledger(tmp_path / "results.jsonl", 20, None, multi_tariff=True)
    try:
        with pytest.raises(BenchError, match="pilot"):
            await run_plan(
                ledger,
                {p: Tariff(input=0, output=0) for p in ("meta", "baseten")},
                Assumptions(input_tokens=1000, output_tokens=512, calls=40),
                tmp_path / "python",
                pilot=False,
                labels_hash="synthetic",
                fake_choice="asked_for",
                seed=42,
            )
        assert not ledger.state.records
    finally:
        ledger.close()


def test_dev_examples_remain_separate():
    assert {p.id for p in load_patches() if p.set == "dev"} == {"d01", "d02", "d03", "d04"}


RATES = {p: Tariff(input=0, output=0) for p in ("meta", "baseten")}
ASSUMPTIONS = Assumptions(input_tokens=1000, output_tokens=512, calls=40)


async def simulated_measure(
    patch, arm, config, adapter, directory, demo_python, labels_hash, run_id=None
):
    return DetectionEvidence(
        patch_id=patch.id,
        patch_sha256=patch.sha256,
        labels_sha256=labels_hash,
        configuration=config,
        prompt_version=config["prompt_version"],
        prompt_sha256=config["prompt_sha256"],
        tests_passed=True,
        detected=None,
        let_through=True,
        question=False,
    )


@pytest.mark.anyio
async def test_pilots_resume_and_repetitions_keep_attempt_ids(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.eval.detection.engine.measure", simulated_measure)
    out = tmp_path / "results.jsonl"
    ledger = Ledger(out, 20, None, multi_tariff=True)
    try:
        kwargs: dict = dict(fake_choice="asked_for", seed=42)
        await run_plan(ledger, RATES, ASSUMPTIONS, Path(sys.executable), pilot=True, **kwargs)
        assert len(ledger.state.records) == 4
        pilot_ids = [r.attempt_id for r in ledger.state.records]
        await run_plan(ledger, RATES, ASSUMPTIONS, Path(sys.executable), pilot=True, **kwargs)
        assert [r.attempt_id for r in ledger.state.records] == pilot_ids
        await run_plan(
            ledger,
            RATES,
            ASSUMPTIONS,
            Path(sys.executable),
            pilot=False,
            labels_hash="synthetic",
            repetitions=2,
            **kwargs,
        )
        assert len(ledger.state.records) == 144
        ids = [r.attempt_id for r in ledger.state.records]
        assert sum(r.arm_id == "detect-1" and not r.pilot for r in ledger.state.records) == 20
        out.unlink()  # Crash between authoritative ledger and derived output.
    finally:
        ledger.close()
    ledger = Ledger(out, 20, None, multi_tariff=True)
    try:
        assert len(out.read_text().splitlines()) == 144
        await run_plan(
            ledger,
            RATES,
            ASSUMPTIONS,
            Path(sys.executable),
            pilot=False,
            labels_hash="synthetic",
            repetitions=2,
            **kwargs,
        )
        assert [r.attempt_id for r in ledger.state.records] == ids
        assert ledger.spent == 0
        with pytest.raises(BenchError, match="seed"):
            await run_plan(
                ledger,
                RATES,
                ASSUMPTIONS,
                Path(sys.executable),
                pilot=False,
                labels_hash="synthetic",
                fake_choice="asked_for",
                seed=7,
                repetitions=2,
            )
        with pytest.raises(BenchError, match="configuration"):
            await run_plan(
                ledger,
                RATES,
                ASSUMPTIONS,
                Path(sys.executable),
                pilot=False,
                labels_hash="synthetic",
                fake_choice="cannot_tell",
                seed=42,
            )
    finally:
        ledger.close()


@pytest.mark.anyio
async def test_cleanup_failure_retains_reservation_and_blocks_resume(tmp_path, monkeypatch):
    async def broken(*args):
        raise BenchError("incomplete disposable runtime cleanup")

    monkeypatch.setattr("tools.eval.detection.engine.measure", broken)
    ledger = Ledger(tmp_path / "results.jsonl", 20, None, multi_tariff=True)
    try:
        with pytest.raises(BenchError, match="cleanup"):
            await run_plan(
                ledger,
                RATES,
                ASSUMPTIONS,
                Path(sys.executable),
                pilot=True,
                fake_choice="asked_for",
            )
        assert ledger.state.records[0].status == "reserved"
        with pytest.raises(BenchError, match="unsettled"):
            await run_plan(
                ledger,
                RATES,
                ASSUMPTIONS,
                Path(sys.executable),
                pilot=True,
                fake_choice="asked_for",
            )
        assert len(ledger.state.records) == 1
    finally:
        ledger.close()


@pytest.mark.anyio
@pytest.mark.parametrize("obstacle", ["charges", "unknown", "reservation", "prompt"])
async def test_history_and_pilot_drift_block_projection(tmp_path, monkeypatch, obstacle):
    monkeypatch.setattr("tools.eval.detection.engine.measure", simulated_measure)
    ledger = Ledger(tmp_path / "results.jsonl", 20, None, multi_tariff=True)
    try:
        await run_plan(
            ledger, RATES, ASSUMPTIONS, Path(sys.executable), pilot=True, fake_choice="asked_for"
        )
        if obstacle == "charges":
            ledger.state.records[0].charged_usd = 19
            for r in ledger.state.records:
                assert r.result is not None
                r.result.cost_usd = 1
        elif obstacle == "unknown":
            ledger.state.records[0].unknown = True
        elif obstacle == "reservation":
            patch = next(p for p in load_patches() if p.id == "d04")
            ledger.reserve(task_for(patch), "other", "other-run", 0.1)
        jobs = jobs_for(RATES, "asked_for", False, 1, "synthetic")
        for _, _, config, _ in jobs:
            config["assumptions"] = ASSUMPTIONS.model_dump()
            if obstacle == "prompt":
                config["prompt_sha256"] = "changed"
        with pytest.raises(BenchError):
            pilot_projection(ledger, jobs)
    finally:
        ledger.close()
