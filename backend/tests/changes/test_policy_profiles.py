"""Hand-built producer evidence and exact profile authority, without I/O or models."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from oversight.agents import FAKE_MODEL, FAKE_MODEL_B, MUSE_SPARK, MUSE_SPARK_OPEN
from oversight.changes._facts import (
    AddedTestsInventory,
    IntegrityStatus,
    added_tests_inventory,
    integrity_status,
)
from oversight.changes._policy import PolicyInputs, SpendSnapshot, assess, merges_alone
from oversight.changes._state import ChangeRecord
from oversight_contracts.common import RuleId
from oversight_contracts.events import ModelProfile, SuiteExecution, SuiteResult
from oversight_contracts.graph import ImpactCone
from oversight_contracts.policy import POLICY_RULES, ChangeAssessment, EvidenceRef, RuleAssessment
from oversight_contracts.review import AddedTestProbe, CrossReview, IntegrityFact, ReviewFinding

PROFILE = "safe-merge-probes-v1"
TARGET = dict(
    change_id="change-1",
    revision=1,
    base_commit="base",
    head_commit="head",
    main_commit="main",
    candidate_tree="tree",
)


def review(
    author: ModelProfile = MUSE_SPARK,
    reviewer: ModelProfile = MUSE_SPARK_OPEN,
    /,
    **updates: object,
) -> CrossReview:
    data = dict(
        **TARGET,
        author_model=author.model,
        reviewer_model=reviewer.model,
        reviewer_provider=reviewer.provider,
        reasoning=reviewer.reasoning,
        verdict="clean",
        findings=[],
        complete=True,
        problems=[],
    )
    return CrossReview.model_validate({**data, **updates})


@pytest.fixture
def ready() -> PolicyInputs:
    return PolicyInputs(
        change_id="change-1",
        revision=1,
        base_commit="base",
        head_commit="head",
        main_commit="main",
        candidate_tree="tree",
        agent_id="agent-1",
        mode="merge_when_safe",
        mode_revision=1,
        suite=SuiteResult(
            outcome="passed", passed=1, failed=0, skipped=0, duration_ms=1, failed_tests=[]
        ),
        cone=ImpactCone(
            seeds=[],
            entries=[],
            edges_followed="exact",
            max_depth=1,
            max_nodes=5,
            omitted_by_depth=0,
            omitted_by_limit=0,
            tests_reaching=0,
        ),
        parse_errors=0,
        unresolved_removed=0,
        files=("shop.py",),
        lines_added=1,
        lines_removed=0,
        spend=SpendSnapshot(spent_usd=0.0, budget_usd=1.0, unknown_costs=0, ledger_ok=True),
        modified_existing_test_paths=(),
        deleted_existing_test_paths=(),
        author=MUSE_SPARK,
        review=review(),
        integrity=(),
        integrity_status=IntegrityStatus(complete=True),
        added_tests=(),
        added_tests_inventory=AddedTestsInventory(test_ids=(), complete=True),
        evidence={
            rule: (EvidenceRef(event_seq=100 + i),)
            for i, rule in enumerate(sorted(POLICY_RULES[PROFILE]))
        },
    )


def result(inputs: PolicyInputs, name: RuleId) -> RuleAssessment:
    return next(rule for rule in assess(inputs, PROFILE).rules if rule.rule == name)


@pytest.mark.parametrize("profile", POLICY_RULES)
def test_exact_profiles(ready: PolicyInputs, profile: str) -> None:
    assessment = assess(ready, profile)
    assert assessment.policy_version == profile
    assert len(assessment.rules) == len(POLICY_RULES[profile])
    assert {rule.rule for rule in assessment.rules} == POLICY_RULES[profile]
    assert merges_alone(assessment) == (profile == PROFILE)


def test_unknown_profile_rejected_in_evaluation_and_authority(ready: PolicyInputs) -> None:
    with pytest.raises(ValueError, match="unknown policy profile"):
        assess(ready, "typo")
    # The contract deliberately accepts unknown names with v1's rules.
    data = assess(ready, "safe-merge-v1").model_dump()
    data["policy_version"] = "typo"
    assert not merges_alone(ChangeAssessment.model_validate(data))


@pytest.mark.parametrize("name", sorted(POLICY_RULES[PROFILE]))
@pytest.mark.parametrize("state", ["fail", "unknown"])
def test_authority_checks_every_rule(ready: PolicyInputs, name: RuleId, state: str) -> None:
    assessment = assess(ready, PROFILE)
    # Bypass contract validation to exercise the authority boundary independently.
    assessment.rules[:] = [
        r.model_copy(update={"result": state}) if r.rule == name else r for r in assessment.rules
    ]
    assert not merges_alone(assessment)


def test_authority_checks_mode_and_duplicate_rules(ready: PolicyInputs) -> None:
    assert not merges_alone(assess(replace(ready, mode="ask_me"), PROFILE))
    assessment = assess(ready, PROFILE)
    assessment.rules[-1] = assessment.rules[0]
    assert not merges_alone(assessment)
    assessment.rules.pop()
    assert not merges_alone(assessment)


@pytest.mark.parametrize("severity", ["BLOCKING", "SHOULD-FIX", "NIT"])
def test_cross_review_verdict_and_evidence(ready: PolicyInputs, severity: str) -> None:
    adverse = severity != "NIT"
    changed = replace(
        ready,
        review=review(
            verdict="changes_requested" if adverse else "clean",
            findings=[ReviewFinding.model_validate(dict(severity=severity, text="Observation"))],
        ),
    )
    item = result(changed, "cross_review")
    assert item.result == ("fail" if adverse else "pass")
    assert item.observed == dict(violations=int(adverse), complete=True)
    assert item.evidence == list(ready.evidence["cross_review"])
    assert merges_alone(assess(changed, PROFILE)) == (not adverse)


@pytest.mark.parametrize(
    "author,reviewer,expected",
    [
        (MUSE_SPARK, MUSE_SPARK_OPEN, "pass"),
        (MUSE_SPARK_OPEN, MUSE_SPARK, "pass"),
        (MUSE_SPARK, MUSE_SPARK.model_copy(update={"model": "another-model"}), "unknown"),
        (MUSE_SPARK_OPEN, MUSE_SPARK_OPEN.model_copy(update={"model": "another-model"}), "unknown"),
        (MUSE_SPARK, FAKE_MODEL_B, "unknown"),
        (FAKE_MODEL, FAKE_MODEL_B, "pass"),
        (FAKE_MODEL, MUSE_SPARK, "unknown"),
    ],
)
def test_cross_review_provider_identity(
    ready: PolicyInputs, author: ModelProfile, reviewer: ModelProfile, expected: str
) -> None:
    changed = replace(ready, author=author, review=review(author, reviewer))
    assert result(changed, "cross_review").result == expected
    assert merges_alone(assess(changed, PROFILE)) == (expected == "pass")


@pytest.mark.parametrize("field", ["review", "author"])
def test_missing_review_identity_unknown(ready: PolicyInputs, field: str) -> None:
    assert result(replace(ready, **{field: None}), "cross_review").result == "unknown"


@pytest.mark.parametrize(
    "updates",
    [
        dict(verdict="unknown", complete=False, problems=["unavailable"]),
        dict(author_model="wrong-author"),
        dict(head_commit="stale"),
        dict(revision=2),
        dict(
            reviewer_model=MUSE_SPARK.model,
            verdict="unknown",
            complete=False,
            problems=["same model"],
        ),
    ],
)
def test_unusable_review_unknown(ready: PolicyInputs, updates: dict[str, object]) -> None:
    assert result(replace(ready, review=review(**updates)), "cross_review").result == "unknown"


@pytest.mark.parametrize(
    "kind",
    [
        "test_removed",
        "assertion_loosened",
        "skip_added",
        "expected_value_changed",
        "literal_branch",
        "stub_left",
        "parse_error",
    ],
)
def test_integrity_fact_classification(ready: PolicyInputs, kind: str) -> None:
    fact = IntegrityFact.model_validate(
        dict(
            kind=kind,
            path="tests/test_a.py",
            line=1,
            text="Observed",
            evidence=[EvidenceRef(git_object="tree").model_dump()],
        )
    )
    item = result(replace(ready, integrity=(fact,)), "test_integrity")
    assert item.result == ("unknown" if kind == "parse_error" else "fail")
    assert item.evidence == list(ready.evidence["test_integrity"])


@pytest.mark.parametrize(
    "path",
    [
        "pytest.ini",
        ".pytest.ini",
        "pytest.toml",
        ".pytest.toml",
        "pyproject.toml",
        "setup.cfg",
        "tox.ini",
        "tests/conftest.py",
        "tests/test_old.py",
    ],
)
@pytest.mark.parametrize("operation", ["add", "modify", "delete"])
def test_integrity_source_vetoes(ready: PolicyInputs, path: str, operation: str) -> None:
    before = {} if operation == "add" else {path: "# old\n"}
    after = {} if operation == "delete" else {path: "# changed\n"}
    status = integrity_status(before, after)
    veto = operation != "add" or path != "tests/test_old.py"
    assert status.prohibited_paths == ((path,) if veto else ())
    assert result(replace(ready, integrity_status=status), "test_integrity").result == (
        "fail" if veto else "pass"
    )


def test_integrity_pass_and_unknown(ready: PolicyInputs) -> None:
    status = integrity_status({}, {"tests/test_new.py": "def test_new():\n    assert value()\n"})
    assert status == IntegrityStatus(complete=True)
    assert result(replace(ready, integrity_status=status), "test_integrity").result == "pass"
    assert (
        result(
            replace(ready, integrity_status=IntegrityStatus(complete=False)), "test_integrity"
        ).result
        == "unknown"
    )
    status = integrity_status({}, {"broken.py": "def ("})
    assert not status.complete
    assert result(replace(ready, integrity_status=status), "test_integrity").result == "unknown"


@pytest.mark.parametrize(
    "outcome,expected",
    [
        ("fails_before_passes_after", "pass"),
        ("passes_before_and_after", "fail"),
        ("fails_after", "fail"),
        ("not_collected_after", "fail"),
        ("skipped", "fail"),
        ("unknown", "unknown"),
    ],
)
def test_added_tests_probes(ready: PolicyInputs, outcome: str, expected: str) -> None:
    probe = AddedTestProbe.model_validate(
        dict(test_id="tests/test_new.py::test_new", before_seq=10, after_seq=20, result=outcome)
    )
    changed = replace(
        ready,
        added_tests=(probe,),
        added_tests_inventory=AddedTestsInventory(test_ids=(probe.test_id,), complete=True),
    )
    item = result(changed, "added_tests_fail_before")
    assert item.result == expected
    assert {ref.event_seq for ref in item.evidence} == {
        10,
        20,
        ready.evidence["added_tests_fail_before"][0].event_seq,
    }
    assert merges_alone(assess(changed, PROFILE)) == (expected == "pass")


@pytest.mark.parametrize("ids,complete", [((), True), ((), False), (("missing",), True)])
def test_added_tests_empty_inventory(
    ready: PolicyInputs, ids: tuple[str, ...], complete: bool
) -> None:
    changed = replace(
        ready, added_tests_inventory=AddedTestsInventory(test_ids=ids, complete=complete)
    )
    assert result(changed, "added_tests_fail_before").result == (
        "pass" if complete and not ids else "unknown"
    )


@pytest.mark.parametrize(
    "name,field",
    [
        ("test_integrity", "integrity"),
        ("test_integrity", "integrity_status"),
        ("added_tests_fail_before", "added_tests"),
        ("added_tests_fail_before", "added_tests_inventory"),
    ],
)
def test_missing_producer_unknown(ready: PolicyInputs, name: RuleId, field: str) -> None:
    assert result(replace(ready, **{field: None}), name).result == "unknown"


@pytest.mark.parametrize("name", ["cross_review", "test_integrity", "added_tests_fail_before"])
def test_missing_receipt_unknown(ready: PolicyInputs, name: RuleId) -> None:
    changed = replace(
        ready, evidence={key: value for key, value in ready.evidence.items() if key != name}
    )
    assert result(changed, name).result == "unknown"
    assert not merges_alone(assess(changed, PROFILE))


def test_inventory_status_requires_producer_report() -> None:
    rec = Mock(
        spec=ChangeRecord,
        base_commit="base",
        base_files={},
        head_files={},
        suite_runs={},
        executions={},
    )
    assert added_tests_inventory(rec, 1) == AddedTestsInventory(test_ids=(), complete=False)
    suite = SuiteResult(
        outcome="passed",
        passed=1,
        failed=0,
        skipped=0,
        duration_ms=1,
        failed_tests=[],
        cases=[],
        cases_complete=True,
    )
    rec.executions = {
        1: SuiteExecution(
            change_id="change-1",
            agent_id="agent-1",
            revision=1,
            phase="proposal",
            tested_commit="head",
            tested_tree="tree",
            suite=suite,
        )
    }
    # added_cases needs the full report matching every retained execution.
    rec.suite_runs = {1: Mock(cases=(), problems=())}
    assert added_tests_inventory(rec, 1) == AddedTestsInventory(test_ids=(), complete=True)
    rec.executions[1] = rec.executions[1].model_copy(
        update={"suite": suite.model_copy(update={"cases_complete": False})}
    )
    assert not added_tests_inventory(rec, 1).complete


def test_clean_review_preserves_independent_veto(ready: PolicyInputs) -> None:
    assessment = assess(replace(ready, lines_added=61), PROFILE)
    assert result(ready, "cross_review").result == "pass"
    assert next(rule for rule in assessment.rules if rule.rule == "small_change").result == "fail"
    assert not merges_alone(assessment)


def test_probe_receipts_cannot_replace_missing_facts_receipt(ready: PolicyInputs) -> None:
    probe = AddedTestProbe(
        test_id="tests/test_new.py::test_new",
        before_seq=10,
        after_seq=20,
        result="fails_before_passes_after",
    )
    changed = replace(
        ready,
        added_tests=(probe,),
        added_tests_inventory=AddedTestsInventory(test_ids=(probe.test_id,), complete=True),
        evidence={
            key: refs for key, refs in ready.evidence.items() if key != "added_tests_fail_before"
        },
    )
    assert result(changed, "added_tests_fail_before").result == "unknown"


@pytest.mark.parametrize("duplicate", ["inventory", "probe"])
def test_duplicate_added_case_is_unknown(ready: PolicyInputs, duplicate: str) -> None:
    probe = AddedTestProbe(
        test_id="tests/test_new.py::test_new",
        before_seq=10,
        after_seq=20,
        result="fails_before_passes_after",
    )
    changed = replace(
        ready,
        added_tests=(probe,) * (2 if duplicate == "probe" else 1),
        added_tests_inventory=AddedTestsInventory(
            test_ids=(probe.test_id,) * (2 if duplicate == "inventory" else 1), complete=True
        ),
    )
    assert result(changed, "added_tests_fail_before").result == "unknown"


def test_inventory_reports_ambiguous_source_without_diagnostic_parsing() -> None:
    rec = Mock(
        spec=ChangeRecord,
        base_commit="base",
        base_files={},
        head_files={"tests/test_new.py": "def ("},
        suite_runs={},
        executions={},
    )
    inventory = added_tests_inventory(rec, 1)
    assert inventory.test_ids == () and not inventory.complete


def test_empty_integrity_requires_capture_witness(ready: PolicyInputs) -> None:
    status = integrity_status({}, {})
    assert not status.complete
    assert result(replace(ready, integrity_status=status), "test_integrity").result == "unknown"
    assert integrity_status({}, {}, captured=True).complete
