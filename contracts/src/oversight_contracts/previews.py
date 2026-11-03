"""Person-owned live-preview requests and lifecycle facts."""

from typing import Annotated, Literal, Self

from pydantic import PositiveInt, StringConstraints, model_validator

from oversight_contracts.common import AgentId, ChangeId, ContractModel, NonEmptyText, RunId

type PreviewId = Annotated[str, StringConstraints(pattern=r"^preview-[0-9a-f]{32}$")]
"""Server-generated UUID4 hex identifier, prefixed with preview-."""

type PreviewLeaseToken = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{43}$")]
"""Possession token generated from 32 cryptographically random bytes; never recorded."""

type PreviewState = Literal["starting", "ready", "stopping", "stopped", "failed"]
"""Closed preview lifecycle states."""

type PreviewReason = Literal[
    "closed",
    "lease_expired",
    "lifetime_expired",
    "mutation",
    "shutdown",
    "app_exited",
    "readiness_timeout",
    "launch_failed",
    "isolation_unavailable",
    "memory_limit",
    "cleanup_unverified",
    "worktree_changed",
    "profile_disabled",
]
"""Closed public reasons; never application output or exception text."""


class StartPreview(ContractModel):
    """POST /previews: start the observed agent candidate."""

    run_id: RunId
    """Observed live run."""
    agent_id: AgentId
    """Target coding agent; its change is derived on the server."""
    revision: PositiveInt
    """Observed proposed revision."""
    head_commit: NonEmptyText
    """Observed proposed head, compared exactly with server state."""


class PreviewCreated(ContractModel):
    """202 from POST /previews; execution has not yet become ready."""

    preview_id: PreviewId
    """Identifier for status, renewal and stop."""
    lease_token: PreviewLeaseToken
    """Returned once to the initiating tab; never included in events or GET."""


class PreviewTokenRequest(ContractModel):
    """Required body for POST /previews/{id}/lease and /stop."""

    lease_token: PreviewLeaseToken
    """Proof of possession of this preview's initiating lease."""


class PreviewStatus(ContractModel):
    """Public lifecycle facts; also the lease and stop response."""

    preview_id: PreviewId
    """Server-assigned preview identifier."""
    run_id: RunId
    """Run containing the candidate."""
    agent_id: AgentId
    """Coding agent whose worktree was selected."""
    change_id: ChangeId
    """Change derived from that agent by the server."""
    revision: PositiveInt
    """Candidate revision pinned at launch."""
    head_commit: NonEmptyText
    """Candidate head pinned at launch."""
    state: PreviewState
    """Current lifecycle state."""
    reason: PreviewReason | None = None
    """Public cause; null until a stopping or failure cause exists."""

    @model_validator(mode="after")
    def _state_reason(self) -> Self:
        if (self.state in ("starting", "ready")) != (self.reason is None):
            raise ValueError("starting/ready require null reason; other states require a reason")
        return self


class PreviewDetails(ContractModel):
    """200 from GET /previews/{id}; live runtime information only."""

    status: PreviewStatus
    """Current public lifecycle facts."""
    url: NonEmptyText | None = None
    """Server-built loopback frame URL only while ready; otherwise null."""

    @model_validator(mode="after")
    def _ready_url(self) -> Self:
        if self.url is not None and self.status.state != "ready":
            raise ValueError("url is non-null only while ready")
        return self
