"""Pure queue transitions, refusal policy and FIFO selection; no slot ownership here."""

from collections.abc import Iterable

from oversight.changes._plans import ready
from oversight_contracts.commands import EnqueueTask
from oversight_contracts.plans import Plan
from oversight_contracts.queue import QueuedTask

QUEUE_LIMIT = 32
TERMINAL = frozenset({"completed", "cancelled", "failed"})
TEMPORARY_REFUSALS = frozenset({"slot_busy", "spend_brake", "not_configured", "wrong_state"})
TRANSITIONS = {
    "queued": {"queued", "starting", "running", "blocked", "failed", "cancelled"},
    "blocked": {"blocked", "queued", "cancelled", "failed"},
    "starting": {"starting", "running", "completed", "failed"},
    "running": {"running", "completed", "failed"},
}


def queued(request: EnqueueTask, number: int) -> QueuedTask:
    return QueuedTask(
        **request.model_dump(),
        queued_task_id=f"queued-task-{number}",
        revision=1,
        state="queued",
        agent_ids=[],
        detail=None,
        started_by=None,
    )


def transition(
    item: QueuedTask,
    state: str,
    *,
    detail: str | None = None,
    agent_id: str | None = None,
) -> QueuedTask:
    if state not in TRANSITIONS.get(item.state, set()):
        raise ValueError(f"cannot move queue item from {item.state} to {state}")
    return QueuedTask.model_validate(
        item.model_dump()
        | {
            "state": state,
            "revision": item.revision + 1,
            "detail": detail,
            "agent_ids": [*item.agent_ids, agent_id] if agent_id is not None else item.agent_ids,
        }
    )


def refusal_state(item: QueuedTask, code: str, *, launch_failed: bool = False) -> str:
    # Coordinator's answer to CONTESTED (2026-10-30): before any launch use the F1
    # table; after a launch keep recoverable refusals waiting in starting/running.
    if item.plan_id is not None:
        if launch_failed or code not in TEMPORARY_REFUSALS - {"not_configured"}:
            return "failed"
        if item.agent_ids:
            return item.state
    if code == "slot_busy":
        return "queued"
    return "blocked" if code in TEMPORARY_REFUSALS else "failed"


def candidates(
    items: Iterable[QueuedTask], plans: dict[str, Plan] | None = None
) -> list[QueuedTask]:
    """Ready subtasks in dispatched plans precede strict head-of-line FIFO work."""
    plans = plans or {}

    def active(item: QueuedTask) -> bool:
        return (
            item.state in {"starting", "running"}
            and item.plan_id in plans
            and bool(ready(plans[item.plan_id]))
        )

    return sorted(
        (item for item in items if item.state in {"queued", "blocked"} or active(item)),
        key=lambda item: (not active(item), int(item.queued_task_id.removeprefix("queued-task-"))),
    )
