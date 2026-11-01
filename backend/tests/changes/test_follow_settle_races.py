"""F6 person intent wins at lock wait, HEAD verification and renewed publication."""

import asyncio

import pytest
from changes_helpers import bounded, queued_callbacks_ran
from follow_settle_helpers import drain, general_evidence, queued_move
from test_assessment import rec, settled, terminals, wait
from test_person_preemption import person
from test_rules_transaction import reserved as reserved

from oversight.changes._state import ChangeState
from oversight_contracts.events import ChangeResolved
from oversight_contracts.policy import ChangeAssessment

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("when", ["before_restore", "after_restore"])
@bounded
async def test_person_claim_beats_pending_follow(h, reserved, monkeypatch, when):
    decisions = []
    if when == "after_restore":
        from follow_settle_helpers import on_pending

        on_pending(h, monkeypatch, lambda record: setattr(h, "gate", ChangeAssessment))
    async with queued_move(h, reserved, monkeypatch, settle=when != "after_restore") as (old, task):
        if when == "before_restore":
            decisions.append(person(h))
            assert task.cancelling() and rec(h).follow_pending is None
    if when == "after_restore":
        await h.at_gate.wait()
        assert wait(h) is not old
        decisions.append(person(h))
        h.open_gate.set()
    await asyncio.gather(*decisions)
    await drain(h, reserved[1])
    [resolved] = h.payloads(ChangeResolved)
    assert resolved.decision == "rejected" and resolved.decided_by == "person"
    assert rec(h).state == "closed" and not terminals(h)
    assert len(reserved[1]) == 2  # The original rules owner and one person owner.
    if when == "before_restore":
        assert wait(h) is old and task.cancelled()


@pytest.mark.parametrize("boundary", ["lock_wait", "head_read", "restore"])
@bounded
async def test_preemption_beats_follow_renewal(h, reserved, monkeypatch, boundary):
    general_evidence(h, monkeypatch)
    old, checks, decisions = wait(h), [], []
    restore = ChangeState.restore

    def restored(state, record, decision):
        if boundary == "restore" and record.follow_pending == decision and not decisions:
            decisions.append(person(h))
            assert record.follow_pending is None
        restore(state, record, decision)
        if record.person_intent is not None:
            checks.append(True)
            assert record.follow_pending is None
            assert not h.manager._ctx.assessment.valid(old)

    monkeypatch.setattr(ChangeState, "restore", restored)
    if boundary == "head_read":
        async with h.workspace.locks(main=True):
            h.manager.run_ended("agent-1", None)
            await settled(h)
            task = rec(h).rules_task
            # Announce and deliver an actual commit before releasing preflight.
            ctx = h.manager._ctx
            await ctx.git(h.workspace.main, "commit", "--allow-empty", "-m", "Managed move")
            post = (await ctx.git(h.workspace.main, "rev-parse", "HEAD")).strip()
            ctx.assessment.main_changed(post)
            ctx.state.snapshot = ctx.state.snapshot.model_copy(update={"commit": post})
            ctx.assessment.main_delivered(post)
            h.workspace.arm("rev-parse", mode="hold")
        await h.workspace.entered.wait()
        decision = person(h)
        assert rec(h).person_intent is not None
        h.workspace.resume.set()
        await decision
    else:
        async with queued_move(h, reserved, monkeypatch) as (_, task):
            if boundary != "restore":
                decisions.append(person(h))
                await queued_callbacks_ran()
        await asyncio.gather(*decisions)
    await settled(h)
    assert checks and task.cancelled() and wait(h) is old
    assert rec(h).follow_pending is None and rec(h).state == "closed"
    assert len(h.payloads(ChangeAssessment)) == 1 and not terminals(h)
    [resolved] = h.payloads(ChangeResolved)
    assert resolved.decided_by == "person"
