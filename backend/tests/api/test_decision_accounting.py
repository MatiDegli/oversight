"""Runtime decision admission and durable Baseten receipts, without provider traffic."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from decision_client_helpers import INPUT, FakeApi, body, client, ok, status
from live_helpers import live_settings

from oversight import runtime_decisions
from oversight.agents import LedgerError, LedgerFile, LedgerState, UnknownUsage
from oversight.app import LiveRun
from oversight.events import read_store
from oversight_contracts.events import UsageTick

pytestmark = pytest.mark.anyio


@asynccontextmanager
async def runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    api: FakeApi,
    **overrides: Any,
) -> AsyncIterator[LiveRun]:
    settings = live_settings(
        tmp_path,
        **(
            {
                "baseten_api_key": "test",
                "baseten_budget_usd": 10.0,
                "baseten_input_usd_per_m": 1.0,
                "baseten_output_usd_per_m": 2.0,
            }
            | overrides
        ),
    )
    monkeypatch.setattr(runtime_decisions, "configured_decision", lambda _: client(api))
    live = LiveRun(settings)
    # Only the run store and ledgers are needed; no workspace or author is started.
    await live.ledger.load()
    await live.store.start()
    try:
        yield live
    finally:
        await live.difference_decisions.close()
        await live.store.close()


async def decide(live: LiveRun, *, deadline: float | None = None) -> Any:
    return await live.difference_decisions.classify_difference(
        INPUT,
        deadline=deadline if deadline is not None else asyncio.get_running_loop().time() + 30,
    )


def ticks(live: LiveRun) -> list[UsageTick]:
    return [
        UsageTick.model_validate(json.loads(e.envelope)["payload"])
        for e in read_store(live.store.path)
        if e.type == "usage.tick"
    ]


async def test_known_cost_is_persistent_attributed_and_separate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = FakeApi(ok(body()))
    async with runtime(tmp_path, monkeypatch, api) as live:
        outcome = await decide(live)
        assert outcome.choice == "asked_for"
        [tick] = ticks(live)
        assert tick.role == "review" and tick.agent_id == INPUT.agent_id
        assert (
            tick.change_id == INPUT.target.change_id
            and tick.model == "meta-models/muse-spark-open-1026"
        )
        assert tick.cost_usd == pytest.approx(0.00008)  # completion includes reasoning already
        assert tick.spent_usd == pytest.approx(0.00008) and tick.budget_usd == 10
        assert live.ledger.state == LedgerState() and live._usage.spent_usd == 0
        bank = live.difference_decisions.accounting
        assert bank.ledger.read().spent_usd == pytest.approx(0.00008)


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"baseten_budget_usd": None}, "budget_unconfigured"),
        ({"baseten_budget_usd": float("nan")}, "budget_unconfigured"),
        ({"baseten_input_usd_per_m": None}, "pricing_unconfigured"),
        ({"baseten_output_usd_per_m": None}, "pricing_unconfigured"),
    ],
)
async def test_missing_budget_or_prices_blocks_without_send_or_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, Any],
    reason: str,
) -> None:
    api = FakeApi(ok(body()))
    async with runtime(tmp_path, monkeypatch, api, **overrides) as live:
        outcome = await decide(live)
        assert outcome.unavailable == reason and not api.bodies and not ticks(live)


@pytest.mark.parametrize("unknown", [True, False])
async def test_existing_unknown_or_failed_ledger_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unknown: bool,
) -> None:
    api = FakeApi(ok(body()))
    async with runtime(tmp_path, monkeypatch, api) as live:
        ledger = live.difference_decisions.accounting.ledger
        ledger.path.parent.mkdir(parents=True, exist_ok=True)
        if unknown:
            ledger.write(LedgerState(0, (UnknownUsage("old", "agent-old", "model", "ts"),)))
        else:
            ledger.path.write_text("corrupt")
        outcome = await decide(live)
        assert outcome.unavailable == ("ledger_unknown" if unknown else "ledger_unreliable")
        assert not api.bodies and not ticks(live)


@pytest.mark.parametrize("usage", [None, {"prompt_tokens": 40}, {"completion_tokens": 20}])
async def test_partial_usage_never_becomes_zero_and_blocks_the_next_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    usage: dict[str, int] | None,
) -> None:
    api = FakeApi(ok(body(usage=usage)), ok(body()))
    async with runtime(tmp_path, monkeypatch, api) as live:
        assert (await decide(live)).choice == "asked_for"
        assert (await decide(live)).unavailable == "ledger_unknown"
        [tick] = ticks(live)
        assert tick.cost_usd is None
        assert tick.input_tokens == (usage or {}).get("prompt_tokens")
        assert tick.output_tokens == (usage or {}).get("completion_tokens")
        assert len(api.bodies) == 1
        assert len(live.difference_decisions.accounting.ledger.read().unknown) == 1


async def test_http_error_is_recorded_and_unknown_usage_prevents_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = FakeApi(status(429), ok(body()))
    model = client(api)

    async def sleep(delay: float) -> None:
        assert len(ticks(live)) == 1

    model._sleep = sleep
    async with runtime(tmp_path, monkeypatch, api) as live:
        monkeypatch.setattr(runtime_decisions, "configured_decision", lambda _: model)
        assert (await decide(live)).unavailable == "ledger_unknown"
        assert len(ticks(live)) == len(api.bodies) == 1


async def test_spend_brake_uses_only_baseten_balance_and_persists_across_instances(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = FakeApi(ok(body()), ok(body()))
    async with runtime(tmp_path, monkeypatch, api, baseten_budget_usd=0.00008) as live:
        assert (await decide(live)).choice == "asked_for"
        assert (await decide(live)).unavailable == "budget_blocked"
        path = live.difference_decisions.accounting.ledger.path
        assert LedgerFile(path).read().spent_usd == pytest.approx(0.00008)
        assert live._usage.spent_usd == 0
    async with runtime(tmp_path, monkeypatch, api, baseten_budget_usd=0.00008) as restarted:
        assert (await decide(restarted)).unavailable == "budget_blocked"
        assert not ticks(restarted) and len(api.bodies) == 1


async def test_receipt_survives_repeated_cancellation_and_keeps_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    api = FakeApi(ok(body()))
    async with runtime(tmp_path, monkeypatch, api) as live:
        ledger = live.difference_decisions.accounting.ledger
        original = ledger.record

        async def held(*args: Any) -> Any:
            entered.set()
            await release.wait()
            return await original(*args)

        monkeypatch.setattr(ledger, "record", held)
        request = asyncio.create_task(decide(live))
        await entered.wait()
        request.cancel()
        await asyncio.sleep(0)
        request.cancel()
        await asyncio.sleep(0)
        assert not request.done() and not ticks(live)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await request
        [tick] = ticks(live)
        assert tick.change_id == INPUT.target.change_id and tick.cost_usd == pytest.approx(0.00008)
        assert ledger.read().spent_usd == pytest.approx(0.00008)


@pytest.mark.parametrize("failure", [LedgerError, OSError])
async def test_failed_write_blocks_further_calls_without_poisoning_meta(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: type[Exception],
) -> None:
    api = FakeApi(ok(body()), ok(body()))
    async with runtime(tmp_path, monkeypatch, api) as live:

        def fail(state: LedgerState) -> None:
            raise failure("injected")

        monkeypatch.setattr(live.difference_decisions.accounting.ledger, "write", fail)
        assert (await decide(live)).unavailable == "ledger_unreliable"
        assert (await decide(live)).unavailable == "ledger_unreliable"
        assert len(api.bodies) == 1 and not ticks(live)
        assert not live._ledger_lost and live.ledger.state == LedgerState()


async def test_serialized_candidates_cannot_both_admit_against_the_old_balance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = FakeApi(ok(body()), ok(body()))
    async with runtime(tmp_path, monkeypatch, api, baseten_budget_usd=0.00008) as live:
        outcomes = await asyncio.gather(decide(live), decide(live))
        assert [o.choice for o in outcomes] == ["asked_for", None]
        assert outcomes[1].unavailable == "budget_blocked"
        assert len(api.bodies) == len(ticks(live)) == 1


async def test_reconciled_balance_is_rechecked_after_backoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = FakeApi(status(503), ok(body()))
    model = client(api)

    async def sleep(delay: float) -> None:
        ledger = live.difference_decisions.accounting.ledger
        ledger.write(LedgerState(9.5))  # simulated reconciliation while the retry is waiting
        await ledger.load()

    model._sleep = sleep
    async with runtime(tmp_path, monkeypatch, api) as live:
        monkeypatch.setattr(runtime_decisions, "configured_decision", lambda _: model)
        assert (await decide(live)).unavailable == "budget_blocked"
        assert len(api.bodies) == len(ticks(live)) == 1


@pytest.mark.parametrize("ending", ["cancel", "deadline", "shutdown"])
async def test_inflight_attempt_survives_every_runtime_ending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ending: str,
) -> None:
    entered = asyncio.Event()
    api = FakeApi()
    model = client(api)

    async def create(**params: Any) -> Any:
        entered.set()
        await asyncio.Event().wait()

    model._create = create
    async with runtime(tmp_path, monkeypatch, api) as live:
        monkeypatch.setattr(runtime_decisions, "configured_decision", lambda _: model)
        request = asyncio.create_task(
            decide(
                live,
                deadline=asyncio.get_running_loop().time() + (0.1 if ending == "deadline" else 30),
            )
        )
        await entered.wait()
        if ending == "cancel":
            request.cancel()
        elif ending == "shutdown":
            await live.difference_decisions.close()
        if ending == "deadline":
            assert (await request).unavailable == "deadline"
        else:
            with pytest.raises(asyncio.CancelledError):
                await request
        [tick] = ticks(live)
        assert tick.input_tokens is None and tick.output_tokens is None and tick.cost_usd is None
        assert len(live.difference_decisions.accounting.ledger.read().unknown) == 1


async def test_ledger_write_failure_stays_closed_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = FakeApi(ok(body()), ok(body()))
    async with runtime(tmp_path, monkeypatch, api) as live:

        def fail(state: LedgerState) -> None:
            raise OSError("injected")

        monkeypatch.setattr(live.difference_decisions.accounting.ledger, "write", fail)
        assert (await decide(live)).unavailable == "ledger_unreliable"
    async with runtime(tmp_path, monkeypatch, api) as restarted:
        assert (await decide(restarted)).unavailable == "ledger_unreliable"
        assert len(api.bodies) == 1


async def test_candidates_keep_distinct_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = FakeApi(ok(body()), ok(body()))
    second = replace(
        INPUT,
        agent_id="agent-2",
        target=INPUT.target.model_copy(
            update={"change_id": "change-2", "revision": 2},
        ),
    )
    async with runtime(tmp_path, monkeypatch, api) as live:
        first_outcome, second_outcome = await asyncio.gather(
            decide(live),
            live.difference_decisions.classify_difference(
                second,
                deadline=asyncio.get_running_loop().time() + 30,
            ),
        )
        assert first_outcome.target != second_outcome.target
        assert [(t.agent_id, t.change_id) for t in ticks(live)] == [
            ("agent-1", "change-1"),
            ("agent-2", "change-2"),
        ]
        assert live.difference_decisions.accounting.ledger.read().spent_usd == pytest.approx(
            0.00016
        )
