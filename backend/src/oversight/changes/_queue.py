"""Pure standalone queue transitions and FIFO selection; no slot ownership here."""

from collections.abc import Iterable

from oversight_contracts.commands import EnqueueTask
from oversight_contracts.queue import QueuedTask

QUEUE_LIMIT = 32
TERMINAL = frozenset({"completed", "cancelled", "failed"})
TRANSITIONS = {
    "queued": {"running", "blocked", "failed", "cancelled"},
    "blocked": {"queued", "cancelled", "failed"},
    "running": {"completed"},
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
            "agent_ids": [agent_id] if agent_id is not None else item.agent_ids,
        }
    )


def candidates(items: Iterable[QueuedTask]) -> list[QueuedTask]:
    """Strict head-of-line FIFO; only blocked items may be skipped by the dispatcher.

    F2 can prepend ready dependents here without changing slot admission or launch ownership.
    """
    return sorted(
        (item for item in items if item.state in {"queued", "blocked"}),
        key=lambda item: int(item.queued_task_id.removeprefix("queued-task-")),
    )
