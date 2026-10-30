"""Separate detection and intent, with unchanged denominators even for unavailable probes."""

from typing import get_args

import pytest
from tools.eval.detection import reporting
from tools.eval.detection.reporting import repetition, summarize
from tools.eval.detection_types import DecisionEvidence, DetectionEvidence, ValidationStage
from tools.eval.grade import Result, Tokens
from tools.eval.grade import Tests as SuiteTests
from tools.eval.ledger import Record

from oversight_contracts.review import ChangeTarget


def record(index, *, unavailable=False, rep=1, pilot=False):
    patch_id = f"b{index:02}"
    identity: dict = dict(
        arm_id="detect-4-v3", repetition=rep, config_hash="config", attempt_id=f"a{index}-{rep}"
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
    arm = report["arms"]["detect-4-v3"]
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


def test_question_sources_and_per_row_causes():
    rows = [record(i) for i in (1, 2, 3)]
    for index, row in enumerate(rows):
        assert row.result is not None and row.result.detection is not None
        evidence = row.result.detection
        evidence.decisions = [
            DecisionEvidence(
                target=ChangeTarget(
                    change_id="change-1",
                    revision=1,
                    base_commit="base",
                    head_commit="head",
                    main_commit="main",
                    candidate_tree="tree",
                ),
                node_id="module:fn",
                task="task",
                difference=None,
                choice="not_asked_for" if index == 0 else "asked_for",
            )
        ]
        if index == 0:
            evidence.decisions *= 2  # Consultation counts rows, not decisions.
        if index == 1:
            evidence.unavailable = [
                "module:a: probe unavailable",
                "module:b: probe unavailable",
                "new reason",
                "review cards: 2 nodes omitted by classification limit",
            ]
        if index == 2:
            evidence.let_through, evidence.question = True, False
            evidence.decisions = []
    report = repetition(rows, ANSWERS)
    group = report["classification"]["groups"]["unasked_difference"]
    assert group["model_asked"] == 2  # Includes a decision with incomplete evidence.
    assert group["questions_from_model"] == group["unavailable_questions"] == 1
    assert group["questions"] == 2 and group["let_through"] == 1
    assert list(group["unavailable_causes"].items()) == [
        ("probe unavailable", 1),
        ("nodes omitted by classification limit", 1),
        ("other", 1),
    ]
    assert report["classification"]["groups"]["only_asked"]["unavailable_causes"] == {}
    assert report["criteria"] == [
        "Unasked differences let through: 1/10; criterion 0; unavailable questions 1/10; "
        "questions from the model 1/10",
        "Only-asked needless questions: 0/10; criterion <=2; unavailable questions 0/10; "
        "questions from the model 0/10",
    ]


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("unsupported node kind", "method or class not probed"),
        ("probe unavailable", "probe unavailable"),
        ("version unavailable", "version unavailable"),
        ("behavior comparison incomplete", "comparison incomplete"),
        ("caller comparison incomplete", "comparison incomplete"),
        ("comparison or repetition incomplete", "repetition incomplete"),
        ("caller comparison or repetition incomplete", "repetition incomplete"),
        ("differing observations not retained", "observations not retained"),
        ("2 other differences unclassified", "other differences unclassified"),
        ("13 nodes omitted by classification limit", "nodes omitted by classification limit"),
        ("no stable selected difference to classify", "no difference to classify"),
        ("no behavior evidence; empty probe is not equivalence", "no behavior evidence"),
        ("isolation unavailable: disabled: reason", "isolation unavailable"),
        ("row witness unavailable", "row witness unavailable"),
        ("unstable or equal observation", "unstable or equal observation"),
        ("typed identity unavailable", "typed identity unavailable"),
        ("display sample truncated", "display sample truncated"),
        ("typed display incomplete", "typed display incomplete"),
        ("pending; 0/8 classified", "review cards pending"),
        ("truncated; 3/8 classified", "review cards truncated"),
        ("failed; 0/1 classified", "review cards failed"),
        ("review deadline exhausted; remaining nodes unclassified", "review deadline exhausted"),
        ("classification identity mismatch", "classification identity mismatch"),
        ("contractual card limit", "contractual card limit"),
        ("unavailable (ValueError)", "review unavailable"),
        ("publication or classification unavailable (RuntimeError)", "review unavailable"),
        ("a new unknown reason", "other"),
    ],
)
def test_reason_causes_strip_node_ids_and_counts(reason, expected):
    for prefix in ("", "module:fn: ", "review cards: ", "review cards: module:fn: "):
        if reason.startswith("isolation unavailable") and prefix:
            continue  # Runtime emits isolation reasons without a node or review-card prefix.
        assert reporting.cause(prefix + reason) == expected


@pytest.mark.parametrize(
    "stage", ["invalid_response", *(s for s in get_args(ValidationStage.__value__) if s != "none")]
)
def test_each_decision_unavailable_stage_has_a_cause(stage):
    for reason in (stage, f"module:fn: {stage}", f"review cards: module:fn: unavailable ({stage})"):
        assert reporting.cause(reason) == f"decision unavailable: {stage}"
