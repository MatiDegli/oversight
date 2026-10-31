"""Accounting installation, receipt lag and fixed deadlines at asynchronous boundaries."""

import asyncio
import threading
from contextlib import AsyncExitStack, asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from changes_helpers import queued_callbacks_ran
from test_assessment import latest, rec, settled, terminals, wait
from test_rules_transaction import configure_transaction
from test_spend_evidence import UNKNOWN, pending, reserve, spend_rule
from test_spend_evidence import spend as spend

from oversight.agents import LedgerState
from oversight.changes import _approval, _transaction
from oversight.changes._support import RulesFailure
from oversight_contracts.events import ChangeResolved
from oversight_contracts.policy import ChangeAssessment

pytestmark = pytest.mark.anyio


@asynccontextmanager
async def writing(spend, monkeypatch, cost: float | None = 0.1, *, cancel=False):
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    original = spend.ledger.write

    def held(state):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        original(state)

    with monkeypatch.context() as patch:
        patch.setattr(spend.ledger, "write", held)
        writer = asyncio.create_task(spend.ledger.record(cost, UNKNOWN))
        try:
            await entered.wait()
            if cancel:
                writer.cancel()
            yield writer
        finally:
            release.set()
            if cancel:
                with pytest.raises(asyncio.CancelledError):
                    await writer
            else:
                await writer


async def test_capture_without_state_retries_without_consuming_or_resetting_deadline(
    h, spend, monkeypatch
):
    h.hold = ChangeAssessment
    spend.ctx.ports.rules_transaction = AsyncMock(return_value="Test transaction declined")
    h.manager.run_ended("agent-1", None)
    await h.held.wait()
    deadline = wait(h).deadline
    async with writing(spend, monkeypatch):
        h.release.set()
        await queued_callbacks_ran()
        assert rec(h).state == "ready" and not rec(h).rules_consumed
        assert not terminals(h) and wait(h).active
        assert wait(h).deadline == deadline
    h.hold = None
    await settled(h)
    assert len(spend.decisions) == 1
    await spend.decisions[0]
    assert rec(h).rules_consumed == {1}
    assert spend.ctx.ports.rules_transaction.await_count == 1
    assert wait(h).deadline == deadline


@pytest.mark.parametrize("cancel", [False, True])
async def test_installed_without_forwarded_receipt_progresses_with_old_pin(
    h, spend, monkeypatch, cancel
):
    await pending(h, spend)
    async with writing(spend, monkeypatch, cancel=cancel):
        assert spend.ledger.state is None
    await settled(h)
    assert spend_rule(latest(h)).observed["spent_usd"] == 0.1
    assert spend_rule(latest(h)).evidence[0].event_seq == spend.seed.seq
    assert spend.ctx.assessment.spend_receipt == spend.seed


@pytest.mark.parametrize("boundary", [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11])
async def test_each_async_readiness_wait_retains_safe_reservation(h, spend, monkeypatch, boundary):
    from oversight.changes import _spend

    configure_transaction(h, monkeypatch)
    await pending(h, spend)
    real = _spend.ready
    seen, entered, release = 0, asyncio.Event(), asyncio.Event()

    async def held(ctx, **kwargs):
        nonlocal seen
        seen += 1
        if seen == boundary:
            async with writing(spend, monkeypatch, cancel=True):
                entered.set()
                await release.wait()
                task = asyncio.create_task(real(ctx, **kwargs))
                await queued_callbacks_ran()
                assert not task.done()
            await task
        else:
            await real(ctx, **kwargs)

    monkeypatch.setattr(_spend, "ready", held)
    h.manager._admit_rules(rec(h), latest(h))
    decision = spend.decisions[0]
    await entered.wait()
    assert rec(h).state == "deciding" and rec(h).rules_consumed == {1}
    release.set()
    await decision
    assert seen >= boundary and h.payloads(ChangeResolved) and not terminals(h)


@pytest.mark.parametrize("change", ["person", "mode", "target", "main", "loss"])
async def test_guards_rechecked_after_write_wait(h, spend, monkeypatch, change):
    from oversight.changes import _spend

    await pending(h, spend)
    reservation = reserve(h, spend, latest(h))
    real = _spend.ready
    entered = asyncio.Event()

    async def signalled(ctx, **kwargs):
        entered.set()
        await real(ctx, **kwargs)

    monkeypatch.setattr(_spend, "ready", signalled)
    port = spend.ctx.ports.rules_transaction = AsyncMock(return_value=None)
    async with writing(spend, monkeypatch):
        task = asyncio.create_task(_approval.approve_rules(spend.ctx, reservation))
        await entered.wait()
        if change == "person":
            rec(h).decision += 1
        elif change == "mode":
            spend.ctx.mode._suspended = True
        elif change == "target":
            rec(h).head_commit = "other"
        elif change == "main":
            spend.ctx.state.snapshot = spend.ctx.state.snapshot.model_copy(
                update={"commit": "other"}
            )
        else:
            spend.owner._ledger_lost = True
            spend.ledger.notify_loss()
    await task
    port.assert_not_awaited()
    assert not h.payloads(ChangeResolved)


async def test_repeated_wakes_do_not_reset_deadline_or_cancel_shared_writer(h, spend, monkeypatch):
    from oversight.changes import _spend

    spend.ctx.timeout = 0.04
    real = spend.ledger.wait_changed
    deadlines = []

    async def changed(after, *, deadline):
        deadlines.append(deadline)
        await real(after, deadline=deadline)

    monkeypatch.setattr(spend.ledger, "wait_changed", changed)
    async with writing(spend, monkeypatch) as writer:
        pending_wait = asyncio.create_task(_spend.ready(spend.ctx))
        for _ in range(3):
            await queued_callbacks_ran()
            spend.ledger._changed()  # A wake with another write still active.
        with pytest.raises(RulesFailure, match="timed out"):
            await pending_wait
        assert len(deadlines) >= 2 and len(set(deadlines)) == 1
        assert not writer.done() and spend.ledger.writing
    assert spend.ledger.state == LedgerState(0.1)


@pytest.mark.parametrize("bad", ["unknown", "threshold", "loss", "timeout"])
async def test_reserved_write_refusal_recovers_to_needs_you(h, spend, monkeypatch, bad):
    from oversight.changes import _spend

    configure_transaction(h, monkeypatch)
    await pending(h, spend)
    real = _transaction.Transaction.barriers
    spend.ctx.timeout = 0.03 if bad == "timeout" else 1

    async def barrier(tx):
        async with writing(spend, monkeypatch, cost=None if bad == "unknown" else 0.5):
            if bad == "loss":
                spend.owner._ledger_lost = True
                spend.ledger.notify_loss()
            if bad == "timeout":
                await real(tx)
                pytest.fail("active write exceeded deadline")
            waiter = asyncio.create_task(_spend.ready(spend.ctx))
            await queued_callbacks_ran()
        await waiter
        await real(tx)

    monkeypatch.setattr(_transaction.Transaction, "barriers", barrier)
    h.manager._admit_rules(rec(h), latest(h))
    await spend.decisions[0]
    assert await h.main_head() == h.base_commit
    assert not h.payloads(ChangeResolved) and terminals(h)


async def test_absent_without_writer_or_with_sticky_loss_never_waits(h, spend, monkeypatch):
    from oversight.changes import _spend

    changed = AsyncMock(side_effect=AssertionError("not an active healthy writer"))
    monkeypatch.setattr(spend.ledger, "wait_changed", changed)
    spend.ledger.state = None
    await _spend.ready(spend.ctx)
    spend.ledger.writing = True
    spend.owner._ledger_lost = True
    await _spend.ready(spend.ctx)
    changed.assert_not_awaited()


async def test_armed_write_wait_uses_original_deadline(h, spend, monkeypatch):
    from oversight.changes import _assessment

    loop = asyncio.get_running_loop()
    spend.ctx.assessment.monotonic = loop.time
    spend.ctx.assessment.sleep_until = lambda deadline: asyncio.sleep(
        max(0, deadline - loop.time())
    )
    monkeypatch.setattr(_assessment, "EVIDENCE_TIMEOUT_S", 0.03)
    async with writing(spend, monkeypatch) as writer:
        h.manager.run_ended("agent-1", None)
        deadline = wait(h).deadline
        await settled(h)
        assert wait(h).deadline == deadline and loop.time() >= deadline
        assert rec(h).state == "ready" and not rec(h).rules_consumed
        assert terminals(h) and not writer.done()
    await settled(h)
    assert len(terminals(h)) == 1


async def test_loss_wake_during_assessment_delivery_is_not_dropped(h, spend):
    h.hold = ChangeAssessment
    h.manager.run_ended("agent-1", None)
    await h.held.wait()
    spend.owner._ledger_lost = True
    spend.ledger.state = None
    spend.ledger.notify_loss()
    h.hold = None
    h.release.set()
    await settled(h)
    assert not rec(h).rules_consumed and not spend.decisions
    assert spend_rule(latest(h)).result != "pass"
    assert wait(h).active  # Unknown observation keeps the original expiry bound.


async def test_preflight_write_timeout_is_policy_invalid_and_releases_pins(h, spend, monkeypatch):
    await pending(h, spend)
    spend.ctx.timeout = 0.03
    port = spend.ctx.ports.rules_transaction = AsyncMock(return_value="Test decline")
    async with AsyncExitStack() as stack:
        async with h.workspace.locks(main=True):
            h.manager._admit_rules(rec(h), latest(h))
            writer = await stack.enter_async_context(writing(spend, monkeypatch))
        await spend.decisions[0]
        assert not writer.done() and rec(h).state == "ready"
        assert "code=policy_invalid;" in terminals(h)[0].message
        port.assert_not_awaited()
        assert wait(h).spend_pin is None
