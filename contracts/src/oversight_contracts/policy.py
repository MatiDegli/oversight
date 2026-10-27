"""Reserved safe-merge evidence and assessments; no policy evaluation or merge execution."""

from typing import Literal, Self, get_args

from pydantic import (
    ConfigDict,
    FiniteFloat,
    JsonValue,
    NonNegativeInt,
    PositiveInt,
    model_validator,
)

from oversight_contracts.common import (
    AgentId,
    ChangeId,
    ContractModel,
    MergeMode,
    NonEmptyText,
    RuleId,
    WorkspacePath,
)


class EvidenceRef(ContractModel):
    """An event or Git object in this run, optionally narrowed to a workspace path."""

    event_seq: PositiveInt | None = None
    git_object: NonEmptyText | None = None
    path: WorkspacePath | None = None

    @model_validator(mode="after")
    def _has_source(self) -> Self:
        if self.event_seq is None and self.git_object is None:
            raise ValueError("evidence requires event_seq or git_object")
        return self


class _Inputs(ContractModel):
    """Validate recorded JSON inputs without coercing booleans or numeric strings."""

    model_config = ConfigDict(strict=True)


class _Cone(_Inputs):
    nodes: NonNegativeInt
    omitted_by_depth: NonNegativeInt
    omitted_by_limit: NonNegativeInt
    parse_errors: NonNegativeInt
    unresolved_removed: NonNegativeInt


class _Change(_Inputs):
    files: NonNegativeInt
    lines_added: NonNegativeInt
    lines_removed: NonNegativeInt


class _Spend(_Inputs):
    spent_usd: FiniteFloat
    budget_usd: FiniteFloat
    unknown_costs: NonNegativeInt
    ledger_ok: bool


class _ExistingTests(_Inputs):
    modified_existing_paths: list[WorkspacePath]
    deleted_existing_paths: list[WorkspacePath]


_THRESHOLDS: dict[RuleId, dict[str, JsonValue]] = {
    "combined_tests": {"outcome": "passed", "passed_min": 1, "failed_max": 0, "skipped_max": 0},
    "small_cone": {
        "nodes_max": 5,
        "omitted_by_depth_max": 0,
        "omitted_by_limit_max": 0,
        "parse_errors_max": 0,
        "unresolved_removed_max": 0,
    },
    "small_change": {"files_min": 1, "files_max": 3, "lines_total_max": 60},
    "known_spend": {"budget_fraction_lt": 0.5, "unknown_costs_max": 0, "ledger_ok": True},
    "existing_tests_untouched": {"modified_existing_max": 0, "deleted_existing_max": 0},
}
_INPUTS: dict[RuleId, type[ContractModel]] = {
    "small_cone": _Cone,
    "small_change": _Change,
    "known_spend": _Spend,
    "existing_tests_untouched": _ExistingTests,
}


class RuleAssessment(ContractModel):
    """One rule's recorded inputs, limit, outcome and evidence."""

    rule: RuleId
    observed: dict[str, JsonValue] | None = None
    threshold: dict[str, JsonValue]
    result: Literal["pass", "fail", "unknown"]
    evidence: list[EvidenceRef]

    @model_validator(mode="after")
    def _safe_merge_inputs(self) -> Self:
        expected = _THRESHOLDS[self.rule]
        if self.threshold != expected or any(
            type(self.threshold[key]) is not type(value) for key, value in expected.items()
        ):
            raise ValueError("safe-merge-v1 threshold must match the rule's fixed limits")
        if self.result != "unknown" and not self.evidence:
            raise ValueError("missing evidence requires an unknown result")
        if self.observed is None:
            if self.result != "unknown":
                raise ValueError("missing observations require an unknown result")
            return self
        if self.rule == "combined_tests":
            # Local import keeps SuiteResult's existing public module and avoids an import cycle.
            from oversight_contracts.events import SuiteResult

            tests = SuiteResult.model_validate(self.observed, strict=True)
            if tests.outcome in {"error", "timeout"} and self.result != "unknown":
                raise ValueError("test errors and timeouts require an unknown result")
        else:
            _INPUTS[self.rule].model_validate(self.observed)
        return self


class MergeModeState(ContractModel):
    """Server-owned merge mode and its monotonically increasing revision."""

    mode: MergeMode = "ask_me"
    revision: NonNegativeInt = 0


class ChangeAssessment(ContractModel):
    """One evaluation of an exact change revision against current main."""

    change_id: ChangeId
    agent_id: AgentId
    revision: PositiveInt
    base_commit: NonEmptyText
    head_commit: NonEmptyText
    main_commit: NonEmptyText
    candidate_tree: NonEmptyText | None = None
    policy_version: NonEmptyText
    mode: MergeMode
    mode_revision: NonNegativeInt
    rules: list[RuleAssessment]

    @model_validator(mode="after")
    def _all_rules_once(self) -> Self:
        if len(self.rules) != 5 or {rule.rule for rule in self.rules} != set(
            get_args(RuleId.__value__)
        ):
            raise ValueError("an assessment requires all five rules exactly once")
        return self
