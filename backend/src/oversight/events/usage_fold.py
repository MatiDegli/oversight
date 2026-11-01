"""Pure accounting over a complete committed event prefix, with no decision authority."""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from math import fsum, isfinite
from typing import Literal, assert_never

from oversight_contracts.events import ServerEvent
from oversight_contracts.review import ChangeUsage, RoleUsage

type _Role = Literal["agent", "review", "plan", "lead"]
type _Bucket = tuple[_Role | None, str | None, int]


@dataclass(frozen=True)
class ChangeAccounting:
    usage: ChangeUsage
    revision_rounds: int


@dataclass(frozen=True)
class UnassignedUsage:
    """One call without a change; retain its attribution without guessing a round."""

    seq: int
    agent_id: str
    role: _Role | None
    model: str | None
    plan_id: str | None
    cost_usd: float | None


@dataclass(frozen=True)
class RunUsage:
    """All calls, including unassigned calls; independent of the Meta credit ledger.

    Run dollar totals require known costs, but not role/model/change attribution. Bucket and
    change totals also require their bucket attribution. known_cost_usd is only a subtotal when
    unknown_calls is positive, never an estimate of the unknown calls' spend.
    """

    calls: int
    known_cost_usd: float
    unknown_calls: int
    cost_usd: float | None
    accepted_changes: int
    cost_per_accepted_change_usd: float | None


@dataclass(frozen=True)
class UsageFold:
    """Backend-only projection, reconstructed independently on every call."""

    through_seq: int
    by_change: dict[str, ChangeAccounting]
    unassigned: tuple[UnassignedUsage, ...]
    run: RunUsage


def _sum_costs(costs: Iterable[float]) -> float:
    try:
        total = fsum(costs)
    except OverflowError as error:
        raise ValueError("cost total exceeds finite arithmetic") from error
    if not isfinite(total):
        raise ValueError("cost total must be finite")
    return total


def _change_usage(groups: dict[_Bucket, list[float | None]]) -> ChangeUsage:
    buckets: list[RoleUsage] = []
    for (role, model, round_number), costs in groups.items():
        unknown = sum(cost is None for cost in costs)
        known = _sum_costs(cost for cost in costs if cost is not None)
        buckets.append(
            RoleUsage(
                role=role,
                model=model,
                round=round_number,
                cost_usd=None if unknown or role is None or model is None else known,
                unknown_calls=unknown,
            )
        )
    # Sum original calls, rather than rounded bucket totals, for stable finite arithmetic.
    known = _sum_costs(cost for costs in groups.values() for cost in costs if cost is not None)
    return ChangeUsage(
        cost_usd=None if any(bucket.cost_usd is None for bucket in buckets) else known,
        unknown_calls=sum(bucket.unknown_calls or 0 for bucket in buckets),
        by_role=buckets,
    )


def _run_usage(costs: list[float], unknown: int, calls: int, accepted: int) -> RunUsage:
    known = _sum_costs(costs)
    total = None if unknown else known
    return RunUsage(
        calls=calls,
        known_cost_usd=known,
        unknown_calls=unknown,
        cost_usd=total,
        accepted_changes=accepted,
        cost_per_accepted_change_usd=total / accepted if total is not None and accepted else None,
    )


def fold_usage(events: Sequence[ServerEvent]) -> UsageFold:
    """Fold a complete, ordered, committed prefix starting at seq 1 (or an empty prefix).

    Reject missing/duplicate/out-of-order seqs and invalid costs with ValueError. A suffix cannot
    establish rounds or complete accounting. Completeness refers to the committed inventory;
    absent receipts or calls never recorded by a producer cannot be reconstructed here.

    Round zero precedes confirmed changes_requested resolutions; each such resolution increments
    that change's round for subsequent ticks. This is receipt order, not proof of the originating
    call's revision. Proposal revisions, agent ids, model names and timing imply no attribution.
    In particular, reviewer and decision calls remain combined under the existing review role.
    """
    rounds: dict[str, int] = {}
    groups: dict[str, dict[_Bucket, list[float | None]]] = {}
    accepted: set[str] = set()
    unassigned: list[UnassignedUsage] = []
    costs: list[float] = []
    unknown = 0
    calls = 0
    through_seq = 0
    for expected, event in enumerate(events, start=1):
        if type(event.seq) is not int or event.seq != expected:
            raise ValueError(f"expected seq {expected}, got {event.seq!r}")
        through_seq = expected
        match event.type:
            case "usage.tick":
                tick = event.payload
                if tick.cost_usd is not None:
                    if not isfinite(tick.cost_usd) or tick.cost_usd < 0:
                        raise ValueError(f"invalid individual cost at seq {event.seq}")
                    costs.append(tick.cost_usd)
                else:
                    unknown += 1
                calls += 1
                if tick.change_id is None:
                    unassigned.append(
                        UnassignedUsage(
                            event.seq,
                            tick.agent_id,
                            tick.role,
                            tick.model,
                            tick.plan_id,
                            tick.cost_usd,
                        )
                    )
                    continue
                change_id = tick.change_id
                rounds.setdefault(change_id, 0)
                buckets = groups.setdefault(change_id, defaultdict(list))
                buckets[tick.role, tick.model, rounds[change_id]].append(tick.cost_usd)
            case "change.resolved":
                resolution = event.payload
                change_id = resolution.change_id
                rounds.setdefault(change_id, 0)
                if resolution.decision == "changes_requested":
                    rounds[change_id] += 1
                if resolution.decision == "approved" and resolution.merge == "merged":
                    accepted.add(change_id)
            case "change.proposed" | "change.updated" | "agent.status" | "error":
                if event.payload.change_id is not None:
                    rounds.setdefault(event.payload.change_id, 0)
            case (
                "run.started"
                | "graph.snapshot"
                | "graph.delta"
                | "model.attempt_failed"
                | "agent.step"
                | "agent.focus"
                | "agent.message"
                | "change.assessed"
                | "merge_mode.changed"
                | "plan.updated"
                | "change.reviewed"
                | "change.facts_recorded"
                | "tests.executed"
                | "revert.resolved"
                | "queue.updated"
                | "lead.status"
                | "lead.reply"
                | "grant.created"
                | "grant.revoked"
            ):
                pass  # These events consume seqs but provide no accounting observations.
            case _:
                assert_never(event)
    return UsageFold(
        through_seq=through_seq,
        by_change={
            change_id: ChangeAccounting(_change_usage(groups.get(change_id, {})), count)
            for change_id, count in rounds.items()
        },
        unassigned=tuple(unassigned),
        run=_run_usage(costs, unknown, calls, len(accepted)),
    )
