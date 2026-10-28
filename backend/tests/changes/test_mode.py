"""Mode CAS, closed startup port, receipt uncertainty and lock independence."""

import asyncio
from typing import NoReturn

import pytest
from changes_helpers import Harness

from oversight.changes import ModeController, WrongState
from oversight.changes._mode import StartCapability
from oversight_contracts.commands import ERROR_STATUS, SetMergeMode
from oversight_contracts.policy import MergeModeState

pytestmark = pytest.mark.anyio


def available(h: Harness, *, timeout_s: float = 5) -> ModeController:
    """Only tests confirm the startup fact; the manager always builds a closed port."""
    mode = ModeController(
        availability=lambda: StartCapability(True, "Test startup confirmed"), timeout_s=timeout_s
    )
    h.manager._ctx.mode = mode
    return mode


async def select(h: Harness, mode: str, revision: int) -> MergeModeState:
    return await h.manager.set_merge_mode(
        SetMergeMode.model_validate(dict(mode=mode, revision=revision))
    )


async def test_initial_state_and_snapshot_are_independent(h: Harness) -> None:
    controller = h.manager._ctx.mode
    assert isinstance(controller, ModeController)
    assert controller._timeout_s == 5
    first = controller.snapshot()
    assert first == h.manager.merge_mode() == MergeModeState(mode="ask_me", revision=0)
    assert first is not controller.snapshot()
    assert not h.manager.authority_suspended()
    assert h.payloads(MergeModeState) == []


@pytest.mark.parametrize("mode", ["ask_me", "merge_when_safe"])
async def test_stale_cas_is_wrong_state_even_for_same_mode(h: Harness, mode: str) -> None:
    available(h)
    with pytest.raises(WrongState, match="revision is stale"):
        await select(h, mode, 1)
    assert ERROR_STATUS["wrong_state"] == 409
    assert h.manager.merge_mode() == MergeModeState()
    assert not h.manager.authority_suspended()
    assert h.payloads(MergeModeState) == []


async def test_fresh_cas_increments_once_and_snapshot_does_not_follow_it(h: Harness) -> None:
    controller = available(h)
    before = controller.snapshot()
    on = await select(h, "merge_when_safe", 0)
    assert on == MergeModeState(mode="merge_when_safe", revision=1)
    with pytest.raises(WrongState, match="revision is stale"):
        await select(h, "ask_me", 0)
    off = await select(h, "ask_me", 1)
    assert off == MergeModeState(mode="ask_me", revision=2)
    assert before == MergeModeState()
    assert controller.snapshot() == off
    assert h.payloads(MergeModeState) == [on, off]
    assert h.types().count("merge_mode.changed") == 2


@pytest.mark.parametrize("mode", ["ask_me", "merge_when_safe"])
async def test_same_state_has_no_event_or_revision_change(h: Harness, mode: str) -> None:
    available(h)
    if mode == "merge_when_safe":
        await select(h, mode, 0)
    before = h.manager.merge_mode()
    events = list(h.log)
    assert await select(h, mode, before.revision) == before
    assert h.log == events
    assert not h.manager.authority_suspended()


async def test_production_port_refuses_activation_with_visible_reason(h: Harness) -> None:
    with pytest.raises(WrongState, match="start capability is unavailable; launcher not connected"):
        await select(h, "merge_when_safe", 0)
    assert h.manager.merge_mode() == MergeModeState()
    assert not h.manager.authority_suspended()
    assert h.payloads(MergeModeState) == []
    assert await select(h, "ask_me", 0) == MergeModeState()


@pytest.mark.parametrize("reason", ["Startup probe pending", "Startup contamination unresolved"])
async def test_unconfirmed_start_capability_is_refused(h: Harness, reason: str) -> None:
    h.manager._ctx.mode = ModeController(availability=lambda: StartCapability(False, reason))
    with pytest.raises(WrongState, match=reason):
        await select(h, "merge_when_safe", 0)
    assert h.manager.merge_mode() == MergeModeState()
    assert h.payloads(MergeModeState) == []


async def test_mode_lock_wait_has_a_bound(h: Harness) -> None:
    controller = available(h, timeout_s=0.02)
    async with controller._lock:
        async with asyncio.timeout(0.5):
            with pytest.raises(WrongState, match=r"timed out after 0\.02 seconds"):
                await select(h, "ask_me", 0)
    assert not controller._lock.locked()
    assert not h.manager.authority_suspended()
    assert h.payloads(MergeModeState) == []


@pytest.mark.parametrize("committed", [False, True])
async def test_receipt_suspends_until_confirmation(h: Harness, committed: bool) -> None:
    controller = available(h)
    if committed:
        h.hold = MergeModeState
    else:
        h.gate = MergeModeState
    setting = asyncio.create_task(select(h, "merge_when_safe", 0))
    try:
        async with asyncio.timeout(1):
            await (h.held if committed else h.at_gate).wait()
        assert not setting.done()
        assert controller._lock.locked()
        assert h.manager.authority_suspended()
        assert controller.snapshot() == MergeModeState()
        assert len(h.payloads(MergeModeState)) == int(committed)
    finally:
        h.release.set()
        h.open_gate.set()
        await setting
    assert controller.snapshot() == MergeModeState(mode="merge_when_safe", revision=1)
    assert not h.manager.authority_suspended()


async def test_failed_publish_keeps_authority_suspended_and_activation_refused(h: Harness) -> None:
    controller = available(h)
    h.fail_once = MergeModeState
    with pytest.raises(WrongState, match="event was not confirmed"):
        await select(h, "merge_when_safe", 0)
    assert controller.snapshot() == MergeModeState()
    assert h.manager.authority_suspended()
    with pytest.raises(WrongState, match="authority is suspended"):
        await select(h, "merge_when_safe", 0)
    assert h.payloads(MergeModeState) == []
    # Disabling needs no startup capability or authority, including this no-op.
    assert await select(h, "ask_me", 0) == MergeModeState()
    assert h.manager.authority_suspended()


async def test_failed_disable_can_be_retried_while_suspended(h: Harness) -> None:
    available(h)
    on = await select(h, "merge_when_safe", 0)
    h.fail_once = MergeModeState
    with pytest.raises(WrongState, match="event was not confirmed"):
        await select(h, "ask_me", 1)
    assert h.manager.merge_mode() == on
    assert h.manager.authority_suspended()
    off = await select(h, "ask_me", 1)
    assert off == MergeModeState(mode="ask_me", revision=2)
    assert not h.manager.authority_suspended()
    assert h.payloads(MergeModeState) == [on, off]


async def test_confirmation_timeout_keeps_receipt_tracked_and_prevents_overtaking(
    h: Harness,
) -> None:
    controller = available(h, timeout_s=0.02)
    h.gate = MergeModeState
    try:
        async with asyncio.timeout(0.5):
            with pytest.raises(WrongState, match="timed out"):
                await select(h, "merge_when_safe", 0)
            assert h.manager.authority_suspended()
            assert controller.snapshot() == MergeModeState()
            receipt = controller._receipt
            assert receipt in h.manager._ctx.state.tasks
            assert receipt is not None and not receipt.done()
            with pytest.raises(WrongState, match="timed out"):
                await select(h, "ask_me", 0)
            assert controller._receipt is receipt
    finally:
        h.open_gate.set()
        await asyncio.gather(*h.manager._ctx.state.tasks)
    assert not h.manager.authority_suspended()
    assert controller.snapshot() == MergeModeState(mode="merge_when_safe", revision=1)
    assert len(h.payloads(MergeModeState)) == 1


@pytest.mark.parametrize("fail", [False, True])
async def test_cancelled_caller_leaves_receipt_alive(h: Harness, fail: bool) -> None:
    controller = available(h)
    h.gate = MergeModeState
    if fail:
        h.fail_once = MergeModeState
    setting = asyncio.create_task(select(h, "merge_when_safe", 0))
    try:
        async with asyncio.timeout(1):
            await h.at_gate.wait()
        setting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await setting
        assert h.manager.authority_suspended()
        receipt = controller._receipt
        assert receipt is not None and not receipt.done()
        assert receipt in h.manager._ctx.state.tasks
    finally:
        h.open_gate.set()
        await asyncio.gather(*h.manager._ctx.state.tasks, return_exceptions=True)
    assert h.manager.authority_suspended() is fail
    assert controller.snapshot() == (
        MergeModeState() if fail else MergeModeState(mode="merge_when_safe", revision=1)
    )


async def test_concurrent_fresh_commands_have_one_winner(h: Harness) -> None:
    available(h)
    results = await asyncio.gather(
        select(h, "merge_when_safe", 0), select(h, "merge_when_safe", 0), return_exceptions=True
    )
    assert sum(isinstance(result, MergeModeState) for result in results) == 1
    assert sum(isinstance(result, WrongState) for result in results) == 1
    assert len(h.payloads(MergeModeState)) == 1


async def test_disable_uses_only_mode_lock_and_never_git_or_idleness(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = available(h)
    await select(h, "merge_when_safe", 0)
    calls = list(h.workspace.calls)

    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("Mode path acquired a workspace lock, called Git or queried startup capability")

    async with h.workspace.locks(main=True, agent_id="agent-1"):
        assert not h.workspace.operations.idle
        monkeypatch.setattr(h.workspace, "locks", forbidden)
        monkeypatch.setattr(h.workspace, "git", forbidden)
        monkeypatch.setattr(h.workspace, "git_ok", forbidden)
        monkeypatch.setattr(controller, "_availability", forbidden)
        # Another task owns main/worktree throughout this effective mode change.
        async with asyncio.timeout(1):
            off = await asyncio.create_task(select(h, "ask_me", 1))
    assert off == MergeModeState(mode="ask_me", revision=2)
    assert h.workspace.calls == calls
    assert not controller._lock.locked()
    assert not h.manager.authority_suspended()
