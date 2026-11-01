"""Change wall spans and observed activity for one run."""

from datetime import timedelta

from oversight.events import fold_activity, parse_ts
from oversight_contracts.events import ServerEvent

from .inputs import Inputs


def wall_ms(start: ServerEvent | None, end: ServerEvent | None) -> int | None:
    if start is None or end is None:
        return None
    span = (parse_ts(end.ts) - parse_ts(start.ts)) // timedelta(milliseconds=1)
    return span if span >= 0 else None


def add_timing(
    inputs: Inputs, revisions: list[dict], changes: list[dict], arms: list[dict]
) -> dict:
    starts, merges = {}, {}
    for event in inputs.events:
        cid = getattr(event.payload, "change_id", None)
        if cid is not None:
            starts.setdefault(cid, event)
        if (
            event.type == "change.resolved"
            and event.payload.decision == "approved"
            and event.payload.merge == "merged"
        ):
            merges[cid] = event
    for change in changes:
        cid = change["change_id"]
        green = inputs.proposals.get((cid, change["first_green"]))
        merged = merges.get(cid) if (cid, change["merged_revision"]) in inputs.proposals else None
        change["wall_ms"] = dict(
            without_oversight=wall_ms(starts.get(cid), green),
            with_oversight=wall_ms(starts.get(cid), merged),
        )
    correct = {(r["change_id"], r["revision"]) for r in revisions if r["correct"] is True}
    for arm, selection in zip(arms, ("first_green", "merged_revision"), strict=True):
        spans = [
            c["wall_ms"][arm["arm"]]
            for c in changes
            if (c["change_id"], c[selection]) in correct and c["wall_ms"][arm["arm"]] is not None
        ]
        arm["wall_ms_per_correct"] = sum(spans) // len(spans) if spans else None
    return run_record(inputs)


def run_record(inputs: Inputs) -> dict:
    activity = fold_activity(inputs.events)
    return dict(
        record="run",
        agent_ms=activity.agent_ms,
        known_agent_ms=activity.known_agent_ms,
        agent_hours=activity.agent_ms / 3600000 if activity.agent_ms is not None else None,
        active_ms=activity.active_ms,
        known_active_ms=activity.known_active_ms,
        parallelism=activity.parallelism,
        coverage=activity.coverage,
        problems=list(activity.problems),
        parallelism_over_time=dict(status="not_built"),
    )
