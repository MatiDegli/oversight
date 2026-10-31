"""Pure supervision over a complete committed event prefix, with no decision authority."""

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, fields
from math import fsum
from typing import Literal

from oversight.events.usage_fold import UnassignedUsage, fold_usage
from oversight_contracts.events import ChangeProposed, ChangeResolved, ServerEvent
from oversight_contracts.review import ChangeUsage

type _End = Literal["open", "merged", "conflict", "rejected", "stopped"]


@dataclass(frozen=True)
class Outcomes:
    merged: int = 0
    conflict: int = 0
    changes_requested: int = 0
    rejected: int = 0
    stopped: int = 0


@dataclass(frozen=True)
class Decisions:
    person: int = 0
    rules: int = 0
    cross_review: int = 0
    lead_agent: int = 0
    unrecorded: int = 0


@dataclass(frozen=True)
class ChangeSupervision:
    change_id: str
    agent_id: str
    proposals: int
    review_rounds: int
    last_revision: int | None
    lines_added: int | None
    lines_removed: int | None
    end: _End
    net_lines_merged: int | None
    usage: ChangeUsage
    outcomes: Outcomes


@dataclass(frozen=True)
class Spend:
    changes: int
    cost_usd: float | None
    unknown_calls: int


@dataclass(frozen=True)
class NotMerged:
    rejected: Spend
    stopped: Spend
    conflict: Spend
    earlier_rounds: Spend


@dataclass(frozen=True)
class SupervisionTotals:
    changes: int
    proposals: int
    review_rounds: int
    open_changes: int
    outcomes: Outcomes
    lines_added: int
    lines_removed: int
    net_lines_merged: int | None
    steps: int
    step_ms: int
    cost_usd: float | None
    unknown_calls: int


@dataclass(frozen=True)
class AgentSupervision(SupervisionTotals):
    agent_id: str


@dataclass(frozen=True)
class SupervisionFold:
    through_seq: int
    agents: tuple[AgentSupervision, ...]
    changes: tuple[ChangeSupervision, ...]
    run: SupervisionTotals
    decisions: Decisions
    not_merged: NotMerged


@dataclass
class _Change:
    agent_id: str
    proposals: list[ChangeProposed] = field(default_factory=list[ChangeProposed])
    merged_revisions: list[int] = field(default_factory=list[int])
    outcomes: Counter[str] = field(default_factory=Counter[str])
    end: _End = "open"
    round: int = 0
    merged_round: int = 0

    def resolve(self, resolution: ChangeResolved) -> None:
        if resolution.decision == "approved":
            self.end = "merged" if resolution.merge == "merged" else "conflict"
        elif resolution.decision == "changes_requested":
            self.end = "open"
        else:
            self.end = "stopped" if resolution.reason == "stopped" else "rejected"
        outcome = "changes_requested" if self.end == "open" else self.end
        self.outcomes[outcome] += 1
        if outcome == "merged":
            self.merged_revisions.append(resolution.revision)
            self.merged_round = self.round
        self.round += outcome == "changes_requested"

    def finish(self, change_id: str, usage: ChangeUsage) -> ChangeSupervision:
        last = self.proposals[-1] if self.proposals else None
        by_revision = {p.revision: p for p in self.proposals}
        net = (
            sum(
                by_revision[r].lines_added - by_revision[r].lines_removed
                for r in self.merged_revisions
            )
            if all(r in by_revision for r in self.merged_revisions)
            else None
        )
        return ChangeSupervision(
            change_id,
            self.agent_id,
            len(self.proposals),
            self.round,
            last.revision if last else None,
            last.lines_added if last else None,
            last.lines_removed if last else None,
            self.end,
            net,
            usage,
            Outcomes(**self.outcomes),
        )


def _cost(costs: Iterable[float | None]) -> float | None:
    parts = list(costs)
    return None if any(cost is None for cost in parts) else fsum(c for c in parts if c is not None)


def _totals(
    changes: Sequence[ChangeSupervision],
    steps: Sequence[int],
    unassigned: Sequence[UnassignedUsage],
) -> SupervisionTotals:
    return SupervisionTotals(
        changes=sum(c.proposals > 0 for c in changes),
        proposals=sum(c.proposals for c in changes),
        review_rounds=sum(c.review_rounds for c in changes),
        open_changes=sum(c.end == "open" for c in changes),
        outcomes=Outcomes(
            **{f.name: sum(getattr(c.outcomes, f.name) for c in changes) for f in fields(Outcomes)}
        ),
        lines_added=sum(c.lines_added or 0 for c in changes),
        lines_removed=sum(c.lines_removed or 0 for c in changes),
        net_lines_merged=None
        if any(c.net_lines_merged is None for c in changes)
        else sum(c.net_lines_merged or 0 for c in changes),
        steps=len(steps),
        step_ms=sum(steps),
        cost_usd=_cost([c.usage.cost_usd for c in changes] + [u.cost_usd for u in unassigned]),
        unknown_calls=sum(c.usage.unknown_calls or 0 for c in changes)
        + sum(u.cost_usd is None for u in unassigned),
    )


def _not_merged(changes: Sequence[ChangeSupervision], states: dict[str, _Change]) -> NotMerged:
    spend: dict[str, Spend] = {}
    for end in ("rejected", "stopped", "conflict", "earlier_rounds"):
        costs: list[float | None] = []
        unknown = count = 0
        for change in changes:
            if end == "earlier_rounds" and change.end == "merged":
                buckets = [
                    b
                    for b in change.usage.by_role or ()
                    if b.round is not None and b.round < states[change.change_id].merged_round
                ]
                count += bool(buckets)
                costs.extend(b.cost_usd for b in buckets)
                unknown += sum(b.unknown_calls or 0 for b in buckets)
            elif change.end == end:
                count += 1
                costs.append(change.usage.cost_usd)
                unknown += change.usage.unknown_calls or 0
        spend[end] = Spend(count, _cost(costs), unknown)
    return NotMerged(**spend)


def fold_supervision(events: Sequence[ServerEvent]) -> SupervisionFold:
    """Fold a complete ordered prefix from seq 1; refuse gaps, costs or conflicting owners.

    Only proposals, resolutions and attributed ticks establish owners. Other change references
    supply first-appearance order through fold_usage but cannot invent an owner or a call.
    Dollars come exclusively from that fold; resolution order determines review rounds.
    """
    usage = fold_usage(events)
    states: dict[str, _Change] = {}
    steps: dict[str, list[int]] = defaultdict(list)
    decisions: Counter[str] = Counter()
    for event in events:
        if event.type == "agent.step":
            steps[event.payload.agent_id].append(event.payload.duration_ms)
        elif (
            event.type == "change.proposed"
            or event.type == "change.resolved"
            or event.type == "usage.tick"
        ):
            payload = event.payload
            if payload.change_id is None:
                continue
            state = states.setdefault(payload.change_id, _Change(payload.agent_id))
            if state.agent_id != payload.agent_id:
                raise ValueError(f"two owners for change {payload.change_id} at seq {event.seq}")
            if event.type == "change.proposed":
                state.proposals.append(event.payload)
                state.end = "open"
            elif event.type == "change.resolved":
                state.resolve(event.payload)
                decisions[event.payload.decided_by or "unrecorded"] += 1
    changes = tuple(
        states[cid].finish(cid, accounting.usage)
        for cid, accounting in usage.by_change.items()
        if cid in states
    )
    agent_ids = sorted(
        set(steps) | {c.agent_id for c in changes} | {u.agent_id for u in usage.unassigned}
    )
    agents = tuple(
        AgentSupervision(
            **{f.name: getattr(totals, f.name) for f in fields(SupervisionTotals)},
            agent_id=agent_id,
        )
        for agent_id in agent_ids
        for totals in [
            _totals(
                [c for c in changes if c.agent_id == agent_id],
                steps[agent_id],
                [u for u in usage.unassigned if u.agent_id == agent_id],
            )
        ]
    )
    return SupervisionFold(
        usage.through_seq,
        agents,
        changes,
        _totals(changes, [ms for values in steps.values() for ms in values], usage.unassigned),
        Decisions(**decisions),
        _not_merged(changes, states),
    )
