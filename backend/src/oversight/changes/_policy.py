"""Pure profile evaluation. Callers collect evidence; this module never executes a merge."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from math import isfinite
from typing import Literal

from pydantic import JsonValue, ValidationError

from oversight.changes._facts import AddedTestsInventory, IntegrityStatus
from oversight.changes._protected_paths import (
    ChangedFileInventory,
    ProtectedPathSnapshot,
    check_protected_paths,
)
from oversight_contracts.common import MergeMode, RuleId
from oversight_contracts.events import ModelProfile, SuiteResult
from oversight_contracts.grants import GrantUse, rules_satisfied
from oversight_contracts.graph import ImpactCone
from oversight_contracts.policy import (
    POLICY_RULES,
    ChangeAssessment,
    CheckInputs,
    EvidenceRef,
    RuleAssessment,
)
from oversight_contracts.review import AddedTestProbe, CrossReview, IntegrityFact

type Result = Literal["pass", "fail", "unknown"]
_REVIEW_VERDICTS: dict[Literal["clean", "changes_requested", "unknown"], bool] = {
    "clean": True,
    "changes_requested": True,
    "unknown": False,
}


@dataclass(frozen=True, kw_only=True)
class SpendSnapshot:
    """Ledger facts at evaluation time; None means unavailable, including collection errors."""

    spent_usd: float | None = None
    budget_usd: float | None = None
    unknown_costs: int | None = None
    ledger_ok: bool | None = None


@dataclass(frozen=True, kw_only=True)
class PolicyInputs:
    """Facts about one combined candidate on current main, supplied without I/O.

    None represents a missing, errored or timed-out observation. Empty tuples are known empty
    sets, not missing data. Evidence is per rule; absent or empty references mean unknown.
    Existing test paths are classified by the caller against the base, so added tests are absent
    from both existing-test tuples. ImpactCone.entries already excludes seeds and tests.
    """

    change_id: str
    agent_id: str
    revision: int
    base_commit: str
    head_commit: str
    main_commit: str
    mode: MergeMode
    mode_revision: int
    candidate_tree: str | None = None
    suite: SuiteResult | None = None
    cone: ImpactCone | None = None
    parse_errors: int | None = None
    unresolved_removed: int | None = None
    possible_callers: int | None = None
    files: tuple[str, ...] | None = None
    lines_added: int | None = None
    lines_removed: int | None = None
    spend: SpendSnapshot | None = None
    modified_existing_test_paths: tuple[str, ...] | None = None
    deleted_existing_test_paths: tuple[str, ...] | None = None
    author: ModelProfile | None = None
    review: CrossReview | None = None
    integrity: tuple[IntegrityFact, ...] | None = None
    integrity_status: IntegrityStatus | None = None
    added_tests: tuple[AddedTestProbe, ...] | None = None
    added_tests_inventory: AddedTestsInventory | None = None
    protected_paths: ProtectedPathSnapshot | None = field(default_factory=ProtectedPathSnapshot)
    changed_files: ChangedFileInventory | None = None
    evidence: Mapping[RuleId, tuple[EvidenceRef, ...]] = field(
        default_factory=dict[RuleId, tuple[EvidenceRef, ...]]
    )


def _record(
    inputs: PolicyInputs,
    rule: RuleId,
    threshold: dict[str, JsonValue],
    observed: dict[str, JsonValue] | None,
    passes: bool | None,
) -> RuleAssessment:
    evidence = list(inputs.evidence.get(rule, ()))
    result: Result = "unknown"
    if observed is not None and evidence and passes is not None:
        result = "pass" if passes else "fail"
    try:
        return RuleAssessment(
            rule=rule, observed=observed, threshold=threshold, result=result, evidence=evidence
        )
    except ValidationError:
        # Invalid numeric observations (negative counters, non-finite spend) are unavailable,
        # never an invitation to pass. Keep only contract-valid observations in the report.
        return RuleAssessment(
            rule=rule, observed=None, threshold=threshold, result="unknown", evidence=evidence
        )


def _combined_tests(inputs: PolicyInputs) -> RuleAssessment:
    tests = inputs.suite
    observed = tests.model_dump(mode="json") if tests is not None else None
    passes = None
    if tests is not None and tests.outcome not in {"error", "timeout"}:
        if min(tests.passed, tests.failed, tests.skipped, tests.duration_ms) < 0:
            observed = None
        else:
            passes = (
                tests.outcome == "passed"
                and tests.passed >= 1
                and tests.failed == 0
                and tests.skipped == 0
            )
    return _record(
        inputs,
        "combined_tests",
        {"outcome": "passed", "passed_min": 1, "failed_max": 0, "skipped_max": 0},
        observed,
        passes,
    )


def _small_cone(inputs: PolicyInputs) -> RuleAssessment:
    cone = inputs.cone
    observed: dict[str, JsonValue] | None = None
    passes = None
    if (
        cone is not None
        and inputs.parse_errors is not None
        and inputs.unresolved_removed is not None
    ):
        observed = {
            "nodes": len(cone.entries),
            "omitted_by_depth": cone.omitted_by_depth,
            "omitted_by_limit": cone.omitted_by_limit,
            "parse_errors": inputs.parse_errors,
            "unresolved_removed": inputs.unresolved_removed,
        }
        passes = (
            len(cone.entries) <= 5
            and cone.omitted_by_depth == 0
            and cone.omitted_by_limit == 0
            and inputs.parse_errors == 0
            and inputs.unresolved_removed == 0
        )
        if passes and inputs.possible_callers != 0:
            passes = None
    return _record(
        inputs,
        "small_cone",
        {
            "nodes_max": 5,
            "omitted_by_depth_max": 0,
            "omitted_by_limit_max": 0,
            "parse_errors_max": 0,
            "unresolved_removed_max": 0,
        },
        observed,
        passes,
    )


def _small_change(inputs: PolicyInputs) -> RuleAssessment:
    observed: dict[str, JsonValue] | None = None
    passes = None
    if (
        inputs.files is not None
        and inputs.lines_added is not None
        and inputs.lines_removed is not None
    ):
        observed = {
            "files": len(inputs.files),
            "lines_added": inputs.lines_added,
            "lines_removed": inputs.lines_removed,
        }
        passes = 1 <= len(inputs.files) <= 3 and inputs.lines_added + inputs.lines_removed <= 60
    return _record(
        inputs,
        "small_change",
        {"files_min": 1, "files_max": 3, "lines_total_max": 60},
        observed,
        passes,
    )


def _known_spend(inputs: PolicyInputs) -> RuleAssessment:
    spend = inputs.spend
    observed: dict[str, JsonValue] | None = None
    passes = None
    if (
        spend is not None
        and spend.spent_usd is not None
        and spend.budget_usd is not None
        and spend.unknown_costs is not None
        and spend.ledger_ok is not None
        and isfinite(spend.spent_usd)
        and isfinite(spend.budget_usd)
        and spend.spent_usd >= 0
        and spend.budget_usd > 0
    ):
        observed = {
            "spent_usd": spend.spent_usd,
            "budget_usd": spend.budget_usd,
            "unknown_costs": spend.unknown_costs,
            "ledger_ok": spend.ledger_ok,
        }
        passes = (
            spend.spent_usd / spend.budget_usd < 0.5
            and spend.unknown_costs == 0
            and spend.ledger_ok
        )
    return _record(
        inputs,
        "known_spend",
        {"budget_fraction_lt": 0.5, "unknown_costs_max": 0, "ledger_ok": True},
        observed,
        passes,
    )


def _existing_tests(inputs: PolicyInputs) -> RuleAssessment:
    modified = inputs.modified_existing_test_paths
    deleted = inputs.deleted_existing_test_paths
    observed: dict[str, JsonValue] | None = None
    passes = None
    if modified is not None and deleted is not None:
        observed = {
            "modified_existing_paths": list(modified),
            "deleted_existing_paths": list(deleted),
        }
        passes = not modified and not deleted
    return _record(
        inputs,
        "existing_tests_untouched",
        {"modified_existing_max": 0, "deleted_existing_max": 0},
        observed,
        passes,
    )


def _check(inputs: PolicyInputs, rule: RuleId, checks: CheckInputs | None) -> RuleAssessment:
    passes = None
    if checks is not None:
        passes = False if checks.violations else True if checks.complete else None
    return _record(
        inputs,
        rule,
        {"violations_max": 0, "complete": True},
        checks.model_dump(mode="json") if checks is not None else None,
        passes,
    )


def _cross_review(inputs: PolicyInputs) -> RuleAssessment:
    review, author = inputs.review, inputs.author
    checks = None
    if (
        review is not None
        and author is not None
        and review.complete
        and not review.problems
        and _REVIEW_VERDICTS.get(review.verdict, False)
        and review.author_model == author.model
        and review.reviewer_model != author.model
        and (review.change_id, review.revision, review.base_commit, review.head_commit)
        == (inputs.change_id, inputs.revision, inputs.base_commit, inputs.head_commit)
        and (
            (author.provider == "fake" and review.reviewer_provider == "fake")
            or {author.provider, review.reviewer_provider} == {"meta", "baseten"}
        )
    ):
        checks = CheckInputs(
            violations=sum(f.severity in {"BLOCKING", "SHOULD-FIX"} for f in review.findings),
            complete=all(f.severity is not None for f in review.findings),
        )
    return _check(inputs, "cross_review", checks)


def _test_integrity(inputs: PolicyInputs) -> RuleAssessment:
    facts, status = inputs.integrity, inputs.integrity_status
    checks = None
    if facts is not None and status is not None:
        checks = CheckInputs(
            violations=sum(f.kind != "parse_error" for f in facts) + len(status.prohibited_paths),
            complete=status.complete and all(f.kind != "parse_error" for f in facts),
        )
    return _check(inputs, "test_integrity", checks)


def _added_tests_fail_before(inputs: PolicyInputs) -> RuleAssessment:
    probes, inventory = inputs.added_tests, inputs.added_tests_inventory
    checks = None
    if probes is not None and inventory is not None:
        ids = [probe.test_id for probe in probes]
        checks = CheckInputs(
            violations=sum(
                probe.result not in {"fails_before_passes_after", "unknown"} for probe in probes
            ),
            complete=inventory.complete
            and len(ids) == len(set(ids))
            and len(inventory.test_ids) == len(set(inventory.test_ids))
            and set(ids) == set(inventory.test_ids)
            and all(probe.result != "unknown" for probe in probes),
        )
        # A facts receipt is required even for an empty inventory. Probe receipts
        # supplement it; they never substitute for missing inventory evidence.
        refs = inputs.evidence.get("added_tests_fail_before", ())
        if refs:
            extra_seqs = sorted(
                {seq for probe in probes for seq in (probe.before_seq, probe.after_seq)}
                - {ref.event_seq for ref in refs}
            )
            inputs = replace(
                inputs,
                evidence={
                    **inputs.evidence,
                    "added_tests_fail_before": (
                        *refs,
                        *(EvidenceRef(event_seq=seq) for seq in extra_seqs),
                    ),
                },
            )
    return _check(inputs, "added_tests_fail_before", checks)


def _protected_paths(inputs: PolicyInputs) -> RuleAssessment:
    captured = check_protected_paths(inputs.protected_paths, inputs.changed_files)
    checks = None
    if captured is not None:
        checks = CheckInputs(violations=len({hit.path for hit in captured.matches}), complete=True)
        inputs = replace(inputs, evidence={**inputs.evidence, "protected_paths": captured.evidence})
    return _check(inputs, "protected_paths", checks)


def assess(inputs: PolicyInputs, profile: str = "safe-merge-v1") -> ChangeAssessment:
    """Evaluate exactly the named profile; unknown names have no fallback authority."""
    if profile not in POLICY_RULES:
        raise ValueError(f"unknown policy profile: {profile}")
    evaluators = {
        "combined_tests": _combined_tests,
        "small_cone": _small_cone,
        "small_change": _small_change,
        "known_spend": _known_spend,
        "existing_tests_untouched": _existing_tests,
        "cross_review": _cross_review,
        "test_integrity": _test_integrity,
        "added_tests_fail_before": _added_tests_fail_before,
        "protected_paths": _protected_paths,
    }
    return ChangeAssessment(
        change_id=inputs.change_id,
        agent_id=inputs.agent_id,
        revision=inputs.revision,
        base_commit=inputs.base_commit,
        head_commit=inputs.head_commit,
        main_commit=inputs.main_commit,
        candidate_tree=inputs.candidate_tree,
        policy_version=profile,
        mode=inputs.mode,
        mode_revision=inputs.mode_revision,
        rules=[
            evaluators[rule](inputs) if rule in evaluators else _check(inputs, rule, None)
            for rule in sorted(POLICY_RULES[profile])
        ],
    )


def merges_alone(assessment: ChangeAssessment, grants: Sequence[GrantUse] = ()) -> bool:
    """Require the exact nine-rule paths profile, mode and unanimous passing evidence."""
    return (
        assessment.policy_version == "safe-merge-paths-v1"
        and assessment.mode == "merge_when_safe"
        and len(assessment.rules) == 9
        and {rule.rule for rule in assessment.rules} == set(POLICY_RULES["safe-merge-paths-v1"])
        and rules_satisfied(assessment, grants)
    )
