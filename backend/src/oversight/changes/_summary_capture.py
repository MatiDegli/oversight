"""Freeze candidate and usage observations before the pure summary builder runs."""

from collections import defaultdict
from math import fsum

from pydantic import TypeAdapter

from oversight.changes._state import ChangeRecord, ChangeState
from oversight.changes._summary import SummaryEvidence
from oversight.events import StoredEvent
from oversight_contracts.events import UsageTickEvent
from oversight_contracts.review import RoleUsage

_TICK = TypeAdapter(UsageTickEvent)


def record_usage(state: ChangeState, receipt: StoredEvent) -> None:
    """Only committed, explicitly attributed ticks; seq makes redelivery idempotent."""
    if receipt.type != "usage.tick":
        return
    tick = _TICK.validate_json(receipt.envelope).payload
    rec = state.changes.get(tick.change_id) if tick.change_id is not None else None
    if rec is None or (tick.role == "agent" and tick.agent_id != rec.agent_id):
        return
    rec.summary_usage.setdefault(
        receipt.seq,
        RoleUsage(
            role=tick.role,
            model=tick.model,
            round=rec.requests,
            cost_usd=tick.cost_usd,
            unknown_calls=int(tick.cost_usd is None),
        ),
    )


def _usage(rec: ChangeRecord) -> tuple[RoleUsage, ...] | None:
    groups: dict[tuple[str | None, str | None, int | None], list[RoleUsage]] = defaultdict(list)
    for _, bucket in sorted(rec.summary_usage.items()):
        groups[bucket.role, bucket.model, bucket.round].append(bucket)
    buckets: list[RoleUsage] = []
    for calls in groups.values():
        first = calls[0]
        unknown = sum(call.unknown_calls or 0 for call in calls)
        try:
            cost = fsum(call.cost_usd for call in calls if call.cost_usd is not None)
        except OverflowError:
            cost = None
        buckets.append(
            first.model_copy(
                update={
                    "cost_usd": None
                    if unknown or first.role is None or first.model is None
                    else cost,
                    "unknown_calls": unknown,
                }
            )
        )
    return tuple(buckets) or None


def capture_summary(
    rec: ChangeRecord, *, added_test_ids: tuple[str, ...] | None
) -> SummaryEvidence:
    return SummaryEvidence(
        task=rec.task.task,
        changed_paths=tuple(
            sorted(
                path
                for path in rec.base_files.keys() | rec.head_files.keys()
                if rec.base_files.get(path) != rec.head_files.get(path)
            )
        ),
        changed_nodes=tuple(
            sorted(
                {node.id for node in rec.graph.nodes_added}
                | set(rec.graph.nodes_modified)
                | set(rec.graph.nodes_removed)
            )
        ),
        added_test_ids=added_test_ids,
        usage=_usage(rec),
    )
