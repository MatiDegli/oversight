"""Real Git/store preflight with injected prerequisite facts; never a combined suite."""

import ast
import asyncio
import inspect
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from changes_helpers import EDIT, Harness, queued_callbacks_ran
from test_assessment import (
    full_evidence,
    head,
    latest,
    make_clock,
    propose,
    rec,
    settled,
    terminals,
    wait,
)
from test_mode import available, select
from test_policy_profiles import policy_inputs

from oversight.changes import ChangeManager, WrongState, _approval, _rules
from oversight.changes._support import closed_rules_transaction
from oversight.events import format_needs_you, parse_needs_you
from oversight_contracts.commands import ERROR_STATUS
from oversight_contracts.events import ChangeResolved, ErrorReport, SuiteExecution
from oversight_contracts.policy import ChangeAssessment

pytestmark = pytest.mark.anyio


@pytest.fixture
async def armed(h: Harness, monkeypatch):
    clock = make_clock(h, monkeypatch)
    h.manager._ctx.ports.rules_transaction = closed_rules_transaction
    available(h)
    await select(h, "merge_when_safe", 0)
    full_evidence(h, policy_inputs())
    await propose(h)
    # A confirmed session fact, independent of the deliberately disconnected startup port.
    monkeypatch.setattr(h.workspace._isolation, "_born", True)
    monkeypatch.setattr(h.workspace._isolation, "_checked", True)
    decisions = []
    decide = h.manager._decide

    def track(record, work):
        assert record.state == "deciding"
        assert record.taken == record.revision
        assert record.revision in record.rules_consumed
        assert record.evidence.actor == "rules"
        task = decide(record, work)
        decisions.append(task)
        return task

    monkeypatch.setattr(h.manager, "_decide", track)
    return clock, decisions


def evidence(h):
    value = rec(h).evidence
    assert value is not None
    return value


def terminal_code(h):
    note = parse_needs_you(terminals(h)[0].message)
    assert note is not None
    return note.code


async def start(h, armed):
    h.manager.run_ended("agent-1", None)
    await settled(h)
    assert len(armed[1]) == 1
    return armed[1][0]


async def test_completed_decision_restores_before_done_callbacks(h: Harness, armed):
    # A completed task can be awaited without yielding to its queued done-callbacks.
    # Eager execution makes that ordering deterministic, without scheduler sleeps.
    record = rec(h)
    record.fallback, record.state = "ready", "deciding"

    async def completed():
        pass

    loop = asyncio.get_running_loop()
    factory = loop.get_task_factory()
    loop.set_task_factory(asyncio.eager_task_factory)
    try:
        task = ChangeManager._decide(h.manager, record, completed())
        assert task.done()
        await task
        assert record.state == "ready"
    finally:
        loop.set_task_factory(factory)


async def test_atomic_reservation_declines_restores_and_never_retries(h: Harness, armed):
    before = await h.main_head()
    calls = len(h.workspace.calls)
    task = await start(h, armed)
    await task
    assert rec(h).state == "ready" and rec(h).rules_consumed == {1}
    assert rec(h).taken == 1 and not rec(h).person_acted
    assert evidence(h).assessment == wait(h).latest
    assert "transaction is unavailable" in terminals(h)[0].message
    assert wait(h).terminal_seq is not None
    assert await h.main_head() == before
    assert not any(c.args[0] in {"merge", "commit", "add"} for c in h.workspace.calls[calls:])
    assert not h.payloads(ChangeResolved)
    assert not any(e.phase == "combined" for e in h.payloads(SuiteExecution))
    for _ in range(2):
        h.manager._admit_rules(rec(h), latest(h))
        h.manager._ctx.assessment.wake(rec(h))
    await settled(h)
    assert len(armed[1]) == 1 and len(terminals(h)) == 1
    # The admission port must remain synchronous all the way to installing restore.
    tree = ast.parse(inspect.getsource(ChangeManager).strip())
    admission = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_admit_rules"
    )
    assert not any(isinstance(n, ast.Await) for n in ast.walk(admission))


@pytest.mark.parametrize("committed", [False, True])
async def test_persist_pending_before_waiting_main(h: Harness, armed, committed):
    if committed:
        h.hold = ChangeAssessment
    else:
        h.gate = ChangeAssessment
    calls = len(h.workspace.calls)
    async with h.workspace.locks(main=True):
        h.manager.run_ended("agent-1", None)
        await (h.held if committed else h.at_gate).wait()
        assert rec(h).state == "ready" and not rec(h).rules_consumed
        h.release.set()
        h.open_gate.set()
        await settled(h)
        [assessment] = h.payloads(ChangeAssessment)
        pending = next(r for r in assessment.rules if r.rule == "combined_tests")
        assert pending.result == "unknown" and pending.observed is None
        assert rec(h).state == "deciding" and rec(h).rules_consumed == {1}
        assert len(h.workspace.calls) == calls
        assert not armed[1][0].done()
        armed[0].advance(121)
        await queued_callbacks_ran()
        assert terminals(h) == []
    await armed[1][0]
    assert rec(h).state == "ready" and len(terminals(h)) == 1


async def test_person_wins_without_consumption(h: Harness, armed, monkeypatch):
    h.gate = ChangeAssessment
    h.manager.run_ended("agent-1", None)
    await h.at_gate.wait()
    # Restore the ordinary person decision path (the fixture spies only on rules).
    monkeypatch.setattr(h.manager, "_decide", ChangeManager._decide.__get__(h.manager))
    await h.manager.reject("change-1", None, revision=1, head_commit=head(h))
    h.open_gate.set()
    await settled(h)
    assert rec(h).state == "closed" and not rec(h).rules_consumed and not armed[1]
    armed[0].advance(121)
    await h.manager._ctx.assessment.terminal(wait(h), "Late callback")
    assert terminals(h) == []


@pytest.mark.parametrize("decision", ["approve"])
async def test_person_during_rules_gets_explanatory_409(h: Harness, armed, decision):
    async with h.workspace.locks(main=True):
        task = await start(h, armed)
        with pytest.raises(WrongState, match=r"deciding.*refresh and review"):
            getattr(h.manager, decision)(
                "change-1", "Please review", revision=1, head_commit=head(h)
            )
        assert ERROR_STATUS["wrong_state"] == 409 and rec(h).rules_consumed == {1}
    await task


async def test_lock_order_and_port_receive_exact_capture(h: Harness, armed):
    ctx = h.manager._ctx
    calls = len(h.workspace.calls)

    async def port(record, evidence):
        assert h.workspace.operations.holds(main=True)
        assert h.workspace.operations.holds(agent_id="agent-1")
        assert evidence is record.evidence and evidence.actor == "rules"
        assert evidence.assessment == wait(h).latest
        assert h.payloads(ChangeAssessment) == [evidence.assessment]
        assert not ctx.mode._lock.locked()
        return "Test transaction declined"

    ctx.ports.rules_transaction = AsyncMock(side_effect=port)
    await (await start(h, armed))
    ctx.ports.rules_transaction.assert_awaited_once()
    assert all(c.held == ("main", "agent-1") for c in h.workspace.calls[calls:])
    assert "Test transaction declined" in terminals(h)[0].message


@pytest.mark.parametrize("change", ["actual_main", "snapshot", "head", "tree", "dirty", "query"])
async def test_git_revalidation(h: Harness, armed, monkeypatch, change):
    ctx = h.manager._ctx
    port = ctx.ports.rules_transaction = AsyncMock(return_value="Must not reach transaction")
    async with h.workspace.locks(main=True):
        task = await start(h, armed)
        if change == "actual_main":
            await ctx.git(h.workspace.main, "commit", "--allow-empty", "-m", "Move main")
        elif change == "snapshot":
            ctx.state.snapshot = ctx.state.snapshot.model_copy(update={"commit": "other"})
        elif change == "head":
            async with h.workspace.locks(agent_id="agent-1"):
                await ctx.git(rec(h).tree, "commit", "--allow-empty", "-m", "Move proposal")
        elif change == "tree":
            git = ctx.git

            async def changed_tree(tree, *args, **kwargs):
                if tree is rec(h).tree and args == ("rev-parse", "HEAD^{tree}"):
                    return "other"
                return await git(tree, *args, **kwargs)

            monkeypatch.setattr(ctx, "git", changed_tree)
        elif change == "dirty":
            await rec(h).tree.write_text("extra.py", "x = 1\n", expected_hash=None)
        else:
            h.workspace.arm("rev-parse", tree="agent-1")
    await task
    expected = {
        "actual_main": "main_moved",
        "snapshot": "main_moved",
        "head": "target_changed",
        "tree": "worktree_unverifiable",
        "dirty": "worktree_unverifiable",
        "query": "worktree_unverifiable",
    }[change]
    assert terminal_code(h) == expected
    assert rec(h).state == "ready" and rec(h).rules_consumed == {1}
    port.assert_not_awaited()


@pytest.mark.parametrize(
    "guard",
    [
        "taken",
        "consumed",
        "base",
        "fallback",
        "evidence",
        "latest",
        "receipt",
        "mode",
        "suspended",
        "suspect",
        "captured_mode",
        "integrity_suspect",
        "delivery",
    ],
)
async def test_reserved_revalidation(h: Harness, armed, guard):
    ctx = h.manager._ctx
    port = ctx.ports.rules_transaction = AsyncMock(return_value="Must not reach transaction")
    async with h.workspace.locks(main=True):
        task = await start(h, armed)
        record = rec(h)
        if guard == "taken":
            record.taken = 0
        elif guard == "consumed":
            record.rules_consumed.clear()
        elif guard == "base":
            record.base_commit = "other"
        elif guard == "fallback":
            record.fallback = "working"
        elif guard == "evidence":
            record.evidence = replace(evidence(h), actor="person")
        elif guard == "latest":
            wait(h).latest = latest(h).model_copy(update={"candidate_tree": "other"})
        elif guard == "receipt":
            ctx.assessment.events.clear()
        elif guard == "mode":
            ctx.mode._state = ctx.mode.snapshot().model_copy(update={"revision": 2})
        elif guard == "suspended":
            ctx.mode._suspended = True
        elif guard == "captured_mode":
            record.proposal_mode = None
        elif guard == "integrity_suspect":
            ctx.workspace.integrity.main_suspect = True
        elif guard == "delivery":
            record.delivery_rejected = "Rejected"
        else:
            ctx.state.suspect = True
    await task
    expected = (
        "target_changed"
        if guard in {"taken", "consumed", "base", "fallback", "delivery"}
        else "policy_invalid"
    )
    assert terminal_code(h) == expected
    port.assert_not_awaited()


@pytest.mark.parametrize("late", ["person", "revision", "close", "ack"])
async def test_late_reserved_callback_is_silent(h: Harness, armed, monkeypatch, late):
    captured = []

    async def capture(ctx, reservation):
        captured.append(reservation)

    monkeypatch.setattr(_approval, "approve_rules", capture)
    await (await start(h, armed))
    [reservation] = captured
    assert rec(h).state == "ready"
    if late == "person":
        monkeypatch.setattr(h.manager, "_decide", ChangeManager._decide.__get__(h.manager))
        await h.manager.reject("change-1", None, revision=1, head_commit=head(h))
    elif late == "revision":
        rec(h).state = "working"
        await h.edit(1, *(EDIT[0], "return 3", "return 4"))
        assert await h.manager.proposed("agent-1", "New revision")
    elif late == "close":
        await h.manager.aclose()
    else:
        receipt = await h.publish(
            ErrorReport(
                source="server",
                change_id="change-1",
                agent_id="agent-1",
                fatal=False,
                message=format_needs_you(1, head(h), "ledger", "Already terminal"),
            )
        )
        h.manager.rules_received(receipt)
    # Model a delayed callback that still holds the old reservation.
    before = list(terminals(h))
    await _rules.terminal(h.manager._ctx, reservation, "cancelled", "Late callback")
    armed[0].advance(121)
    await h.manager._ctx.assessment.expire(reservation.wait)
    assert terminals(h) == before


async def test_cancelled_preflight_restores_once(h: Harness, armed):
    async with h.workspace.locks(main=True):
        task = await start(h, armed)
        await queued_callbacks_ran()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert rec(h).state == "ready" and rec(h).rules_consumed == {1}
    assert terminal_code(h) == "cancelled"


async def test_consumed_revision_cannot_rearm_or_regenerate(h: Harness, armed):
    await (await start(h, armed))
    old = wait(h)
    calls = armed[0].calls.call_count
    h.manager._ctx.assessment.proposed(rec(h), old.proposal, old.mode, armed=True)
    await settled(h)
    assert len(armed[1]) == 1 and armed[0].calls.call_count == calls
    assert wait(h).timer is None and rec(h).rules_consumed == {1}


async def test_main_moved_while_assessment_receipt_waits(h: Harness, armed):
    h.gate = ChangeAssessment
    h.manager.run_ended("agent-1", None)
    await h.at_gate.wait()
    ctx = h.manager._ctx
    ctx.state.snapshot = ctx.state.snapshot.model_copy(update={"commit": "other"})
    h.open_gate.set()
    await settled(h)
    assert not armed[1] and rec(h).state == "ready" and not rec(h).rules_consumed
    assert terminal_code(h) == "main_moved"


async def test_evidence_rechecked_after_git_wait(h: Harness, armed):
    ctx = h.manager._ctx
    port = ctx.ports.rules_transaction = AsyncMock(return_value="Must not reach transaction")
    h.workspace.arm("rev-parse", tree="agent-1", mode="hold")
    task = await start(h, armed)
    await h.workspace.entered.wait()
    ctx.mode._suspended = True
    h.workspace.resume.set()
    await task
    assert terminal_code(h) == "policy_invalid"
    port.assert_not_awaited()


@pytest.mark.parametrize(
    "field,value",
    [
        ("policy_version", "safe-merge-v1"),
        ("mode", "ask_me"),
        ("candidate_tree", None),
        ("head_commit", "other"),
    ],
)
async def test_admission_rejects_forged_policy_without_consumption(h: Harness, armed, field, value):
    # Capture the persisted pending assessment without reserving it yet.
    h.manager._ctx.ports.rules_admission = lambda record, assessment: None
    h.manager.run_ended("agent-1", None)
    await settled(h)
    forged = latest(h).model_copy(update={field: value})
    wait(h).latest = forged
    assert h.manager._admit_rules(rec(h), forged)
    assert rec(h).state == "ready" and not rec(h).rules_consumed and not armed[1]


@pytest.mark.parametrize(
    "guard", ["state", "decision", "revision", "head", "generation", "close", "resolution", "ack"]
)
async def test_inflight_callback_rechecks_each_liveness_guard(
    h: Harness, armed, monkeypatch, guard
):
    captures = []
    approve = _approval.approve_rules

    async def spy(ctx, reservation):
        captures.append(reservation)
        await approve(ctx, reservation)

    monkeypatch.setattr(_approval, "approve_rules", spy)
    ctx = h.manager._ctx
    async with h.workspace.locks(main=True):
        task = await start(h, armed)
        [reservation] = captures
        if guard == "state":
            rec(h).state = "ready"
        elif guard == "decision":
            rec(h).decision += 1
        elif guard == "revision":
            rec(h).revision += 1
        elif guard == "head":
            rec(h).head_commit = "other"
        elif guard == "generation":
            ctx.assessment.waits["change-1"] = replace(wait(h))
        elif guard == "close":
            ctx.state.closing = True
        elif guard == "resolution":
            rec(h).resolution = ChangeResolved(
                change_id="change-1",
                agent_id="agent-1",
                revision=1,
                decision="rejected",
                reason=None,
                comment=None,
                merge=None,
                merge_commit=None,
                conflict=None,
            )
        else:
            reservation.wait.terminal_seq = 999
        await _rules.terminal(ctx, reservation, "cancelled", "Late callback")
        assert terminals(h) == []
    await task
    assert terminals(h) == []


async def test_lost_take_consumes_nothing(h: Harness, armed, monkeypatch):
    def person_won(*args, **kwargs):
        raise WrongState("A person already took this change")

    monkeypatch.setattr(h.manager, "_take", person_won)
    h.manager.run_ended("agent-1", None)
    await settled(h)
    assert not armed[1] and not rec(h).rules_consumed
    assert rec(h).state == "ready" and rec(h).evidence is None


async def test_new_revision_gets_its_own_attempt(h: Harness, armed):
    await (await start(h, armed))
    rec(h).state = "working"
    await h.edit(1, EDIT[0], "return 3", "return 4")
    assert await h.manager.proposed("agent-1", "Next revision")
    h.manager.run_ended("agent-1", None)
    await settled(h)
    assert len(armed[1]) == 2
    await armed[1][1]
    assert rec(h).revision == 2 and rec(h).rules_consumed == {1, 2}
