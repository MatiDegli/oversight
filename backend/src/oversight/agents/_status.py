"""Pure server-owned coding-agent provenance checks."""

from oversight_contracts.events import AgentStatus
from oversight_contracts.plans import PlanZone


def validate_agent_status(
    status: AgentStatus,
    *,
    base_commit: str | None,
    zone: PlanZone | None = None,
    started_by: str | None = None,
    queued_task_id: str | None = None,
) -> None:
    """Compare with the server registration, never provenance from request data."""
    if (status.base_commit, status.zone, status.started_by, status.queued_task_id) != (
        base_commit,
        zone,
        started_by,
        queued_task_id,
    ):
        raise ValueError("agent status differs from server-owned base and provenance")
    if not base_commit:
        raise ValueError("live coding-agent status requires its recorded base")
