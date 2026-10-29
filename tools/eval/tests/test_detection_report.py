"""Separate detection and intent, with unchanged denominators even for unavailable probes."""

import pytest
from tools.eval.detection.reporting import repetition, summarize
from tools.eval.detection_types import DetectionEvidence
from tools.eval.grade import Result, Tokens
from tools.eval.grade import Tests as SuiteTests
from tools.eval.ledger import Record


def record(index, *, unavailable=False, rep=1, pilot=False):
    patch_id = f"b{index:02}"
    identity: dict = dict(
        arm_id="detect-4", repetition=rep, config_hash="config", attempt_id=f"a{index}-{rep}"
    )
    evidence = DetectionEvidence(
        patch_id=patch_id,
        patch_sha256="patch",
        labels_sha256="pin",
        configuration={},
        prompt_version="v1",
        prompt_sha256="prompt",
        tests_passed=True,
        detected=not unavailable,
        unprobed=["unreachable"] if unavailable else [],
        unavailable=["deadline"] if unavailable else [],
        let_through=False,
        question=True,
    )
    result = Result(
        **identity,
        profile="baseten",
        task_id="behavior:" + patch_id,
        agent_model="fake",
        outcome="stopped",
        steps=1,
        duration_s=1,
        tokens=Tokens(input=0, output=0),
        cost_usd=0,
        test_result=SuiteTests(baseline="passed", hidden="not_run", policy="not_run"),
        detection=evidence,
    )
    return Record(
        **identity,
        profile="baseten",
        task_id=result.task_id,
        task_hash="patch",
        run_id="run",
        allowance_usd=1,
        result=result,
        status="finished",
        pilot=pilot,
    )


ANSWERS = {f"b{i:02}": "unasked_difference" if i <= 10 else "only_asked" for i in range(1, 21)}


def test_incomplete_coverage_never_passes():
    summary = summarize([], {}, expected_repetitions=2)
    assert not summary["complete"]
    assert not summary["criteria_passed"]
    assert summary["calibration"] == "not available"
    assert len(summary["arms"]) == 4


def test_unprobed_and_unavailable_remain_in_both_denominators():
    rows = [record(i, unavailable=i in {1, 11}) for i in range(1, 21)]
    report = repetition(rows, ANSWERS)
    assert report["complete"] and not report["criteria_passed"]
    groups = report["classification"]["groups"]
    assert groups["unasked_difference"]["let_through"] == 0
    assert groups["only_asked"]["questions"] == 10
    for group in groups.values():
        assert group["denominator"] == 10
        assert group["unavailable_questions"] == group["unprobed"] == 1
    assert "unavailable questions 1/10" in report["criteria"][0]
    assert rows[0].result is not None
    rows[0].result.cost_usd = "unknown"
    rows[0].unknown = True
    assert repetition(rows, ANSWERS)["accounting"]["cost_usd"] == "unknown"


def test_repetitions_pilots_and_wrong_label_pin():
    rows = [record(i, rep=r) for r in (1, 2) for i in range(1, 21)]
    rows.append(record(1, pilot=True))
    report = summarize(rows, ANSWERS, expected_repetitions=2, labels_hash="pin")
    arm = report["arms"]["detect-4"]
    assert arm["pilots"]["attempts"] == 1
    assert arm["including_pilots"]["attempts"] == 41
    assert arm["repetitions"]["2"]["complete"]
    with pytest.raises(Exception, match="labels pin"):
        summarize(rows, ANSWERS, expected_repetitions=2, labels_hash="wrong")


def test_passing_criteria_require_all_twenty_unique_patches():
    rows = [record(i) for i in range(1, 21)]
    for row in rows[10:]:
        assert row.result is not None and row.result.detection is not None
        row.result.detection.let_through = True
        row.result.detection.question = False
    assert repetition(rows, ANSWERS)["criteria_passed"]
    assert not repetition(rows[:-1], ANSWERS)["criteria_passed"]
    assert not repetition([*rows[:-1], rows[0]], ANSWERS)["criteria_passed"]
