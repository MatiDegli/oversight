"""Replace only the zero-call Open pilot identity; preserve completed pilot evidence."""

import sys
from dataclasses import replace
from pathlib import Path

import pytest
from tools.eval.detection import engine
from tools.eval.detection.accounting import pilot_projection
from tools.eval.detection.config import ARMS, PILOT_PATCHES, Assumptions, fingerprint, rates_for
from tools.eval.detection.reporting import summarize
from tools.eval.detection_types import DetectionEvidence
from tools.eval.ledger import Ledger
from tools.eval.tasks import BenchError
from tools.eval.tests.test_detection_plan import simulated_measure

# Pinned after the transport-accounting fix; model source hashes invalidate old pilots.
# Arm.configuration(fake=False), Meta tariffs (1, 2), Baseten tariffs (3, 4), USD/M.
# No fake_choice or assumptions are included in these base configuration hashes.
CURRENT_FINGERPRINTS = {
    "detect-1": "8d6af6b92b60d8650eb8d1d20e74c140af155e999549bdb23991091d2d169cf3",
    "detect-2-v2": "7d01e62a1b76be3b5bd46a39f3567ba108e2684282fc3f2a7b8fb1059a17ad45",
    "detect-3-v2": "d2efe788f3f287027c477afd1166a0a77f90ce9ecae5da08e836dfa05e9cf9c5",
}
# Ledger configurations additionally include fake_choice=None and these assumptions:
ASSUMPTIONS = Assumptions(input_tokens=1000, output_tokens=512, calls=8)
CURRENT_JOB_FINGERPRINTS = {
    "detect-1": "a09a2b423834677d320a3461ea8b02c16326bf4408ef4b87192e9bf7499942c9",
    "detect-2-v2": "95ef86a76a5ad4982edd4ca22814e721f4a877c1e15aaf4a92abfa511536398a",
    "detect-3-v2": "cf47361bbee0f17982792276792f181d5033d0060ca3e90aee234b5176b35e57",
}
RATES = rates_for((1, 2), (3, 4))


@pytest.mark.parametrize("arm", ARMS[:3], ids=lambda a: a.id)
def test_transport_accounting_fingerprints_are_pinned(arm):
    config = arm.configuration(False, RATES)
    assert fingerprint(config) == CURRENT_FINGERPRINTS[arm.id]
    job_config = config | {"fake_choice": None, "assumptions": ASSUMPTIONS.model_dump()}
    assert fingerprint(job_config) == CURRENT_JOB_FINGERPRINTS[arm.id]


def test_open_v3_changes_only_arm_and_prompt_identity():
    arm = ARMS[3]
    assert arm.id == "detect-4-v3"
    old = replace(arm, id="detect-4-v2").configuration(False, RATES)
    new = arm.configuration(False, RATES)
    assert fingerprint(old) == "518fc0a69f69ae89afab3ceed28a32cb45c54ba201ec6d9802db3290becc98f5"
    assert fingerprint(new) != fingerprint(old)
    assert new["prompt_sha256"] != old["prompt_sha256"]
    assert new["system_prompt_sha256"] == old["system_prompt_sha256"]
    assert {k: v for k, v in new.items() if k not in {"arm_id", "prompt_sha256"}} == {
        k: v for k, v in old.items() if k not in {"arm_id", "prompt_sha256"}
    }


async def seed_v2_history(tmp_path, monkeypatch):
    old_arm = replace(ARMS[3], id="detect-4-v2")
    old_patches = PILOT_PATCHES | {"behavior-Spark-Open": "d02"}
    monkeypatch.setattr(engine, "ARMS", ARMS[:3])
    monkeypatch.setattr(engine, "measure", simulated_measure)
    ledger = Ledger(tmp_path / "results.jsonl", 20, None, multi_tariff=True)
    try:
        kwargs: dict = dict(pilot=True, fake_choice="asked_for")
        await engine.run_plan(ledger, RATES, ASSUMPTIONS, Path(sys.executable), **kwargs)
        completed = list(ledger.state.records)
        monkeypatch.setattr(engine, "ARMS", (old_arm,))
        monkeypatch.setattr(engine, "PILOT_PATCHES", old_patches)

        async def no_difference(patch, arm, config, *args):
            assert patch.id == "d02"
            return DetectionEvidence(
                patch_id=patch.id,
                patch_sha256=patch.sha256,
                labels_sha256=None,
                configuration=config,
                prompt_version=config["prompt_version"],
                prompt_sha256=config["prompt_sha256"],
                tests_passed=True,
                detected=False,
                unavailable=["no stable selected difference to classify"],
                let_through=False,
                question=True,
            )

        monkeypatch.setattr(engine, "measure", no_difference)
        with pytest.raises(BenchError, match="detect-4-v2: pilot recorded no model call"):
            await engine.run_plan(ledger, RATES, ASSUMPTIONS, Path(sys.executable), **kwargs)
        old_record = ledger.state.records[-1]
        assert old_record.status == "finished" and old_record.charged_usd == 0
        assert old_record.result is not None
        assert old_record.result.steps == old_record.result.cost_usd == 0
        assert old_record.result.tokens.input == old_record.result.tokens.output == 0
        monkeypatch.setattr(engine, "PILOT_PATCHES", PILOT_PATCHES)
        with pytest.raises(BenchError, match="pilot identity changed; never retry a pilot"):
            await engine.run_plan(ledger, RATES, ASSUMPTIONS, Path(sys.executable), **kwargs)
    finally:
        ledger.close()
    return completed, old_record


@pytest.mark.anyio
async def test_zero_call_v2_stays_durable_while_only_v3_gets_a_new_pilot(tmp_path, monkeypatch):
    completed, old_record = await seed_v2_history(tmp_path, monkeypatch)

    # Reopen the persisted fixture; no canonical ledger or credentials are accessed.
    monkeypatch.setattr(engine, "ARMS", ARMS)
    measured = []

    async def record_measure(patch, arm, *args):
        measured.append((patch.id, arm.id))
        return await simulated_measure(patch, arm, *args)

    monkeypatch.setattr(engine, "measure", record_measure)
    ledger = Ledger(tmp_path / "results.jsonl", 20, None, multi_tariff=True)
    try:
        await engine.run_plan(
            ledger, RATES, ASSUMPTIONS, Path(sys.executable), pilot=True, fake_choice="asked_for"
        )
        assert measured == [("d01", "detect-4-v3")]
        assert len(ledger.state.records) == 5
        assert ledger.state.records[:3] == completed
        assert ledger.state.records[3] == old_record
        jobs = engine.jobs_for(RATES, "asked_for", False, 1, "pin")
        for _, _, config, _ in jobs:
            config["assumptions"] = ASSUMPTIONS.model_dump()
        assert pilot_projection(ledger, jobs)["remaining_attempts"] == 80
        report = summarize(ledger.state.records, {}, expected_repetitions=1)
        assert report["superseded_pilots"] == [
            {
                "arm_id": "detect-4-v2",
                "attempt_id": old_record.attempt_id,
                "status": "superseded",
                "charged_usd": 0,
                "cost_usd": 0,
            }
        ]
        assert report["arms"]["detect-4-v3"]["pilots"]["attempts"] == 1
    finally:
        ledger.close()
