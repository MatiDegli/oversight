"""Follow eligibility, invalidation, restart, and notification ordering."""

import asyncio
from typing import cast

import pytest
from changes_helpers import EDIT, PATH, Harness, commit_on_main
from test_assessment import clock as clock
from test_assessment import propose, rec, settled, terminals, wait
from test_follow_main import fail_scan, ready_wait, save
from test_mode import select

from oversight.changes import ChangeManager, _delivery
from oversight.changes._mode import RULES_ACTIVATION
from oversight.changes._preemption import PersonIntent
from oversight.changes._proposal import record_proposal
from oversight.changes._state import APPROVABLE
from oversight_contracts.events import ChangeResolved
from oversight_contracts.policy import ChangeAssessment

pytestmark = pytest.mark.anyio


async def test_closed_latch_scheduling_is_inert(h: Harness, clock):
    await h.run_agent(1, EDIT)
    post = await save(h)
    await settled(h)
    sched = h.manager._ctx.assessment
    assert not RULES_ACTIVATION and sched.waits == {} and sched.committed_main == post
    assert not clock.calls.called and not h.payloads(ChangeAssessment) and not terminals(h)
    assert "error" not in h.types()


@pytest.mark.parametrize(
    "guard",
    [
        "person_acted",
        "person_intent",
        "consumed",
        "resolution",
        "terminal",
        "taken",
        "other_mode",
        "stale_head",
        "ask_me_proposal",
    ],
)
async def test_wait_that_must_not_follow_stays_put(h: Harness, clock, guard):
    if guard == "ask_me_proposal":
        await propose(h)
        h.manager.run_ended("agent-1", None)
        old = None
    else:
        old = await ready_wait(h, clock)
    record = rec(h)
    if guard == "person_acted":
        record.person_acted = True
    elif guard == "person_intent":
        record.person_intent = cast(PersonIntent, object())
    elif guard == "consumed":
        record.rules_consumed.add(record.revision)
        consumed = h.manager._ctx.assessment.waits[record.change_id]
        record.rules_attempts.add(
            (record.revision, consumed.target.head, consumed.main_commit, consumed.mode.revision)
        )
    elif guard == "resolution":
        record.resolution = ChangeResolved(
            change_id=record.change_id,
            agent_id=record.agent_id,
            revision=record.revision,
            head_commit=record.head_commit,
            decision="rejected",
            decided_by="person",
            reason=None,
            comment=None,
            merge=None,
            merge_commit=None,
            conflict=None,
        )
    elif guard == "terminal":
        assert old is not None
        await h.manager._ctx.assessment.terminal(old, "Already terminal.")
    elif guard == "taken":
        h.manager._take(record.change_id, APPROVABLE, "test")
    elif guard == "other_mode":
        assert old is not None
        record.proposal_mode = old.mode.model_copy(update={"revision": 9})
    elif guard == "stale_head":
        record.head_commit = "f" * 40
    baseline = len(terminals(h))
    calls = clock.calls.call_count
    await save(h)
    await settled(h)
    assert h.manager._ctx.assessment.waits.get("change-1") is old
    assert old is None or not getattr(old, "parked", False)
    assert clock.calls.call_count == calls and len(terminals(h)) == baseline


@pytest.mark.parametrize("event", ["disable", "close", "take", "revision"])
async def test_follow_cancel_disable_restart_and_late_callbacks(
    h: Harness, clock, monkeypatch, event
):
    old = await ready_wait(h, clock)
    sched = h.manager._ctx.assessment
    scan = fail_scan(h, monkeypatch)
    post = await save(h)
    assert old.parked
    if event == "disable":
        await select(h, "ask_me", 1)
    elif event == "close":
        await h.manager.aclose()
    elif event == "take":
        h.manager._take("change-1", APPROVABLE, "test")
    else:
        rec(h).state = "working"  # The next proposal arrives before its run ends.
        proposal = old.proposal.model_copy(update={"revision": 2})
        await record_proposal(h.manager._ctx, rec(h), proposal)
    monkeypatch.setattr(h.main_graph, "scan", scan)
    if event == "close":
        # A delayed delivery callback may arrive even after close; no new command is accepted.
        sched.main_delivered(post)
    else:
        await h.manager.rescan()
    await settled(h)
    assert not old.active and old.timer.cancelled()
    assert (
        sched.waits["change-1"] is old
        or sched.waits["change-1"].target.revision != old.target.revision
    )
    count = len(h.payloads(ChangeAssessment))
    clock.advance(130)
    await sched.expire(old)
    await sched.terminal(old, "Late terminal")
    await settled(h)
    assert not terminals(h) and len(h.payloads(ChangeAssessment)) == count
    restarted = ChangeManager(
        h.workspace, h.publish, main_graph=h.main_graph, main_snapshot=h.manager.snapshot
    )
    assert restarted._ctx.assessment.waits == {}
    assert restarted._ctx.assessment.committed_main is None
    await restarted.aclose()


async def test_hooks_run_before_scan_and_after_snapshot(h: Harness, clock, monkeypatch):
    sched, ctx = h.manager._ctx.assessment, h.manager._ctx
    observed = []
    changed, delivered, scan = sched.main_changed, sched.main_delivered, ctx.scan

    def announce(post):
        observed.append(("changed", post))
        changed(post)

    async def scanning(post):
        assert observed[-1] == ("changed", post)
        observed.append(("scan", post))
        return await scan(post)

    def receive(post):
        assert ctx.state.snapshot.commit == post
        observed.append(("delivered", post))
        delivered(post)

    monkeypatch.setattr(sched, "main_changed", announce)
    monkeypatch.setattr(sched, "main_delivered", receive)
    monkeypatch.setattr(ctx, "scan", scanning)
    await save(h)
    await h.manager.rescan()  # delta is None: still delivers the current snapshot.
    await commit_on_main(h, PATH, "return 4", "return 5")
    await h.manager.rescan()
    await h.run_agent(1, (PATH, "return 5", "return 6"))
    record = rec(h)
    post = await commit_on_main(h, PATH, "return 5", "return 7")
    await _delivery.project_merge(ctx, record, post)

    # The same receipt callback used by merge, rules merge and recovery.
    async def sent():
        assert record.delta is not None
        await ctx.publish(record.delta)

    task = asyncio.create_task(sent())
    await task
    _delivery.receipt(ctx, record, "delta", task)
    assert [kind for kind, _ in observed] == ["changed", "scan", "delivered"] * 4


async def test_ready_control_in_guard_file(h: Harness, clock):
    old = await ready_wait(h, clock)
    post = await save(h)
    await settled(h)
    assert wait(h) is not old and wait(h).main_commit == post
