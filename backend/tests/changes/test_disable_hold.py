"""Disable attempts own their holds through CAS, confirmation, timeout and shutdown."""

import asyncio
from collections.abc import AsyncIterator

import pytest
from changes_helpers import EDIT, Harness, queued_callbacks_ran
from test_assessment import make_clock, propose, settled, wait
from test_mode import available, select

from oversight.changes import WrongState
from oversight_contracts.events import ChangeProposed
from oversight_contracts.policy import MergeModeState

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
async def no_disable_leaks(h: Harness) -> AsyncIterator[None]:
    yield
    h.open_every_gate()
    await asyncio.gather(*h.manager._ctx.state.tasks, return_exceptions=True)
    assert not h.manager._ctx.mode._disables


@pytest.mark.parametrize("initial", ["ask_me", "merge_when_safe"])
@pytest.mark.parametrize("path", ["success", "stale", "same", "receipt_failure"])
async def test_disable_clear_table(h: Harness, initial: str, path: str) -> None:
    controller = available(h)
    if initial == "merge_when_safe":
        await select(h, initial, 0)
    before = controller.snapshot()
    if path == "receipt_failure":
        h.fail_once = MergeModeState
    revision = before.revision + int(path == "stale")
    if path == "same":
        # Same-mode is possible only when already Ask me.
        await select(h, "ask_me", before.revision)
        before = controller.snapshot()
        revision = before.revision
    events = list(h.log)
    if path == "stale" or (path == "receipt_failure" and before.mode != "ask_me"):
        with pytest.raises(WrongState):
            await select(h, "ask_me", revision)
        assert controller.snapshot() == before and h.log == events
    else:
        result = await select(h, "ask_me", revision)
        assert result.mode == "ask_me"
        assert result.revision == before.revision + int(before.mode != "ask_me")
        assert len(h.log) - len(events) == int(before.mode != "ask_me")
    assert not controller._disables
    assert controller.authority_suspended() is (
        path == "receipt_failure" and before.mode != "ask_me"
    )


async def test_timeout_attempt_later_changes_mode_without_a_retry(h: Harness) -> None:
    controller = available(h, timeout_s=0.02)
    await select(h, "merge_when_safe", 0)
    async with controller._lock:
        with pytest.raises(WrongState, match="timed out"):
            await select(h, "ask_me", 1)
        assert len(controller._disables) == 1 and controller.authority_suspended()
        assert controller.snapshot().mode == "merge_when_safe"
    await asyncio.gather(*h.manager._ctx.state.tasks)
    assert controller.snapshot() == MergeModeState(mode="ask_me", revision=2)
    assert not controller._disables and not controller.authority_suspended()


async def test_enable_confirmation_cannot_clear_a_later_disable(h, monkeypatch):
    controller = available(h)
    original = controller._confirm
    checked = []

    async def confirm(ctx, state):
        await original(ctx, state)
        if state.mode == "merge_when_safe":
            assert not controller._suspended
            assert len(controller._disables) == 1
            assert controller.authority_suspended()
            checked.append(True)

    monkeypatch.setattr(controller, "_confirm", confirm)
    h.gate = MergeModeState
    enabling = asyncio.create_task(select(h, "merge_when_safe", 0))
    await h.at_gate.wait()
    disabling = asyncio.create_task(select(h, "ask_me", 1))
    try:
        await queued_callbacks_ran()
        assert len(controller._disables) == 1
    finally:
        h.open_every_gate()
        await asyncio.gather(enabling, disabling)
    assert checked == [True]
    assert controller.snapshot() == MergeModeState(mode="ask_me", revision=2)
    assert not controller._disables and not controller.authority_suspended()


async def test_overlapping_disables_never_cross_clear(h: Harness) -> None:
    controller = available(h)
    await select(h, "merge_when_safe", 0)
    h.gate = MergeModeState
    first = asyncio.create_task(select(h, "ask_me", 1))
    await h.at_gate.wait()
    entered, release = asyncio.Event(), asyncio.Event()

    async def between_attempts():
        async with controller._lock:
            entered.set()
            await release.wait()

    blocker = asyncio.create_task(between_attempts())
    await queued_callbacks_ran()
    # Stale after the first disable, but it owns a separate hold until its CAS.
    second = asyncio.create_task(select(h, "ask_me", 1))
    try:
        await queued_callbacks_ran()
        assert len(controller._disables) == 2 and controller.authority_suspended()
        h.open_gate.set()
        assert await first == MergeModeState(mode="ask_me", revision=2)
        await entered.wait()
        assert not controller._suspended
        assert len(controller._disables) == 1 and controller.authority_suspended()
        release.set()
        with pytest.raises(WrongState, match="stale"):
            await second
    finally:
        release.set()
        h.open_every_gate()
        await asyncio.gather(first, blocker, second, return_exceptions=True)
    assert not controller._disables and not controller.authority_suspended()


async def test_shutdown_cancels_waiting_attempt_and_removes_hold(h: Harness) -> None:
    controller = available(h, timeout_s=0.02)
    await select(h, "merge_when_safe", 0)
    async with controller._lock:
        with pytest.raises(WrongState, match="timed out"):
            await select(h, "ask_me", 1)
        assert len(controller._disables) == 1
        h.manager._ctx.state.closing = True
        attempts = list(h.manager._ctx.state.tasks)
        for attempt in attempts:
            attempt.cancel()
        await asyncio.gather(*attempts, return_exceptions=True)
        assert not controller._disables and not controller.authority_suspended()


@pytest.mark.parametrize("stale", [False, True])
async def test_disable_ends_every_evidence_wait_before_lock_or_cas(h, monkeypatch, stale):
    make_clock(h, monkeypatch)
    controller = available(h, timeout_s=0.02)
    await select(h, "merge_when_safe", 0)
    await propose(h)
    await h.open(2)
    assert await h.edit(2, *EDIT)
    assert await h.manager.proposed("agent-2", "Ready")
    h.manager.run_ended("agent-1", None)
    h.manager.run_ended("agent-2", None)
    await settled(h)
    pending = wait(h)
    assert pending.active and pending.timer is not None
    async with controller._lock:
        with pytest.raises(WrongState, match="timed out"):
            await select(h, "ask_me", 0 if stale else 1)
        assert not pending.active and pending.timer.done()
        assert all(
            not w.active and w.timer.done() for w in h.manager._ctx.assessment.waits.values()
        )
        assert len(controller._disables) == 1 and controller.authority_suspended()
    results = await asyncio.gather(*h.manager._ctx.state.tasks, return_exceptions=True)
    if stale:
        assert any(isinstance(result, WrongState) for result in results)
    assert not pending.active  # A stale disable never rearms an ended wait.
    assert not controller._disables and not controller.authority_suspended()


async def test_shutdown_before_attempt_starts_removes_its_own_hold(h, monkeypatch):
    controller = available(h)
    spawn = h.manager._ctx.spawn

    def cancel_before_start(work):
        attempt = spawn(work)
        attempt.cancel()
        return attempt

    monkeypatch.setattr(h.manager._ctx, "spawn", cancel_before_start)
    with pytest.raises(asyncio.CancelledError):
        await select(h, "ask_me", 0)
    await queued_callbacks_ran()
    assert not controller._disables and not controller.authority_suspended()


async def test_cancel_before_attempt_starts_rechecks_wait_created_during_hold(h, monkeypatch):
    make_clock(h, monkeypatch)
    controller = available(h)
    await select(h, "merge_when_safe", 0)
    await propose(h)
    await settled(h)
    previous = wait(h)
    ctx = h.manager._ctx
    spawn = ctx.spawn

    def cancel_before_start(work):
        assert controller.authority_suspended() and not previous.active
        # Reproduce a proposal registered while the disable owns its hold, before
        # the attempt or its cancellation callback can run.
        ctx.assessment.proposed(previous.rec, previous.proposal, controller.snapshot(), armed=True)
        pending = wait(h)
        assert pending.active and pending.timer is None and pending.task is None
        attempt = spawn(work)
        attempt.cancel()
        return attempt

    with monkeypatch.context() as patch:
        patch.setattr(ctx, "spawn", cancel_before_start)
        with pytest.raises(asyncio.CancelledError):
            await select(h, "ask_me", 1)
    await queued_callbacks_ran()
    pending = wait(h)
    assert pending is not previous
    assert not controller._disables and not controller.authority_suspended()
    assert not pending.active
    assert pending.timer is None and pending.task is None


@pytest.mark.parametrize("waiting_on", ["lock", "receipt"])
async def test_cancelled_disable_caller_leaves_its_attempt_and_hold_alive(h, waiting_on):
    controller = available(h)
    await select(h, "merge_when_safe", 0)
    if waiting_on == "lock":
        await controller._lock.acquire()
    else:
        h.gate = MergeModeState
    caller = asyncio.create_task(select(h, "ask_me", 1))
    try:
        if waiting_on == "receipt":
            await h.at_gate.wait()
        else:
            await queued_callbacks_ran()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert len(controller._disables) == 1 and controller.authority_suspended()
        assert any(not task.done() for task in h.manager._ctx.state.tasks)
    finally:
        if waiting_on == "lock":
            controller._lock.release()
        h.open_every_gate()
        await asyncio.gather(*h.manager._ctx.state.tasks)
    assert controller.snapshot() == MergeModeState(mode="ask_me", revision=2)
    assert not controller._disables and not controller.authority_suspended()


async def test_proposal_published_during_stale_disable_stays_unarmed(h, monkeypatch):
    clock = make_clock(h, monkeypatch)
    controller = available(h, timeout_s=0.02)
    await select(h, "merge_when_safe", 0)
    await h.open(1)
    assert await h.edit(1, *EDIT)
    h.gate = ChangeProposed
    proposing = asyncio.create_task(h.manager.proposed("agent-1", "Ready"))
    try:
        await h.at_gate.wait()
        async with controller._lock:
            with pytest.raises(WrongState, match="timed out"):
                await select(h, "ask_me", 0)
            assert len(controller._disables) == 1
            h.open_gate.set()
            assert await proposing
            h.manager.run_ended("agent-1", None)
            pending = wait(h)
            assert pending.active and not h.manager._ctx.assessment.valid(pending)
            assert pending.timer is None and pending.task is None
        results = await asyncio.gather(*h.manager._ctx.state.tasks, return_exceptions=True)
        assert any(isinstance(result, WrongState) for result in results)
        assert controller.snapshot() == MergeModeState(mode="merge_when_safe", revision=1)
        assert not controller._disables and not controller.authority_suspended()
        assert not pending.active
        h.manager._ctx.assessment.wake(pending.rec)
        clock.advance(121)
        await settled(h)
        assert pending.timer is None and pending.task is None
        assert clock.calls.call_count == 0 and not clock.registrations
    finally:
        h.open_every_gate()
        await asyncio.gather(proposing, return_exceptions=True)


async def test_enable_refusal_names_the_pending_disable(h: Harness) -> None:
    controller = available(h)
    async with controller._lock:
        enabling = asyncio.create_task(select(h, "merge_when_safe", 0))
        await queued_callbacks_ran()
        disabling = asyncio.create_task(select(h, "ask_me", 1))
        await queued_callbacks_ran()
        assert len(controller._disables) == 1 and not controller._suspended
    with pytest.raises(WrongState, match="a switch to Ask me is in progress"):
        await enabling
    with pytest.raises(WrongState, match="revision is stale"):
        await disabling
    assert controller.snapshot() == MergeModeState()
    assert not controller._disables and not controller.authority_suspended()
    assert h.payloads(MergeModeState) == []
