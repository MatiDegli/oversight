"""Reserved FIFO work items; provenance is assigned by the server."""

from typing import Literal, Self

from pydantic import NonNegativeInt, PositiveInt, model_validator

from oversight_contracts.common import (
    AgentId,
    ContractModel,
    NonEmptyText,
    PlanId,
    QueuedTaskId,
    RunId,
    Slot,
)
from oversight_contracts.plans import PlanZone


class QueuedAgentTask(ContractModel):
    """One coding task waiting for a slot."""

    task: NonEmptyText
    """The queued coding task."""
    slot: Slot | None
    """The requested slot, or any free slot."""
    model: str | None
    """The requested profile, or slot default."""
    zone: PlanZone | None
    """The requested edit zone, or unknown."""


class QueueSpec(ContractModel):
    """Exactly one standalone task or exact proposed plan."""

    agent: QueuedAgentTask | None = None
    """One standalone coding task."""
    plan_id: PlanId | None = None
    """One proposed two-agent plan."""
    plan_revision: PositiveInt | None = None
    """The exact queued plan revision."""

    @model_validator(mode="after")
    def _one_work_item(self) -> Self:
        if (self.agent is None) == (self.plan_id is None):
            raise ValueError("queue exactly one agent task or plan")
        if (self.plan_id is None) != (self.plan_revision is None):
            raise ValueError("plan revision is set exactly with a plan")
        return self


class QueuedTask(QueueSpec):
    """One server-owned versioned queue item."""

    queued_task_id: QueuedTaskId
    """The server-assigned queue identifier."""
    revision: PositiveInt
    """The current queue-item revision."""
    state: Literal["queued", "starting", "running", "completed", "cancelled", "blocked", "failed"]
    """The queue-item lifecycle state."""
    agent_ids: list[AgentId]
    """Agents assigned during dispatch."""
    detail: str | None
    """The dispatch or failure explanation."""
    started_by: AgentId | None = None
    """The lead that queued this task."""

    @model_validator(mode="after")
    def _dispatch_shape(self) -> Self:
        if self.plan_id is not None and self.started_by is not None:
            raise ValueError("only a person may queue a plan")
        if len(set(self.agent_ids)) != len(self.agent_ids):
            raise ValueError("assigned agents must be distinct")
        if self.state in {"starting", "running", "completed"} and len(self.agent_ids) != (
            2 if self.plan_id else 1
        ):
            raise ValueError("dispatched work requires all assigned agents")
        if self.state in {"queued", "cancelled", "blocked"} and self.agent_ids:
            raise ValueError("undispatched work has no agents")
        if self.state in {"blocked", "failed"} and not (self.detail and self.detail.strip()):
            raise ValueError("blocked or failed work requires detail")
        return self


class QueueSnapshot(ContractModel):
    """The queue state read atomically with its event position."""

    run_id: RunId
    """The current run."""
    seq: NonNegativeInt
    """The event position read with this state."""
    tasks: list[QueuedTask]
    """The queue's current items."""
