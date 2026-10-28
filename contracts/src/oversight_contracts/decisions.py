"""Run-local references to originating inbox entries, with no command or event dependency.

Pydantic validates intrinsic shape only. The producer verifies event existence, target equality
and entry uniqueness within the run. evidence_seq names the event that raised the entry; cards
retain their existing evidence_seq. References preserve identity, not an answer or a new card id.
"""

from typing import Annotated, Literal

from pydantic import Field, NonNegativeInt, PositiveInt

from oversight_contracts.common import AgentId, ChangeId, ContractModel, NodeId, NonEmptyText


class ChangeEntryRef(ContractModel):
    """Shared candidate identity, including revision zero before a proposal; not an endpoint."""

    change_id: ChangeId
    revision: NonNegativeInt
    head_commit: NonEmptyText
    evidence_seq: PositiveInt
    """The event that raised this inbox entry in the current run."""


class ReviewCardDecisionRef(ChangeEntryRef):
    """One card entry per (change_id, revision, node_id, question).

    Retain the card's question and evidence_seq unchanged, scoped by its candidate revision and
    head so an answer to an older candidate cannot consume a newer candidate's entry.
    """

    kind: Literal["review_card"]
    node_id: NodeId
    question: NonEmptyText


class ChangeDecisionRef(ChangeEntryRef):
    """One entry per (kind, change_id, revision), guarded by the candidate head.

    Several unavailable checks of one candidate form one checks_unavailable entry. evidence_seq
    identifies the event that raised the entry, not each check's supporting evidence.
    """

    kind: Literal["checks_unavailable", "follow_main", "risky_area", "examples_before_code"]


class LeadPlanDecisionRef(ContractModel):
    """One entry per (agent_id, revision) of the lead status whose plan awaits approval.

    This names a lead-status revision, not a plan_id. evidence_seq is the event that raised it.
    """

    kind: Literal["lead_plan"]
    agent_id: AgentId
    revision: PositiveInt
    evidence_seq: PositiveInt


type DecisionRef = Annotated[
    ReviewCardDecisionRef | ChangeDecisionRef | LeadPlanDecisionRef,
    Field(discriminator="kind"),
]
