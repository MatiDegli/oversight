"""Dated list prices use each receipt's tokens and preserve coverage gaps."""

from pathlib import Path
from typing import Literal

import pytest

from oversight.events import LIST_PRICES, ListPrice, PriceTable, fold_usage, price_usage, read_jsonl
from oversight.events import list_prices as prices
from oversight_contracts.events import (
    ChangeResolved,
    ChangeResolvedEvent,
    Conflict,
    ServerEvent,
    UsageTick,
    UsageTickEvent,
)

type Role = Literal["agent", "review", "plan", "lead"]
MODEL = "muse-spark-1.3-contributor"
OPEN = "meta-models/muse-spark-open-1026"
TS = "2026-10-31T12:00:00.000Z"
DEMO = Path(__file__).resolve().parents[3] / "fixtures/events/recorded-demo.jsonl"


def tick(
    seq: int,
    i: int | None = 1_000_000,
    c: int | None = 400_000,
    o: int | None = 1_000_000,
    *,
    model: str | None = MODEL,
    agent: str = "agent-1",
    role: Role | None = None,
) -> UsageTickEvent:
    return UsageTickEvent(
        seq=seq,
        ts=TS,
        type="usage.tick",
        payload=UsageTick(
            agent_id=agent,
            model=model,
            input_tokens=i,
            cached_tokens=c,
            output_tokens=o,
            cost_usd=987.0,
            spent_usd=999.0,
            budget_usd=1000.0,
            brake="blocked",
            role=role,
        ),
    )


def resolved(
    seq: int,
    change: str = "change-1",
    decision: Literal["approved", "changes_requested", "rejected"] = "approved",
    merge: Literal["merged", "conflict"] | None = "merged",
) -> ChangeResolvedEvent:
    return ChangeResolvedEvent(
        seq=seq,
        ts=TS,
        type="change.resolved",
        payload=ChangeResolved(
            change_id=change,
            agent_id="agent-1",
            revision=1,
            decision=decision,
            decided_by="person",
            reason=None,
            comment=None,
            merge=merge,
            merge_commit="head" if merge == "merged" else None,
            conflict=Conflict(kind="merge", paths=["app.py"], tests=None)
            if merge == "conflict"
            else None,
        ),
    )


def test_price_table_is_dated_and_minimal() -> None:
    assert LIST_PRICES.source_url == "https://dev.meta.ai/docs/pricing-rate-limits"
    assert LIST_PRICES.retrieved == "2026-10-30"
    assert LIST_PRICES.prices == (ListPrice(MODEL, 0.10, 0.002, 0.20),)
    assert LIST_PRICES.price(MODEL) == LIST_PRICES.prices[0]
    assert LIST_PRICES.price(OPEN) is None
    assert LIST_PRICES.price(None) is None
    assert LIST_PRICES.price(MODEL.upper()) is None
    assert LIST_PRICES.price("muse-spark") is None


def test_recorded_demo_list_prices() -> None:
    fold = price_usage(read_jsonl(DEMO))
    assert fold.through_seq == 69
    assert (fold.calls, fold.priced_calls, fold.unpriced_calls, fold.ranged_calls) == (17, 7, 10, 0)
    assert fold.known_usd == pytest.approx(0.000977554, abs=1e-12)
    assert fold.usd is None
    assert fold.coverage == "missing_metrics"
    assert fold.accepted_changes == 2
    assert fold.known_usd_per_accepted == pytest.approx(0.000488777, abs=1e-12)
    assert fold.usd_per_accepted is None
    assert fold.problems == ("10 calls have no list price",)
    assert len(fold.groups) == 2
    first, last = fold.groups
    assert (first.agent_id, first.role, first.model, first.priced, first.calls) == (
        "agent-1",
        None,
        MODEL,
        True,
        7,
    )
    assert first.usd == first.known_usd == fold.known_usd
    assert (last.agent_id, last.role, last.model, last.priced, last.calls) == (
        "agent-2",
        None,
        OPEN,
        False,
        10,
    )
    assert last.known_usd == 0.0
    assert last.usd is None
    assert (fold.source_url, fold.retrieved) == (LIST_PRICES.source_url, LIST_PRICES.retrieved)


def test_exact_call_prices_cached_input_at_the_cached_rate() -> None:
    fold = price_usage([tick(1)])
    assert fold.usd == pytest.approx(0.2608)
    assert fold.known_usd == fold.usd
    assert fold.groups[0].usd == fold.usd
    assert fold.problems == ()


@pytest.mark.parametrize(
    "i,c,o,known",
    [
        (1_000_000, None, 0, 0.002),
        (None, 400_000, 1_000_000, 0.2008),
        (None, None, 1_000_000, 0.20),
        (1_000_000, 400_000, None, 0.0608),
        (None, 400_000, None, 0.0008),
        (None, None, None, 0.0),
        (0, 0, None, 0.0),
    ],
)
def test_unreported_cached_is_a_range_whose_low_end_is_known(
    i: int | None, c: int | None, o: int | None, known: float
) -> None:
    fold = price_usage([tick(1, i, c, o)])
    assert fold.known_usd == pytest.approx(known)
    assert (fold.priced_calls, fold.ranged_calls, fold.unpriced_calls) == (1, 1, 0)
    assert fold.coverage == "missing_metrics"
    assert fold.usd is None
    assert fold.groups[0].usd is None
    assert fold.problems == ("1 priced calls have unreported token counts",)


@pytest.mark.parametrize("c", [0, None])
@pytest.mark.parametrize("model", [MODEL, OPEN, None])
def test_zero_usage_costs_zero_even_unpriced(c: int | None, model: str | None) -> None:
    fold = price_usage([tick(1, 0, c, 0, model=model)])
    assert fold.coverage == "complete"
    assert fold.usd == fold.known_usd == 0.0
    assert fold.unpriced_calls == fold.ranged_calls == 0
    assert fold.priced_calls == int(model == MODEL)
    assert fold.groups[0].usd == 0.0
    assert fold.groups[0].priced == (model == MODEL)


@pytest.mark.parametrize("model", [OPEN, None, MODEL.upper()])
def test_nonzero_or_unknown_usage_without_a_price_is_unknown(model: str | None) -> None:
    fold = price_usage([tick(1, model=model), tick(2, None, None, None, model=model)])
    assert fold.coverage == "missing_metrics"
    assert fold.usd is None
    assert fold.known_usd == 0.0
    assert (fold.calls, fold.priced_calls, fold.unpriced_calls, fold.ranged_calls) == (2, 0, 2, 0)
    assert fold.problems == ("2 calls have no list price",)


@pytest.mark.parametrize("i,c,o", [(1, 2, 0), (-1, None, 0), (0, -1, 0), (0, 0, -1)])
@pytest.mark.parametrize("model", [MODEL, OPEN])
def test_inconsistent_counts_are_incomplete(
    i: int | None, c: int | None, o: int | None, model: str
) -> None:
    fold = price_usage([tick(1), tick(2, i, c, o, model=model)])
    assert fold.known_usd == pytest.approx(0.2608)
    assert fold.usd is None
    assert fold.coverage == "incomplete"
    assert fold.problems == ("usage at seq 2 is inconsistent",)
    assert fold.priced_calls == 1 + int(model == MODEL)
    assert fold.unpriced_calls == fold.ranged_calls == 0
    assert fold.groups[-1].usd is None


def test_incomplete_precedes_missing_metrics_and_problems_keep_seq_order() -> None:
    fold = price_usage([tick(1, 1, 2, 0), tick(2, model=OPEN), tick(3, -1, 0, 0), tick(4, c=None)])
    assert fold.coverage == "incomplete"
    assert fold.known_usd == pytest.approx(0.202)
    assert fold.problems == (
        "usage at seq 1 is inconsistent",
        "usage at seq 3 is inconsistent",
        "1 calls have no list price",
        "1 priced calls have unreported token counts",
    )


def test_ledger_fields_are_never_read() -> None:
    events = read_jsonl(DEMO)
    altered: list[ServerEvent] = []
    for event in events:
        if event.type == "usage.tick":
            payload = event.payload.model_copy(
                update={"cost_usd": None, "spent_usd": -999.0, "budget_usd": 0.0, "brake": "ok"}
            )
            altered.append(event.model_copy(update={"payload": payload}))
        else:
            altered.append(event)
    assert price_usage(events) == price_usage(altered)


def test_accepted_changes_follow_usage_fold() -> None:
    events: list[ServerEvent] = [
        tick(1),
        resolved(2),
        resolved(3),
        resolved(4, "change-2", merge="conflict"),
        resolved(5, "change-3", decision="changes_requested", merge=None),
        resolved(6, "change-4", decision="rejected", merge=None),
        resolved(7, "change-5"),
    ]
    fold = price_usage(events)
    assert fold.accepted_changes == fold_usage(events).run.accepted_changes == 2
    assert fold.usd_per_accepted == fold.known_usd_per_accepted == fold.known_usd / 2
    no_accepted = price_usage([tick(1)])
    assert no_accepted.accepted_changes == 0
    assert no_accepted.usd_per_accepted is None
    assert no_accepted.known_usd_per_accepted is None


def test_complete_when_every_call_is_priced() -> None:
    fold = price_usage([tick(1, role="review"), tick(2), resolved(3)])
    assert fold.coverage == "complete"
    assert fold.calls == fold.priced_calls == 2
    assert fold.usd == fold.known_usd == pytest.approx(0.5216)
    assert fold.usd is not None
    assert fold.usd_per_accepted == fold.usd / fold.accepted_changes
    assert sum(group.calls for group in fold.groups) == fold.calls


def test_groups_sort_unpriced_last() -> None:
    table = PriceTable(
        "stored source",
        "2026-10-30",
        (ListPrice(MODEL, 1.0, 0.02, 2.0),),
    )
    events = [
        tick(1, agent="agent-9", role=None),
        tick(2, agent="agent-9", role="review"),
        tick(3, agent="agent-9", role="agent"),
        tick(4, agent="agent-1", model=None),
        tick(5, agent="agent-1", model=OPEN),
        tick(6, agent="agent-1", model="another"),
        tick(7, agent="agent-1", role="lead"),
        tick(8, agent="agent-1", role="plan"),
        tick(9, agent="agent-1"),
    ]
    fold = price_usage(events, table)
    assert [(g.priced, g.agent_id, g.role, g.model) for g in fold.groups] == [
        (True, "agent-1", "lead", MODEL),
        (True, "agent-1", "plan", MODEL),
        (True, "agent-1", None, MODEL),
        (True, "agent-9", "agent", MODEL),
        (True, "agent-9", "review", MODEL),
        (True, "agent-9", None, MODEL),
        (False, "agent-1", None, "another"),
        (False, "agent-1", None, OPEN),
        (False, "agent-1", None, None),
    ]
    assert fold.source_url == table.source_url
    assert fold.retrieved == table.retrieved
    assert fold.known_usd == pytest.approx(6 * 2.608)


@pytest.mark.parametrize("seqs", [(1, 3), (1, 1), (2,), (2, 1)])
def test_seqs_are_validated(seqs: tuple[int, ...]) -> None:
    with pytest.raises(ValueError, match="expected seq"):
        price_usage([tick(seq) for seq in seqs])


def test_empty_prefix_is_complete_without_an_accepted_change_ratio() -> None:
    fold = price_usage([])
    assert fold.through_seq == fold.calls == fold.priced_calls == 0
    assert fold.coverage == "complete"
    assert fold.known_usd == fold.usd == 0.0
    assert fold.usd_per_accepted is None
    assert fold.known_usd_per_accepted is None
    assert fold.groups == fold.problems == ()


def test_other_events_only_consume_their_sequence() -> None:
    started = read_jsonl(DEMO)[0]
    fold = price_usage([started, tick(2)])
    assert fold.through_seq == 2
    assert fold.calls == 1
    assert fold.usd == pytest.approx(0.2608)


@pytest.mark.parametrize(
    "kind,coverage",
    [
        ("exact", "complete"),
        ("unpriced", "missing_metrics"),
        ("ranged", "missing_metrics"),
        ("inconsistent", "incomplete"),
    ],
)
def test_coverage_uses_call_flags_independently_of_problem_texts(kind, coverage) -> None:
    for problems in ([], ["first", "second"], ["second", "first"]):
        fold = prices._totals(1, LIST_PRICES, [prices._Call(0.0, kind)], 0, 0, problems, {})
        assert fold.coverage == coverage
        assert fold.unpriced_calls == int(kind == "unpriced")
        assert fold.ranged_calls == int(kind == "ranged")
