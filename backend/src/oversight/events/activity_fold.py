"""Pure accounting of observed activity over a complete committed prefix; no decision authority."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from oversight.events.clock import parse_ts
from oversight_contracts.events import AgentStatus, ChangeReviewedEvent, ServerEvent, UsageTick

type ActorRole = Literal["agent", "review", "lead"]
type Coverage = Literal["complete", "missing_metrics", "incomplete"]
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_COUNTING = {"starting", "working", "revising"}


@dataclass(frozen=True)
class ActivityInterval:
    start_seq: int
    end_seq: int | None
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class ActorActivity:
    actor_id: str
    role: ActorRole
    model: str | None
    change_id: str | None
    intervals: tuple[ActivityInterval, ...]
    ms: int | None
    open: bool


@dataclass(frozen=True)
class ActivityGroup:
    role: ActorRole
    model: str | None
    actors: int
    known_ms: int
    ms: int | None


@dataclass(frozen=True)
class ActivityFold:
    through_seq: int
    actors: tuple[ActorActivity, ...]
    groups: tuple[ActivityGroup, ...]
    known_agent_ms: int
    agent_ms: int | None
    known_active_ms: int
    active_ms: int | None
    parallelism: float | None
    coverage: Coverage
    problems: tuple[str, ...]
    groups_add_up: bool


@dataclass
class _Actor:
    actor_id: str
    role: ActorRole
    model: str | None = None
    change_id: str | None = None
    intervals: list[ActivityInterval] = field(default_factory=list[ActivityInterval])
    start: tuple[int, int] | None = None
    has_status: bool = False
    work_seq: int | None = None
    ready_change: str | None = None

    def finish(self, last_ms: int) -> ActorActivity:
        intervals = list(self.intervals)
        if self.start is not None:
            seq, ms = self.start
            intervals.append(ActivityInterval(seq, None, ms, last_ms))
        unknown = (
            self.role == "lead"
            or (not intervals and (self.role == "review" or self.work_seq is not None))
            or any(interval.end_ms < interval.start_ms for interval in intervals)
        )
        return ActorActivity(
            self.actor_id,
            self.role,
            self.model,
            self.change_id,
            tuple(intervals),
            None if unknown else sum(interval.end_ms - interval.start_ms for interval in intervals),
            self.start is not None,
        )


def _active_ms(actors: tuple[ActorActivity, ...]) -> int:
    intervals = sorted(
        (interval.start_ms, interval.end_ms)
        for actor in actors
        for interval in actor.intervals
        if interval.end_ms >= interval.start_ms
    )
    total = 0
    end: int | None = None
    for start, stop in intervals:
        total += max(0, stop - max(start, end)) if end is not None else stop - start
        end = max(end, stop) if end is not None else stop
    return total


def _groups(actors: tuple[ActorActivity, ...], coverage: Coverage) -> tuple[ActivityGroup, ...]:
    buckets: dict[tuple[ActorRole, str | None], list[ActorActivity]] = {}
    for actor in actors:
        buckets.setdefault((actor.role, actor.model), []).append(actor)
    groups: list[ActivityGroup] = []
    for role, model in sorted(
        buckets, key=lambda key: (("agent", "review", "lead").index(key[0]), key[1] is None, key[1])
    ):
        members = buckets[role, model]
        known = sum(actor.ms for actor in members if actor.ms is not None)
        complete = coverage != "incomplete" and all(actor.ms is not None for actor in members)
        groups.append(ActivityGroup(role, model, len(members), known, known if complete else None))
    return tuple(groups)


class _Builder:
    def __init__(self) -> None:
        self.actors: dict[tuple[ActorRole, str], _Actor] = {}
        self.models: dict[str, str] | None = None
        self.ready: dict[str, tuple[int, int]] = {}
        self.reviewed: dict[str, tuple[int, int]] = {}
        self.review_calls: dict[str | None, int] = {}
        self.problems: dict[str, int] = {}
        self.incomplete = False

    def problem(self, seq: int, text: str) -> None:
        self.problems.setdefault(text, seq)

    def actor(self, role: ActorRole, actor_id: str) -> _Actor:
        return self.actors.setdefault((role, actor_id), _Actor(actor_id, role))

    def model(self, model: str) -> str:
        return (self.models or {}).get(model, model)

    def status(self, status: AgentStatus, seq: int, ms: int) -> None:
        actor = self.actor("agent", status.agent_id)
        if not actor.has_status:
            actor.model = self.model(status.model)
            actor.has_status = True
        actor.change_id = status.change_id
        if status.state in _COUNTING:
            if actor.start is None:
                actor.start = (seq, ms)
        elif actor.start is not None:
            start_seq, start_ms = actor.start
            actor.intervals.append(ActivityInterval(start_seq, seq, start_ms, ms))
            actor.start = None
        if status.state == "ready_for_review" and actor.ready_change != status.change_id:
            self.ready[status.change_id] = (seq, ms)
        actor.ready_change = status.change_id if status.state == "ready_for_review" else None

    def review(self, event: ChangeReviewedEvent, ms: int) -> None:
        review = event.payload
        actor = self.actor("review", f"review@{event.seq}")
        actor.model = review.reviewer_model
        actor.change_id = review.change_id
        starts = [
            start
            for start in (self.ready.get(review.change_id), self.reviewed.get(review.change_id))
            if start is not None
        ]
        if starts:
            seq, start_ms = max(starts)
            actor.intervals.append(ActivityInterval(seq, event.seq, start_ms, ms))
        else:
            self.problem(event.seq, f"review at seq {event.seq} has no ready_for_review start")
        self.reviewed[review.change_id] = (event.seq, ms)

    def lead(self, agent_id: str, model: str | None, seq: int) -> None:
        key = ("lead", agent_id)
        if key not in self.actors:
            self.actor("lead", agent_id).model = model
        self.problem(seq, "lead hours are unavailable")

    def work(self, agent_id: str, seq: int) -> None:
        actor = self.actor("agent", agent_id)
        if actor.work_seq is None:
            actor.work_seq = seq

    def usage(self, tick: UsageTick, seq: int) -> None:
        if tick.role in (None, "agent"):
            self.work(tick.agent_id, seq)
        elif tick.role == "review":
            self.review_calls.setdefault(tick.change_id, seq)
        elif tick.role == "plan":
            self.problem(seq, "plan calls have no activity interval")
        elif tick.role == "lead":
            self.lead(tick.agent_id, tick.model, seq)

    def consume(self, event: ServerEvent, ms: int) -> None:
        if event.type == "run.started" and self.models is None:
            self.models = {profile.id: profile.model for profile in event.payload.models}
        elif event.type == "agent.status":
            self.status(event.payload, event.seq, ms)
        elif event.type == "change.reviewed":
            self.review(event, ms)
        elif event.type == "lead.status":
            self.lead(event.payload.agent_id, self.model(event.payload.model), event.seq)
        elif event.type == "agent.step":
            self.work(event.payload.agent_id, event.seq)
        elif event.type == "usage.tick":
            self.usage(event.payload, event.seq)

    def finish(self, through_seq: int, last_ms: int) -> ActivityFold:
        actors = tuple(actor.finish(last_ms) for actor in self.actors.values())
        for builder, actor in zip(self.actors.values(), actors, strict=True):
            if builder.work_seq is not None and not actor.intervals:
                self.problem(builder.work_seq, f"{actor.actor_id} worked with no activity interval")
        for change, seq in self.review_calls.items():
            if change not in self.reviewed:
                subject = f"for {change}" if change is not None else "without a change"
                self.problem(seq, f"review calls {subject} have no change.reviewed")
        coverage: Coverage = (
            "incomplete" if self.incomplete else "missing_metrics" if self.problems else "complete"
        )
        known_agent = sum(actor.ms for actor in actors if actor.ms is not None)
        known_active = _active_ms(actors)
        agent_ms = known_agent if coverage == "complete" else None
        active_ms = known_active if coverage == "complete" else None
        groups = _groups(actors, coverage)
        return ActivityFold(
            through_seq,
            actors,
            groups,
            known_agent,
            agent_ms,
            known_active,
            active_ms,
            agent_ms / active_ms if agent_ms is not None and active_ms else None,
            coverage,
            tuple(sorted(self.problems, key=lambda text: self.problems[text])),
            coverage == "complete" and sum(group.ms or 0 for group in groups) == agent_ms,
        )


def fold_activity(events: Sequence[ServerEvent]) -> ActivityFold:
    """Account for observed intervals in a complete prefix from seq 1; reject invalid seqs."""
    builder = _Builder()
    through_seq = 0
    last_ms: int | None = None
    for expected, event in enumerate(events, start=1):
        if type(event.seq) is not int or event.seq != expected:
            raise ValueError(f"expected seq {expected}, got {event.seq!r}")
        ms = (parse_ts(event.ts) - _EPOCH) // timedelta(milliseconds=1)
        if last_ms is not None and ms < last_ms:
            builder.problem(event.seq, f"ts goes back at seq {event.seq}")
            builder.incomplete = True
        through_seq = expected
        last_ms = ms
        builder.consume(event, ms)
    return builder.finish(through_seq, last_ms if last_ms is not None else 0)
