"""Queued rules decisions renew only after settlement, without using a terminal claim."""

import asyncio

import pytest
from follow_settle_helpers import closed_failure, drain, general_evidence, on_pending, queued_move
from test_assessment import rec, settled, terminals, wait
from test_follow_main import expired, fail_scan
from test_rules_transaction import reserved as reserved

from oversight.changes import _approval, _rules
from oversight.changes._support import closed_rules_transaction
from oversight_contracts.events import ChangeResolved, SuiteExecution

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("move", ["rules_merge", "save", "person_approve"])
async def test_two_rules_changes_serialize_and_second_reassesses(h, reserved, monkeypatch, move):
    attempts = []
    decide = h.manager._decide

    def admitted(record, work):
        if record.change_id == "change-1":
            assessment = record.evidence.assessment
            attempts.append(
                (
                    record.revision,
                    record.head_commit,
                    assessment.main_commit,
                    assessment.mode_revision,
                )
            )
        return decide(record, work)

    monkeypatch.setattr(h.manager, "_decide", admitted)
    async with queued_move(h, reserved, monkeypatch, move) as (old, task):
        deadline = old.deadline
        reserved[0].advance(30)
    await drain(h, reserved[1])
    new = wait(h)
    assert not terminals(h)
    assert new is not old and new.proposal == old.proposal and new.deadline == deadline
    post = new.main_commit
    assert post != old.main_commit
    suites = [
        s for s in h.payloads(SuiteExecution) if s.change_id == "change-1" and s.phase == "combined"
    ]
    assert len(suites) == 1 and suites[0].tested_commit is None
    final = rec(h).evidence.assessment
    assert final.main_commit == post and suites[0].tested_tree == final.candidate_tree
    resolutions = [r for r in h.payloads(ChangeResolved) if r.change_id == "change-1"]
    assert len(resolutions) == 1 and resolutions[0].decided_by == "rules"
    keys = rec(h).rules_attempts
    assert keys == {
        (1, old.target.head, main, old.mode.revision) for main in (old.main_commit, post)
    }
    assert len([t for t in reserved[1] if t is task]) == 1
    assert len(attempts) == len(set(attempts)) == 2 and set(attempts) == keys


@pytest.mark.parametrize("recover", [True, False])
async def test_queued_attempt_renewal_survives_a_failed_graph_delivery(
    h, reserved, monkeypatch, recover
):
    h.manager._ctx.ports.rules_transaction = closed_rules_transaction
    scan = fail_scan(h, monkeypatch)
    async with queued_move(h, reserved, monkeypatch) as (old, _):
        pass
    new = wait(h)
    assert new is not old and new.active and new.parked and new.latest is None
    assert new.main_commit == old.main_commit and not terminals(h)
    monkeypatch.setattr(h.main_graph, "scan", scan)
    if recover:
        await h.manager.rescan()
        await drain(h, reserved[1])
        assert wait(h) is not new and wait(h).main_commit != old.main_commit
        assert len(reserved[1]) == 2
    else:
        await expired(h, reserved[0], old)
        assert len(reserved[1]) == 1


async def test_renewal_after_deadline_reports_one_expiry_and_never_evaluates(
    h, reserved, monkeypatch
):
    calls = []
    async with queued_move(h, reserved, monkeypatch) as (old, _):
        reserved[0].advance(old.deadline)
        calls.append(reserved[0].calls.call_count)
    await settled(h)
    assert wait(h) is not old and reserved[0].calls.call_count == calls[0]
    assert len(reserved[1]) == 1
    await expired(h, reserved[0], old)


@pytest.mark.parametrize("when", ["queued", "pending"])
async def test_person_acted_change_is_never_renewed(h, reserved, monkeypatch, when):
    seen = (
        on_pending(h, monkeypatch, lambda record: setattr(record, "person_acted", True))
        if when == "pending"
        else []
    )
    async with queued_move(h, reserved, monkeypatch) as (old, _):
        if when == "queued":
            rec(h).person_acted = True
    await settled(h)
    assert wait(h) is old and len(reserved[1]) == 1
    assert len(terminals(h)) == (1 if when == "queued" else 0)
    if when == "pending":
        assert seen and rec(h).follow_pending is None
    else:
        assert "code=main_moved;" in terminals(h)[0].message


async def test_main_move_never_cancels_a_queued_rules_attempt(h, reserved, monkeypatch):
    assert h.manager._ctx.cards is None
    async with queued_move(h, reserved, monkeypatch) as (old, task):
        assert not task.cancelling() and not terminals(h)
    await drain(h, reserved[1])
    assert not task.cancelled() and wait(h) is not old and not terminals(h)
    assert len([r for r in h.payloads(ChangeResolved) if r.change_id == "change-1"]) == 1


async def test_consumed_attempt_renews_only_through_attempt_keys(h, reserved, monkeypatch):
    captured = []

    async def capture(ctx, reservation):
        assert _rules.revalidate(ctx, reservation) is None
        captured.append(reservation)

    monkeypatch.setattr(_approval, "approve_rules", capture)
    general_evidence(h, monkeypatch)
    h.manager.run_ended("agent-1", None)
    await drain(h, reserved[1])
    old = wait(h)
    sched = h.manager._ctx.assessment
    sched.proposed(rec(h), old.proposal, old.mode, armed=True)
    await settled(h)
    assert len(captured) == 1 and wait(h).timer is None
    from test_follow_main import save

    post = await save(h, "return value() + value()", "return value() * 2")
    sched.proposed(rec(h), old.proposal, old.mode, armed=True)
    sched.facts_ready(rec(h), old.facts)
    await drain(h, reserved[1])
    assert len(captured) == 2 and rec(h).rules_consumed == {1}
    assert rec(h).rules_attempts == {
        (1, old.target.head, main, old.mode.revision) for main in (old.main_commit, post)
    }
    assert not terminals(h)


@pytest.mark.parametrize(
    "guard",
    [
        "CandidateVeto",
        "conflict",
        "suite_failed",
        "terminal",
        "integrity",
        "person_intent",
        "disable",
        "close",
        "revision",
        "head",
        "base",
        "resolution",
        "delivery",
        "mode",
        "suspended",
        "suspect",
        "decision",
        "rules_task",
        "current_mode",
        "working",
        "replacement",
        "pending_number",
    ],
)
async def test_settled_attempt_that_must_not_renew_stays_put(h, reserved, monkeypatch, guard):
    if guard in {"CandidateVeto", "conflict", "suite_failed"}:
        await closed_failure(h, reserved, monkeypatch, guard)
        return
    ctx = h.manager._ctx
    actions = []

    def veto(record):
        old = wait(h)
        if guard == "terminal":
            old.terminal_seq = 999
        elif guard == "integrity":
            ctx.workspace.integrity.main_suspect = True
        elif guard == "person_intent":
            record.person_intent = object()
        elif guard == "disable":
            from test_mode import select

            # Eager start makes mode_changed synchronous before the old done callback.
            loop = asyncio.get_running_loop()
            factory = loop.get_task_factory()
            loop.set_task_factory(asyncio.eager_task_factory)
            try:
                actions.append(asyncio.create_task(select(h, "ask_me", 1)))
            finally:
                loop.set_task_factory(factory)
            assert record.follow_pending is None
        elif guard == "close":
            ctx.state.closing = True
        elif guard == "revision":
            record.revision += 1
        elif guard == "head":
            record.head_commit = "other"
        elif guard == "base":
            record.base_commit = "other"
        elif guard == "resolution":
            record.resolution = object()
        elif guard == "delivery":
            record.delivery_rejected = "Rejected"
        elif guard == "mode":
            record.proposal_mode = old.mode.model_copy(update={"revision": 9})
        elif guard == "suspended":
            ctx.mode._suspended = True
        elif guard == "suspect":
            ctx.state.suspect = True
        elif guard == "decision":
            record.decision += 1
            record.rules_task = None  # Isolate decision identity after the owner retired.
        elif guard == "rules_task":

            async def other_owner():
                pass

            record.rules_task = ctx.spawn(other_owner())
        elif guard == "current_mode":
            ctx.mode._state = old.mode.model_copy(update={"revision": 9})
        elif guard == "working":
            record.state = "working"
        elif guard == "replacement":
            ctx.assessment.proposed(record, old.proposal, old.mode, armed=False)
            assert record.follow_pending is None
        else:
            record.follow_pending += 1

    seen = on_pending(h, monkeypatch, veto)
    async with queued_move(h, reserved, monkeypatch) as (old, _):
        pass
    await asyncio.gather(*actions)
    assert seen and wait(h) is old and len(reserved[1]) == 1 and len(terminals(h)) <= 1
    # Do not leave deliberately forged private authority in the fixture's cleanup path.
    rec(h).person_intent = None
    if guard == "resolution":
        rec(h).resolution = None


async def test_unannounced_move_remains_terminal(h, reserved, monkeypatch):
    general_evidence(h, monkeypatch)
    async with h.workspace.locks(main=True):
        h.manager.run_ended("agent-1", None)
        await settled(h)
        task, old = rec(h).rules_task, wait(h)
        await h.manager._ctx.git(h.workspace.main, "commit", "--allow-empty", "-m", "Unannounced")
    await task
    await settled(h)
    assert wait(h) is old and len(terminals(h)) == 1
    assert "code=main_moved;" in terminals(h)[0].message
    assert len(reserved[1]) == 1 and rec(h).follow_pending is None


@pytest.mark.parametrize(
    "guard",
    [
        "unannounced",
        "different_head",
        "same_main",
        "suspect",
        "integrity",
        "availability",
        "person_acted",
        "current",
    ],
)
async def test_queue_verifies_every_guard_after_head_read(h, reserved, monkeypatch, guard):
    ctx = h.manager._ctx
    git, observed, queued = ctx.git, [], []
    on_pending(h, monkeypatch, lambda record: queued.append(record.follow_pending))

    async def reading(tree, *args, **kwargs):
        result = await git(tree, *args, **kwargs)
        if (
            tree is h.workspace.main
            and args == ("rev-parse", "HEAD")
            and rec(h).state == "deciding"
            and h.workspace.operations.holds(agent_id="agent-1")
            and not observed
        ):
            observed.append(True)
            assert h.workspace.operations.holds(main=True)
            assert h.workspace.operations._git.owner is asyncio.current_task()
            if guard == "unannounced":
                ctx.assessment.committed_main = None
            elif guard == "different_head":
                ctx.assessment.committed_main = "other"
            elif guard == "same_main":
                result = wait(h).main_commit
                ctx.assessment.committed_main = result
            elif guard == "suspect":
                ctx.state.suspect = True
            elif guard == "integrity":
                ctx.workspace.integrity.main_suspect = True
            elif guard == "availability":
                monkeypatch.setattr(_rules, "availability", lambda _: ("policy_invalid", "Lost"))
            elif guard == "person_acted":
                rec(h).person_acted = True
            else:
                rec(h).decision += 1
        return result

    async with queued_move(h, reserved, monkeypatch) as (old, _):
        monkeypatch.setattr(ctx, "git", reading)
    await settled(h)
    assert observed and not queued and wait(h) is old and len(reserved[1]) == 1
    assert rec(h).follow_pending is None and len(terminals(h)) <= 1
