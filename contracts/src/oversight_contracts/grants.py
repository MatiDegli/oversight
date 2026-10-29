"""Person-created, bounded permissions to remove a specific rules wait."""

from collections.abc import Sequence
from typing import Literal

from pydantic import Field, NonNegativeInt, PositiveInt

from oversight_contracts.common import (
    AgentId,
    ChangeId,
    ContractModel,
    NodeId,
    NonEmptyText,
    RuleId,
    RunId,
    Timestamp,
)
from oversight_contracts.policy import ChangeAssessment, RuleAssessment

type GrantReason = Literal["possible_callers", "protected_path"]


class GrantScope(ContractModel):
    kind: Literal["module", "path", "agent", "run"]
    value: NonEmptyText
    expires: Literal["run", "session"]


class GrantRequest(ContractModel):
    reason: GrantReason
    scope: GrantScope


class GrantCreated(GrantRequest):
    grant_id: NonEmptyText
    change_id: ChangeId
    revision: PositiveInt
    head_commit: NonEmptyText
    run_id: RunId
    session_id: NonEmptyText | None = None
    granted_by: Literal["person"]


class GrantRevoked(ContractModel):
    grant_id: NonEmptyText


class GrantAuthorization(GrantRequest):
    """Server capture after exact scope matching, bound to this assessment target.

    Scope is captured from the server's matched grant, never from a model or command receipt.
    Historical validation consumes this capture; only live barriers consult mutable grants.
    """

    creation_seq: PositiveInt
    creation_ts: Timestamp
    run_id: RunId
    session_id: NonEmptyText | None = None
    checked_at: Timestamp
    active_at_effect: bool
    change_id: ChangeId
    agent_id: AgentId
    revision: PositiveInt
    head_commit: NonEmptyText


class GrantUse(ContractModel):
    grant_id: NonEmptyText
    rule: RuleId
    caller_ids: list[NodeId] = Field(max_length=20)
    caller_count: NonNegativeInt
    authorization: GrantAuthorization


def possible_callers_wait(rule: RuleAssessment) -> bool:
    """Only a complete cone within every exact limit can have this waivable gap."""
    observed = rule.observed
    nodes = observed.get("nodes") if observed is not None else None
    return (
        rule.rule == "small_cone"
        and rule.result == "unknown"
        and bool(rule.evidence)
        and observed is not None
        and type(nodes) is int
        and 0 <= nodes <= 5
        and all(
            type(observed.get(k)) is int and observed[k] == 0
            for k in ("omitted_by_depth", "omitted_by_limit", "parse_errors", "unresolved_removed")
        )
    )


def covered_rule(
    assessment: ChangeAssessment, rule: RuleAssessment, grants: Sequence[GrantUse]
) -> bool:
    uses = [use for use in grants if use.rule == rule.rule]
    if not possible_callers_wait(rule) or len(uses) != 1:
        return False
    use = uses[0]
    auth = use.authorization
    return (
        auth.reason == "possible_callers"
        and auth.scope.kind in {"module", "run"}
        and auth.scope.expires == "run"
        and (auth.scope.kind != "run" or auth.scope.value == auth.run_id)
        and auth.creation_ts <= auth.checked_at
        and auth.active_at_effect
        and (auth.change_id, auth.agent_id, auth.revision, auth.head_commit)
        == (assessment.change_id, assessment.agent_id, assessment.revision, assessment.head_commit)
        and use.caller_count > 0
        and len(use.caller_ids) == min(20, use.caller_count)
        and use.caller_ids == sorted(set(use.caller_ids))
    )


def rules_satisfied(assessment: ChangeAssessment, grants: Sequence[GrantUse] = ()) -> bool:
    """Shared pure rules/event authorization; a grant never rewrites a measured result."""
    unknown = {rule.rule for rule in assessment.rules if rule.result != "pass"}
    return (
        len(grants) == len({use.rule for use in grants})
        and {use.rule for use in grants} == (unknown if grants else set())
        and all(
            rule.result == "pass" or covered_rule(assessment, rule, grants)
            for rule in assessment.rules
        )
    )
