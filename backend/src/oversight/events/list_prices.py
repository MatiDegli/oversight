"""List-price equivalents from a stored, dated extract of the provider's pricing page.

These equivalents are not a bill and are separate from the budget ledger.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from math import fsum
from typing import Literal

from oversight_contracts.events import ServerEvent, UsageTick

type Coverage = Literal["complete", "missing_metrics", "incomplete"]
type _Role = Literal["agent", "review", "plan", "lead"]
type _GroupKey = tuple[str, _Role | None, str | None]


@dataclass(frozen=True)
class ListPrice:
    model: str
    input_usd_per_mtok: float
    cached_input_usd_per_mtok: float
    output_usd_per_mtok: float


@dataclass(frozen=True)
class PriceTable:
    source_url: str
    retrieved: str
    prices: tuple[ListPrice, ...]

    def price(self, model: str | None) -> ListPrice | None:
        """Look up only the exact model id reported for a call."""
        return next((price for price in self.prices if price.model == model), None)


LIST_PRICES = PriceTable(
    source_url="https://dev.meta.ai/docs/pricing-rate-limits",
    retrieved="2026-10-30",
    prices=(ListPrice("muse-spark-1.3-contributor", 0.10, 0.002, 0.20),),
)


@dataclass(frozen=True)
class ListPriceGroup:
    agent_id: str
    role: _Role | None
    model: str | None
    priced: bool
    calls: int
    known_usd: float
    usd: float | None


@dataclass(frozen=True)
class ListPriceFold:
    through_seq: int
    source_url: str
    retrieved: str
    calls: int
    priced_calls: int
    unpriced_calls: int
    ranged_calls: int
    known_usd: float
    usd: float | None
    accepted_changes: int
    usd_per_accepted: float | None
    known_usd_per_accepted: float | None
    coverage: Coverage
    problems: tuple[str, ...]
    groups: tuple[ListPriceGroup, ...]


@dataclass(frozen=True)
class _Call:
    known: float
    kind: Literal["exact", "unpriced", "ranged", "inconsistent"]


def _price_call(tick: UsageTick, price: ListPrice | None) -> _Call:
    """Price one receipt's tokens, preserving unknown counts as a lower bound."""
    i, c, o = tick.input_tokens, tick.cached_tokens, tick.output_tokens
    if any(count is not None and count < 0 for count in (i, c, o)) or (
        i is not None and c is not None and c > i
    ):
        return _Call(0.0, "inconsistent")
    if i == 0 and o == 0 and c in (0, None):
        return _Call(0.0, "exact")
    if price is None:
        return _Call(0.0, "unpriced")
    if i is not None and c is not None:
        input_part = (i - c) * price.input_usd_per_mtok + c * price.cached_input_usd_per_mtok
    elif i is not None:
        input_part = i * price.cached_input_usd_per_mtok
    else:
        input_part = (c or 0) * price.cached_input_usd_per_mtok
    output_part = (o or 0) * price.output_usd_per_mtok
    known = (input_part + output_part) / 1_000_000
    return _Call(known, "exact" if i is not None and c is not None and o is not None else "ranged")


def _group_rows(
    groups: dict[_GroupKey, list[_Call]], table: PriceTable
) -> tuple[ListPriceGroup, ...]:
    rows: list[ListPriceGroup] = []
    for (agent_id, role, model), calls in groups.items():
        known = fsum(call.known for call in calls)
        rows.append(
            ListPriceGroup(
                agent_id=agent_id,
                role=role,
                model=model,
                priced=table.price(model) is not None,
                calls=len(calls),
                known_usd=known,
                usd=known if all(call.kind == "exact" for call in calls) else None,
            )
        )
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                not row.priced,
                row.agent_id,
                row.role is None,
                row.role or "",
                row.model is None,
                row.model or "",
            ),
        )
    )


def _totals(
    through_seq: int,
    table: PriceTable,
    calls: list[_Call],
    priced: int,
    accepted: int,
    problems: list[str],
    groups: dict[_GroupKey, list[_Call]],
) -> ListPriceFold:
    unpriced = sum(call.kind == "unpriced" for call in calls)
    ranged = sum(call.kind == "ranged" for call in calls)
    coverage: Coverage = "incomplete" if problems else "complete"
    if unpriced:
        problems.append(f"{unpriced} calls have no list price")
    if ranged:
        problems.append(f"{ranged} priced calls have unreported token counts")
    if coverage != "incomplete" and (unpriced or ranged):
        coverage = "missing_metrics"
    known = fsum(call.known for call in calls)
    usd = known if coverage == "complete" else None
    return ListPriceFold(
        through_seq=through_seq,
        source_url=table.source_url,
        retrieved=table.retrieved,
        calls=len(calls),
        priced_calls=priced,
        unpriced_calls=unpriced,
        ranged_calls=ranged,
        known_usd=known,
        usd=usd,
        accepted_changes=accepted,
        usd_per_accepted=usd / accepted if usd is not None and accepted else None,
        known_usd_per_accepted=known / accepted if accepted else None,
        coverage=coverage,
        problems=tuple(problems),
        groups=_group_rows(groups, table),
    )


def price_usage(events: Sequence[ServerEvent], table: PriceTable = LIST_PRICES) -> ListPriceFold:
    """Fold a committed prefix starting at seq 1; reject any sequence gap or duplicate.

    Every tick contributes regardless of attribution or change outcome. Other event types
    consume their seq without supplying pricing observations, except merged approvals.
    """
    groups: dict[_GroupKey, list[_Call]] = defaultdict(list)
    calls: list[_Call] = []
    problems: list[str] = []
    accepted: set[str] = set()
    priced = 0
    through_seq = 0
    for expected, event in enumerate(events, start=1):
        if type(event.seq) is not int or event.seq != expected:
            raise ValueError(f"expected seq {expected}, got {event.seq!r}")
        through_seq = expected
        if event.type == "usage.tick":
            tick = event.payload
            price = table.price(tick.model)
            call = _price_call(tick, price)
            calls.append(call)
            groups[tick.agent_id, tick.role, tick.model].append(call)
            priced += price is not None
            if call.kind == "inconsistent":
                problems.append(f"usage at seq {event.seq} is inconsistent")
        elif event.type == "change.resolved":
            resolution = event.payload
            if resolution.decision == "approved" and resolution.merge == "merged":
                accepted.add(resolution.change_id)
    return _totals(through_seq, table, calls, priced, len(accepted), problems, groups)
