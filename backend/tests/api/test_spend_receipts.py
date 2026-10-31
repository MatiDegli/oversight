"""Only committed Meta-total source sites feed the retained spend receipt and wakes."""

# Tests inspect run-owned accounting and scheduler state.
# pyright: reportPrivateUsage=false

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from backend.tests.changes.changes_helpers import EDIT, start_harness
from decision_client_helpers import INPUT
from live_helpers import Scripts, meta_settings, write_ledger

from oversight.agents import (
    MUSE_SPARK,
    MUSE_SPARK_OPEN,
    LedgerError,
    LedgerState,
    ModelReply,
    Pricing,
    ScriptedModel,
    Usage,
    UsageLedger,
    parse_script,
)
from oversight.app import LiveRun
from oversight.changes import ChangeManager, PolicyInputs, WrongState
from oversight.changes._mode import (
    RULES_ACTIVATION,
    ModeController,
    StartCapability,
    start_capability,
)
from oversight.events import StoredEvent, read_store
from oversight.runtime_accounting import (
    BasetenDecisionAccounting,
    PlanAccounting,
    ReviewAccounting,
)
from oversight_contracts.commands import SetMergeMode, StartAgent
from oversight_contracts.events import ErrorReport, UsageTick

pytestmark = pytest.mark.anyio


@pytest.fixture
async def live(tmp_path: Path) -> AsyncIterator[LiveRun]:
    run = LiveRun(
        meta_settings(
            tmp_path,
            budget_usd=1e20,
            agent_model_b="muse-spark-open",
            baseten_api_key="test",
            baseten_budget_usd=20,
            baseten_input_usd_per_m=1,
            baseten_output_usd_per_m=2,
        ),
        lambda profile, _slot: ScriptedModel(
            parse_script(
                json.dumps(
                    {
                        "task": "Finish",
                        "turns": [
                            {
                                "tool_calls": [
                                    {"name": "finish", "arguments": {"summary": "Done"}}
                                ],
                                "usage": {
                                    "input_tokens": 1,
                                    "output_tokens": 0,
                                    "cached_tokens": 0,
                                },
                            }
                        ],
                    }
                ),
                profile,
            ),
            profile,
        ),
    )
    await run.startup()
    try:
        yield run
    finally:
        await run.shutdown()


def manager(live: LiveRun) -> ChangeManager:
    assert live._manager is not None
    return live._manager


def tick(**updates: Any) -> UsageTick:
    return UsageTick.model_validate(
        dict(
            agent_id="agent-1",
            change_id="change-1",
            role="agent",
            model=MUSE_SPARK.model,
            cost_usd=1,
            input_tokens=1,
            output_tokens=0,
            cached_tokens=0,
            spent_usd=999,
            budget_usd=1e20,
            brake="ok",
        )
        | updates
    )


def reply() -> ModelReply:
    return ModelReply(None, [], usage=Usage(1, 0, 0), model=MUSE_SPARK.model)


async def test_seed_latest_validation_and_cache_independence(live: LiveRun) -> None:
    owner = manager(live)
    scheduler = owner._ctx.assessment
    seed = read_store(live.store.path)[0]
    assert scheduler.spend_receipt == seed and seed.type == "run.started"
    receipt = await live._publish(tick())
    assert scheduler.spend_receipt == receipt
    owner.spend_received(seed)
    owner.spend_received(receipt)
    assert scheduler.spend_receipt is receipt
    older = await live.store.publish(tick(spent_usd=2))
    newest = await live.store.publish(tick(spent_usd=3))
    owner.spend_received(newest)
    wake = Mock(wraps=scheduler.spend_changed)
    scheduler.spend_changed = wake
    owner.spend_received(older)
    owner.spend_received(newest)
    assert not wake.called and scheduler.spend_receipt is newest
    assert receipt.seq not in scheduler.events
    # The separate slot survives real cache eviction, and is the actual stored object.
    for _ in range(1025):
        event = await live.store.publish(
            ErrorReport(
                source="server", message="Historical", agent_id=None, change_id=None, fatal=False
            )
        )
        owner.rules_received(event)
    assert len(scheduler.events) == 1024 and scheduler.spend_receipt is newest


@pytest.mark.parametrize("unavailable", ["budget", "ledger"])
async def test_unavailable_seed_does_not_certify_zero(tmp_path: Path, unavailable: str) -> None:
    settings = meta_settings(tmp_path, budget_usd=None if unavailable == "budget" else 150)
    if unavailable == "ledger":
        write_ledger(settings, "not JSON")
    run = LiveRun(settings, Scripts())
    await run.startup()
    try:
        scheduler = manager(run)._ctx.assessment
        seed = read_store(run.store.path)[0]
        assert scheduler.table_receipt == seed
        assert scheduler.run_started_seq == seed.seq
        assert scheduler.run_started() is not None
        assert scheduler.spend_receipt is None
        assert scheduler.spend_receipt_total is None
        producer = manager(run)._ctx.ports.rules_evidence
        assert producer is not None
        inputs = PolicyInputs(
            change_id="change-1",
            agent_id="agent-1",
            revision=1,
            base_commit="base",
            head_commit="head",
            main_commit="main",
            mode="ask_me",
            mode_revision=0,
        )
        spend = producer(inputs).inputs.spend
        assert spend is not None
        if unavailable == "budget":
            assert spend.budget_usd is None
        else:
            assert spend.spent_usd is None and spend.unknown_costs is None and not spend.ledger_ok
    finally:
        await run.shutdown()


@pytest.mark.parametrize(
    "mutation",
    ["type", "seq", "ts", "json", "negative", "infinite", "budget", "run"],
)
async def test_invalid_receipts_are_inert(live: LiveRun, mutation: str) -> None:
    owner = manager(live)
    seed = owner._ctx.assessment.spend_receipt
    stored = await live.store.publish(tick())
    body = json.loads(stored.envelope)
    if mutation == "type":
        stored = replace(stored, type="error")
    elif mutation == "seq":
        body["seq"] += 1
    elif mutation == "ts":
        body["ts"] = "2026-10-30T12:00:00Z"
    elif mutation == "json":
        stored = replace(stored, envelope="{")
    elif mutation == "run":
        body = json.loads(read_store(live.store.path)[0].envelope)
        body["seq"], body["ts"] = stored.seq, stored.ts
        body["payload"]["run_id"] = "run-20261031-000000"
        stored = replace(stored, type="run.started")
    else:
        field = "budget_usd" if mutation == "budget" else "spent_usd"
        body["payload"][field] = {"negative": -1, "infinite": float("inf"), "budget": 0}[mutation]
    if mutation != "json":
        stored = replace(stored, envelope=json.dumps(body))
    owner.spend_received(stored)
    assert owner._ctx.assessment.spend_receipt is seed


@pytest.mark.parametrize("source", ["author", "plan", "review"])
@pytest.mark.parametrize("meta", [False, True])
async def test_source_sites_forward_committed_meta_totals(
    live: LiveRun, monkeypatch: pytest.MonkeyPatch, source: str, meta: bool
) -> None:
    owner = manager(live)
    forwarded: list[StoredEvent] = []
    receive = owner.spend_received

    def checked(receipt: StoredEvent) -> None:
        assert receipt in read_store(live.store.path)
        forwarded.append(receipt)
        receive(receipt)

    monkeypatch.setattr(owner, "spend_received", checked)
    # An independent accumulator cannot certify even a non-Meta display tick.
    live._usage.spent_usd = 88
    profile = MUSE_SPARK if meta else MUSE_SPARK_OPEN
    if source == "author":
        live._agents["agent-1"] = SimpleNamespace(metered=meta)  # type: ignore[assignment]
        await live._publish(tick())
    elif source == "plan":
        await PlanAccounting(live, "plan-1", profile, "agent-1").record(reply())
    else:
        await ReviewAccounting(live, INPUT.target, profile, "agent-1").record(reply())
    [receipt] = forwarded
    assert owner._ctx.assessment.spend_receipt is receipt
    payload = json.loads(receipt.envelope)["payload"]
    assert live.ledger.state is not None
    assert payload["spent_usd"] == live.ledger.state.spent_usd
    assert (live.ledger.state.spent_usd > 0) is meta
    assert not live.ledger.state.unknown
    if not meta:
        assert payload["cost_usd"] is not None or source != "author"
        assert live.ledger.state == LedgerState()


@pytest.mark.parametrize("source", ["author", "plan", "review"])
async def test_pending_publication_never_enters_slot(
    live: LiveRun, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    owner = manager(live)
    seed = owner._ctx.assessment.spend_receipt
    entered, release = asyncio.Event(), asyncio.Event()
    publish = live.store.publish

    async def held(payload: Any) -> StoredEvent:
        if isinstance(payload, UsageTick):
            entered.set()
            await release.wait()
        return await publish(payload)

    monkeypatch.setattr(live.store, "publish", held)
    work = {
        "author": lambda: live._publish(tick()),
        "plan": lambda: PlanAccounting(live, "plan-1", MUSE_SPARK, "agent-1").record(reply()),
        "review": lambda: ReviewAccounting(live, INPUT.target, MUSE_SPARK, "agent-1").record(
            reply()
        ),
    }
    pending = asyncio.create_task(work[source]())
    try:
        await entered.wait()
        assert owner._ctx.assessment.spend_receipt is seed
        assert not any(e.type == "usage.tick" for e in read_store(live.store.path))
    finally:
        release.set()
        await pending
    assert owner._ctx.assessment.spend_receipt is not seed


@pytest.mark.parametrize("source", ["author", "plan", "review"])
async def test_unavailable_mirror_never_enters_slot(live: LiveRun, source: str) -> None:
    owner = manager(live)
    seed = owner._ctx.assessment.spend_receipt
    live.ledger.state = None
    if source == "author":
        live._agents["agent-1"] = SimpleNamespace(metered=False)  # type: ignore[assignment]
        await live._publish(tick())
    elif source == "plan":
        await PlanAccounting(live, "plan-1", MUSE_SPARK_OPEN, "agent-1").record(reply())
    else:
        await ReviewAccounting(live, INPUT.target, MUSE_SPARK_OPEN, "agent-1").record(reply())
    assert owner._ctx.assessment.spend_receipt is seed


async def test_baseten_decision_receipt_never_enters_meta_slot(live: LiveRun) -> None:
    owner = manager(live)
    seed = owner._ctx.assessment.spend_receipt
    bank = BasetenDecisionAccounting(live)
    await bank.load()
    await bank.record(INPUT, reply())
    assert bank.ledger.state is not None and bank.ledger.state.spent_usd > 0
    assert live.ledger.state == LedgerState()
    assert owner._ctx.assessment.spend_receipt is seed


async def test_baseten_b_author_forwards_from_real_tool_loop(
    live: LiveRun, baseten_verified: None
) -> None:
    await live.start(StartAgent(slot="B", task="Finish"))
    await asyncio.gather(*live._runs.values())
    [receipt] = [e for e in read_store(live.store.path) if e.type == "usage.tick"]
    payload = json.loads(receipt.envelope)["payload"]
    assert payload["role"] == "agent" and payload["model"] == MUSE_SPARK_OPEN.model
    assert payload["spent_usd"] == 0 and payload["cost_usd"] > 0
    assert live.ledger.state == LedgerState()
    assert manager(live)._ctx.assessment.spend_receipt == receipt


async def test_concurrent_sources_publish_their_returned_durable_total(
    live: LiveRun, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    original = live.ledger.record
    installed: dict[str, LedgerState] = {}

    async def ordered(cost: Any, entry: Any) -> LedgerState:
        if entry.agent_id == "agent-1":
            entered.set()
            await release.wait()
        state = await original(cost, entry)
        installed[entry.agent_id] = state
        return state

    monkeypatch.setattr(live.ledger, "record", ordered)
    monkeypatch.setattr(live, "_price_for", lambda _: Pricing(1e6, 1e6, 1e6))
    live._usage = UsageLedger(1e20)
    author = asyncio.create_task(
        live._publish(tick(cost_usd=1e16, spent_usd=live._usage.record(1e16)))
    )
    try:
        await entered.wait()
        await asyncio.gather(
            PlanAccounting(live, "plan-1", MUSE_SPARK, "agent-2").record(reply()),
            ReviewAccounting(live, INPUT.target, MUSE_SPARK, "agent-3").record(reply()),
        )
    finally:
        release.set()
        await author
    ticks = [
        json.loads(e.envelope)["payload"]
        for e in read_store(live.store.path)
        if e.type == "usage.tick"
    ]
    assert len(ticks) == 3
    assert all(t["spent_usd"] == installed[t["agent_id"]].spent_usd for t in ticks)
    assert live._usage.spent_usd == 1e16
    assert live.ledger.state == LedgerState(1e16 + 2)


@pytest.mark.parametrize("source", ["author", "plan", "review"])
async def test_sticky_loss_notifies_before_reporting(
    live: LiveRun, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    seen = []
    live.ledger.watch(lambda: seen.append(live._ledger_lost))

    async def fail(*_args: Any) -> Any:
        raise LedgerError("injected before ledger I/O")

    monkeypatch.setattr(live.ledger, "record", fail)
    with pytest.raises(asyncio.CancelledError if source == "author" else Exception):
        if source == "author":
            await live._publish(tick())
        elif source == "plan":
            await PlanAccounting(live, "plan-1", MUSE_SPARK, "agent-1").record(reply())
        else:
            await ReviewAccounting(live, INPUT.target, MUSE_SPARK, "agent-1").record(reply())
    assert seen == [True] and live.ledger.change_revision == 2


async def test_notifications_wake_valid_waits_without_rewriting_authority(
    tmp_path: Path, live: LiveRun, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = await start_harness(tmp_path / "harness")
    try:
        await h.manager.aclose()
        h._manager = ChangeManager(
            h.workspace,
            h.publish,
            main_graph=h.main_graph,
            main_snapshot=h.manager.snapshot,
            spend_ledger=live.ledger,
        )
        owner = h.manager
        owner._ctx.mode = ModeController(availability=lambda: StartCapability(True, "Test only"))
        await owner.set_merge_mode(SetMergeMode(mode="merge_when_safe", revision=0))
        await h.run_agent(1, EDIT)
        await h.run_agent(2, EDIT)
        scheduler = owner._ctx.assessment
        wait = scheduler.waits["change-1"]
        waits = list(scheduler.waits.values())
        assert len(waits) == 2
        await asyncio.gather(*(w.task for w in waits if w.task is not None))
        old = (wait.deadline, wait.target, wait.latest, scheduler.generation)
        assert owner._ctx.ports.rules_evidence is None
        assert wait.latest is not None
        assert next(r for r in wait.latest.rules if r.rule == "known_spend").result == "unknown"
        calls = Mock(wraps=scheduler.wake)
        monkeypatch.setattr(scheduler, "wake", calls)
        evaluate, release = scheduler.evaluate, asyncio.Event()

        async def held(current: Any) -> None:
            await release.wait()
            await evaluate(current)

        monkeypatch.setattr(scheduler, "evaluate", held)
        for action in (
            live.ledger.load,
            lambda: live._publish(tick()),
            lambda: _loss(live),
            lambda: _receipt(live, owner),
        ):
            release.clear()
            await action()
            assert all(w.dirty for w in waits)
            assert (wait.deadline, wait.target, wait.latest, scheduler.generation) == old
            release.set()
            await asyncio.gather(*(w.task for w in waits if w.task is not None))
            old = (wait.deadline, wait.target, wait.latest, scheduler.generation)
        assert calls.call_count == 8
        assert next(r for r in wait.latest.rules if r.rule == "known_spend").result == "unknown"
        wait.rec.state = "deciding"
        wait.dirty = False
        live.ledger.notify_loss()
        assert not wait.dirty and wait.latest is old[2]
        await owner.aclose()
        calls.reset_mock()
        live.ledger.notify_loss()
        assert not calls.called
    finally:
        await h.close()


async def _loss(live: LiveRun) -> None:
    live.ledger.notify_loss()


async def _receipt(live: LiveRun, owner: ChangeManager) -> None:
    owner.spend_received(await live.store.publish(tick()))


async def test_f3a_does_not_open_activation(live: LiveRun, monkeypatch: pytest.MonkeyPatch) -> None:
    await live._publish(tick())
    capability = start_capability(
        live.workspace, replace(live._settings, cross_review=True, review_model="fake")
    )
    assert not RULES_ACTIVATION and not capability.confirmed
    assert "Rules mode is closed" in capability.reason
    monkeypatch.setattr(manager(live)._ctx.mode, "_availability", lambda: capability)
    assert manager(live)._ctx.ports.rules_evidence is not None
    with pytest.raises(WrongState):
        await live.set_merge_mode(SetMergeMode(mode="merge_when_safe", revision=0))
    assert live.merge_mode().mode == "ask_me"
