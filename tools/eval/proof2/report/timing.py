"""Change wall spans and observed activity for one run."""

from datetime import timedelta
from typing import TypedDict

from oversight.events import fold_activity, parse_ts
from oversight_contracts.events import ServerEvent

from .arms import ArmRecord, ChangeRecord, RevisionRecord
from .inputs import Inputs


class RunRecord(TypedDict):
    record: str
    agent_ms: int | None
    known_agent_ms: int
    agent_hours: float | None
    active_ms: int | None
    known_active_ms: int
    parallelism: float | None
    coverage: str
    problems: list[str]
    parallelism_over_time: dict[str, str]


def wall_ms(start: ServerEvent | None, end: ServerEvent | None) -> int | None:
    if start is None or end is None:
        return None
    span = (parse_ts(end.ts) - parse_ts(start.ts)) // timedelta(milliseconds=1)
    return span if span >= 0 else None


def add_timing(
    inputs: Inputs,
    revisions: list[RevisionRecord],
    changes: list[ChangeRecord],
    arms: list[ArmRecord],
) -> tuple[list[ChangeRecord], list[ArmRecord], RunRecord]:
    starts: dict[str, ServerEvent] = {}
    merges: dict[str, ServerEvent] = {}
    for event in inputs.events:
        cid = getattr(event.payload, "change_id", None)
        if cid is not None:
            starts.setdefault(cid, event)
        if (
            event.type == "change.resolved"
            and event.payload.decision == "approved"
            and event.payload.merge == "merged"
        ):
            merges[event.payload.change_id] = event
    timed_changes = [change.copy() for change in changes]
    for change in timed_changes:
        cid = change["change_id"]
        first_green = change["first_green"]
        green = inputs.proposals.get((cid, first_green)) if first_green is not None else None
        merged = merges.get(cid) if (cid, change["merged_revision"]) in inputs.proposals else None
        change["wall_ms"] = dict(
            without_oversight=wall_ms(starts.get(cid), green),
            with_oversight=wall_ms(starts.get(cid), merged),
        )
    correct = {(r["change_id"], r["revision"]) for r in revisions if r["correct"] is True}
    timed_arms = [arm.copy() for arm in arms]
    for arm, selection in zip(timed_arms, ("first_green", "merged_revision"), strict=True):
        spans: list[int] = []
        for change in timed_changes:
            span = change.get("wall_ms", {}).get(arm["arm"])
            if (change["change_id"], change[selection]) in correct and span is not None:
                spans.append(span)
        arm["wall_ms_per_correct"] = sum(spans) // len(spans) if spans else None
    return timed_changes, timed_arms, run_record(inputs)


def run_record(inputs: Inputs) -> RunRecord:
    activity = fold_activity(inputs.events)
    return RunRecord(
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
