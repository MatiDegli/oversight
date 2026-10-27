"""Capture observed targets synchronously, before a decision reserves or waits on a lock."""

from typing import Any, TypedDict


class Guard(TypedDict):
    revision: int
    head_commit: str


def observed_target(manager: Any, change_id: str) -> Guard:
    record = manager._ctx.state.changes.get(change_id)
    if record is None:
        return {"revision": 1, "head_commit": "unknown-change"}
    return {"revision": record.revision, "head_commit": record.head_commit or record.base_commit}


def observed_body(server: Any, change_id: str = "change-1") -> Guard:
    events = server.events()
    proposals = [
        e.payload
        for e in events
        if e.type == "change.proposed" and e.payload.change_id == change_id
    ]
    if proposals:
        return {"revision": proposals[-1].revision, "head_commit": proposals[-1].head_commit}
    statuses = [
        e.payload for e in events if e.type == "agent.status" and e.payload.change_id == change_id
    ]
    if statuses and statuses[-1].base_commit:
        return {"revision": 0, "head_commit": statuses[-1].base_commit}
    return {"revision": 1, "head_commit": "unknown-change"}
