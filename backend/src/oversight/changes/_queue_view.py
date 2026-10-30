"""Queue reads fold a single inventory of committed events, including in replay."""

from oversight.events import EventStore
from oversight_contracts.queue import QueueSnapshot


def read_queue(store: EventStore) -> QueueSnapshot:
    events = store.committed_events()
    items = {
        event.payload.queued_task_id: event.payload
        for event in events
        if event.type == "queue.updated"
    }
    return QueueSnapshot(
        run_id=store.run_id,
        seq=events[-1].seq if events else 0,
        tasks=sorted(items.values(), key=lambda item: int(item.queued_task_id.rsplit("-", 1)[1])),
    )
