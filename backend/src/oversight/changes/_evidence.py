"""Immutable server captures retained across effects and delivery recovery."""

from dataclasses import dataclass
from typing import Literal

from oversight_contracts.events import AgentStatus
from oversight_contracts.grants import GrantUse
from oversight_contracts.policy import ChangeAssessment
from oversight_contracts.review import ReviewCard


@dataclass(frozen=True)
class WriteEvidence:
    """Authorization and target as observed before changing Git or the worktree base."""

    change_id: str
    agent_id: str
    revision: int
    base_commit: str
    head_commit: str
    actor: Literal["person", "rules", "cross_review", "lead_agent"]
    decision: Literal["approved", "rejected", "changes_requested"]
    comment: str | None
    agent_status: AgentStatus | None = None
    requested_by: str | None = None
    review_seq: int | None = None
    assessment: ChangeAssessment | None = None
    reasons: tuple[str, ...] = ()
    registered_leads: frozenset[str] = frozenset()
    answers: tuple[ReviewCard, ...] | None = None
    grants: tuple[GrantUse, ...] = ()
