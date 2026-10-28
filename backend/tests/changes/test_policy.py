"""Boundary and missing-evidence checks for the isolated safe-merge evaluator."""

from dataclasses import FrozenInstanceError, replace

import pytest

from oversight.changes._policy import PolicyInputs, SpendSnapshot, assess, merges_alone
from oversight_contracts.common import RuleId
from oversight_contracts.events import SuiteResult
from oversight_contracts.graph import ImpactCone, ImpactEntry
from oversight_contracts.policy import (
    SAFE_MERGE_V1_RULES,
    ChangeAssessment,
    EvidenceRef,
    RuleAssessment,
)

RULES = tuple(sorted(SAFE_MERGE_V1_RULES))


@pytest.fixture
def inputs() -> PolicyInputs:
    return PolicyInputs(
        change_id="change-1",
        agent_id="agent-1",
        revision=2,
        base_commit="base",
        head_commit="head",
        main_commit="main",
        candidate_tree="combined-tree",
        mode="merge_when_safe",
        mode_revision=3,
        suite=SuiteResult(
            outcome="passed", passed=1, failed=0, skipped=0, duration_ms=10, failed_tests=[]
        ),
        cone=ImpactCone(
            seeds=["shop:changed"],
            entries=[ImpactEntry(node_id=f"shop:caller{i}", distance=1) for i in range(5)],
            edges_followed="exact",
            max_depth=8,
            max_nodes=20,
            omitted_by_depth=0,
            omitted_by_limit=0,
            tests_reaching=10,
        ),
        parse_errors=0,
        unresolved_removed=0,
        files=("shop.py",),
        lines_added=30,
        lines_removed=30,
        spend=SpendSnapshot(spent_usd=49.99, budget_usd=100.0, unknown_costs=0, ledger_ok=True),
        modified_existing_test_paths=(),
        deleted_existing_test_paths=(),
        evidence={rule: (EvidenceRef(event_seq=i + 1),) for i, rule in enumerate(RULES)},
    )


def rule(inputs: PolicyInputs, name: RuleId) -> RuleAssessment:
    return next(item for item in assess(inputs).rules if item.rule == name)


def test_complete_assessment_validates_and_preserves_identity(inputs: PolicyInputs) -> None:
    assessment = assess(inputs)
    assert not merges_alone(assessment)
    assert len(assessment.rules) == 5
    assert {item.rule for item in assessment.rules} == set(RULES)
    assert all(item.result == "pass" for item in assessment.rules)
    assert ChangeAssessment.model_validate_json(assessment.model_dump_json()) == assessment
    assert [
        assessment.change_id,
        assessment.agent_id,
        assessment.revision,
        assessment.base_commit,
        assessment.head_commit,
        assessment.main_commit,
        assessment.candidate_tree,
        assessment.mode_revision,
        assessment.policy_version,
    ] == ["change-1", "agent-1", 2, "base", "head", "main", "combined-tree", 3, "safe-merge-v1"]
    for item in assessment.rules:
        assert item.evidence == list(inputs.evidence[item.rule])
        assert RuleAssessment.model_validate(item.model_dump()) == item


def test_inputs_are_frozen(inputs: PolicyInputs) -> None:
    assert inputs.spend is not None
    with pytest.raises(FrozenInstanceError):
        inputs.revision = 99  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(FrozenInstanceError):
        inputs.spend.spent_usd = 0.0  # pyright: ignore[reportAttributeAccessIssue]


@pytest.mark.parametrize(
    ("outcome", "passed", "failed", "skipped", "expected"),
    [
        ("passed", 1, 0, 0, "pass"),
        ("passed", 0, 0, 0, "fail"),
        ("passed", 1, 0, 1, "fail"),
        ("passed", 1, 1, 0, "fail"),
        ("failed", 1, 0, 0, "fail"),
        ("failed", 0, 1, 0, "fail"),
        ("error", 1, 0, 0, "unknown"),
        ("timeout", 1, 0, 0, "unknown"),
        ("error", 0, 1, 0, "unknown"),
        ("timeout", 0, 0, 0, "unknown"),
        ("passed", -1, 0, 0, "unknown"),
    ],
)
def test_combined_suite(
    inputs: PolicyInputs, outcome: str, passed: int, failed: int, skipped: int, expected: str
) -> None:
    suite = SuiteResult.model_validate(
        dict(
            outcome=outcome,
            passed=passed,
            failed=failed,
            skipped=skipped,
            duration_ms=1,
            failed_tests=[],
        )
    )
    changed = replace(inputs, suite=suite)
    assert rule(changed, "combined_tests").result == expected
    assert not merges_alone(assess(changed))


@pytest.mark.parametrize(("count", "expected"), [(0, "pass"), (5, "pass"), (6, "fail")])
def test_cone_counts_entries_only(inputs: PolicyInputs, count: int, expected: str) -> None:
    assert inputs.cone is not None
    cone = inputs.cone.model_copy(
        update={
            "entries": [ImpactEntry(node_id=f"shop:f{i}", distance=1) for i in range(count)],
            "seeds": [f"shop:seed{i}" for i in range(20)],
            "tests_reaching": 100,
        }
    )
    result = rule(replace(inputs, cone=cone), "small_cone")
    assert result.result == expected
    assert result.observed is not None and result.observed["nodes"] == count


@pytest.mark.parametrize("counter", ["omitted_by_depth", "omitted_by_limit"])
def test_cone_omissions_fail(inputs: PolicyInputs, counter: str) -> None:
    assert inputs.cone is not None
    cone = inputs.cone.model_copy(update={counter: 1})
    assert rule(replace(inputs, cone=cone), "small_cone").result == "fail"


@pytest.mark.parametrize("counter", ["parse_errors", "unresolved_removed"])
def test_cone_quality_counters(inputs: PolicyInputs, counter: str) -> None:
    changed = replace(inputs, **{counter: 1})
    assert rule(changed, "small_cone").result == "fail"


@pytest.mark.parametrize(
    ("count", "expected"), [(0, "fail"), (1, "pass"), (3, "pass"), (4, "fail")]
)
def test_file_limits(inputs: PolicyInputs, count: int, expected: str) -> None:
    changed = replace(inputs, files=tuple(f"module{i}.py" for i in range(count)))
    assert rule(changed, "small_change").result == expected


@pytest.mark.parametrize(
    ("added", "removed", "expected"),
    [
        (0, 0, "pass"),
        (60, 0, "pass"),
        (0, 60, "pass"),
        (30, 30, "pass"),
        (61, 0, "fail"),
        (0, 61, "fail"),
        (30, 31, "fail"),
        (-1, 1, "unknown"),
    ],
)
def test_total_line_limit(inputs: PolicyInputs, added: int, removed: int, expected: str) -> None:
    changed = replace(inputs, lines_added=added, lines_removed=removed)
    assert rule(changed, "small_change").result == expected


@pytest.mark.parametrize(
    ("spent", "budget", "unknown", "healthy", "expected"),
    [
        (49.99, 100.0, 0, True, "pass"),
        (50.0, 100.0, 0, True, "fail"),
        (50.01, 100.0, 0, True, "fail"),
        (0.0, 100.0, 0, True, "pass"),
        (4.999, 10.0, 0, True, "pass"),
        (5.0, 10.0, 0, True, "fail"),
        (49.99, 100.0, 1, True, "fail"),
        (49.99, 100.0, 0, False, "fail"),
        (0.0, 0.0, 0, True, "unknown"),
        (0.0, -1.0, 0, True, "unknown"),
        (-1.0, 100.0, 0, True, "unknown"),
        (float("nan"), 100.0, 0, True, "unknown"),
        (float("inf"), 100.0, 0, True, "unknown"),
        (0.0, float("inf"), 0, True, "unknown"),
        (0.0, 100.0, -1, True, "unknown"),
    ],
)
def test_spend_limits(
    inputs: PolicyInputs, spent: float, budget: float, unknown: int, healthy: bool, expected: str
) -> None:
    spend = SpendSnapshot(
        spent_usd=spent, budget_usd=budget, unknown_costs=unknown, ledger_ok=healthy
    )
    assert rule(replace(inputs, spend=spend), "known_spend").result == expected


@pytest.mark.parametrize(
    ("modified", "deleted", "expected"),
    [((), (), "pass"), (("tests/test_old.py",), (), "fail"), ((), ("tests/test_old.py",), "fail")],
)
def test_existing_versus_added_tests(
    inputs: PolicyInputs, modified: tuple[str, ...], deleted: tuple[str, ...], expected: str
) -> None:
    changed = replace(
        inputs,
        files=("shop.py", "tests/test_new.py"),
        modified_existing_test_paths=modified,
        deleted_existing_test_paths=deleted,
    )
    assert rule(changed, "existing_tests_untouched").result == expected
    assert not merges_alone(assess(changed))


@pytest.mark.parametrize(
    ("field", "affected"),
    [
        ("suite", "combined_tests"),
        ("cone", "small_cone"),
        ("parse_errors", "small_cone"),
        ("unresolved_removed", "small_cone"),
        ("files", "small_change"),
        ("lines_added", "small_change"),
        ("lines_removed", "small_change"),
        ("spend", "known_spend"),
        ("modified_existing_test_paths", "existing_tests_untouched"),
        ("deleted_existing_test_paths", "existing_tests_untouched"),
    ],
)
def test_each_missing_observation_is_unknown(
    inputs: PolicyInputs, field: str, affected: RuleId
) -> None:
    changed = replace(inputs, **{field: None})
    assessment = assess(changed)
    assert len(assessment.rules) == 5
    assert rule(changed, affected).result == "unknown"
    assert rule(changed, affected).observed is None
    assert all(item.result == "pass" for item in assessment.rules if item.rule != affected)
    assert not merges_alone(assessment)


@pytest.mark.parametrize("field", ["spent_usd", "budget_usd", "unknown_costs", "ledger_ok"])
def test_each_missing_spend_field_is_unknown(inputs: PolicyInputs, field: str) -> None:
    assert inputs.spend is not None
    spend = replace(inputs.spend, **{field: None})
    assert rule(replace(inputs, spend=spend), "known_spend").result == "unknown"


@pytest.mark.parametrize("missing_rule", RULES)
@pytest.mark.parametrize("empty", [False, True])
def test_missing_evidence_is_unknown(
    inputs: PolicyInputs, missing_rule: RuleId, empty: bool
) -> None:
    evidence = dict(inputs.evidence)
    if empty:
        evidence[missing_rule] = ()
    else:
        del evidence[missing_rule]
    changed = replace(inputs, evidence=evidence)
    assert rule(changed, missing_rule).result == "unknown"
    assert rule(changed, missing_rule).observed is not None
    assert not merges_alone(assess(changed))


def test_no_observations_or_evidence_produces_all_unknown(inputs: PolicyInputs) -> None:
    bare = PolicyInputs(
        change_id=inputs.change_id,
        agent_id=inputs.agent_id,
        revision=inputs.revision,
        base_commit=inputs.base_commit,
        head_commit=inputs.head_commit,
        main_commit=inputs.main_commit,
        mode=inputs.mode,
        mode_revision=inputs.mode_revision,
    )
    assessment = assess(bare)
    assert len(assessment.rules) == 5
    assert all(item.result == "unknown" for item in assessment.rules)
    assert ChangeAssessment.model_validate_json(assessment.model_dump_json()) == assessment
    assert not merges_alone(assessment)


def test_ask_me_never_merges(inputs: PolicyInputs) -> None:
    assessment = assess(replace(inputs, mode="ask_me"))
    assert all(item.result == "pass" for item in assessment.rules)
    assert not merges_alone(assessment)


@pytest.mark.parametrize("blocked_rule", RULES)
@pytest.mark.parametrize("result", ["fail", "unknown"])
def test_every_rule_must_pass(inputs: PolicyInputs, blocked_rule: RuleId, result: str) -> None:
    data = assess(inputs).model_dump()
    for item in data["rules"]:
        if item["rule"] == blocked_rule:
            item["result"] = result
    assert not merges_alone(ChangeAssessment.model_validate(data))


def test_other_policy_versions_do_not_merge(inputs: PolicyInputs) -> None:
    data = assess(inputs).model_dump()
    data["policy_version"] = "future-policy"
    assert not merges_alone(ChangeAssessment.model_validate(data))


def test_evaluation_is_repeatable_and_does_not_mutate_inputs(inputs: PolicyInputs) -> None:
    assert inputs.cone is not None and inputs.suite is not None
    before = (inputs.cone.model_dump(), inputs.suite.model_dump(), dict(inputs.evidence))
    first = assess(inputs)
    second = assess(inputs)
    assert first == second
    first.rules.clear()
    assert not merges_alone(second)
    assert not merges_alone(first)
    assert (inputs.cone.model_dump(), inputs.suite.model_dump(), dict(inputs.evidence)) == before
