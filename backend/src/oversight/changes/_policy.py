"""Pure safe-merge-v1 evaluation. Callers collect evidence; this module never executes a merge."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from math import isfinite
from typing import Literal

from pydantic import JsonValue, ValidationError

from oversight_contracts.common import MergeMode, RuleId
from oversight_contracts.events import SuiteResult
from oversight_contracts.graph import ImpactCone
from oversight_contracts.policy import (
    SAFE_MERGE_V1_RULES,
    ChangeAssessment,
    EvidenceRef,
    RuleAssessment,
)

type Result = Literal["pass", "fail", "unknown"]


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
    files: tuple[str, ...] | None = None
    lines_added: int | None = None
    lines_removed: int | None = None
    spend: SpendSnapshot | None = None
    modified_existing_test_paths: tuple[str, ...] | None = None
    deleted_existing_test_paths: tuple[str, ...] | None = None
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


def assess(inputs: PolicyInputs) -> ChangeAssessment:
    """Evaluate all five rules exactly once against the supplied candidate and evidence."""
    return ChangeAssessment(
        change_id=inputs.change_id,
        agent_id=inputs.agent_id,
        revision=inputs.revision,
        base_commit=inputs.base_commit,
        head_commit=inputs.head_commit,
        main_commit=inputs.main_commit,
        candidate_tree=inputs.candidate_tree,
        policy_version="safe-merge-v1",
        mode=inputs.mode,
        mode_revision=inputs.mode_revision,
        rules=[
            _combined_tests(inputs),
            _small_cone(inputs),
            _small_change(inputs),
            _known_spend(inputs),
            _existing_tests(inputs),
        ],
    )


def merges_alone(assessment: ChangeAssessment) -> bool:
    """True only for safe-merge-v1 in merge_when_safe mode with all five rules passing."""
    return (
        assessment.policy_version == "safe-merge-v1"
        and assessment.mode == "merge_when_safe"
        and len(assessment.rules) == 5
        and {rule.rule for rule in assessment.rules} == set(SAFE_MERGE_V1_RULES)
        and all(rule.result == "pass" for rule in assessment.rules)
    )
