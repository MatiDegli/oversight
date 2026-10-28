"""Real Git/store, scripted suite boundary and fault injection; no model or provider I/O."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
import test_rules_reservation as preflight
from changes_helpers import assert_main_clean, queued_callbacks_ran
from test_assessment import head, latest, rec, settled, terminals, wait
from test_rules_reservation import start, terminal_code

from oversight.changes import _approval, _merge, _rules, _transaction
from oversight.changes._support import RulesFailure
from oversight.events import EventRejected
from oversight.workspace import prepare_merge_guard, suite_guard, test_tree
from oversight_contracts.events import ChangeResolved
from oversight_contracts.policy import ChangeAssessment

pytestmark = pytest.mark.anyio


reserved = preflight.armed


@pytest.fixture
async def transaction(h, reserved, monkeypatch):
    ctx = h.manager._ctx
    ctx.ports.rules_transaction = lambda record, evidence: _transaction.run(ctx, record, evidence)
    policies, captures, barriers = [], [], []
    select = h.workspace._isolation.select
    capture = test_tree.capture_test_tree
    barrier = _transaction.Transaction.barriers
    lock = ctx.mode._lock

    class OrderedMode:
        async def __aenter__(self):
            assert h.workspace.operations.holds(main=True)
            assert h.workspace.operations.holds(agent_id="agent-1")
            assert h.workspace.operations._git.owner is not asyncio.current_task()
            return await lock.__aenter__()

        async def __aexit__(self, *args):
            return await lock.__aexit__(*args)

        def locked(self):
            return lock.locked()

    monkeypatch.setattr(ctx.mode, "_lock", OrderedMode())

    def isolated(policy, injected, open_runner):
        # Script the unavailable namespace adapter, not authorization or Git/suite checks.
        policies.append(policy)
        assert policy == "required"
        assert not ctx.mode._lock.locked()
        return open_runner

    async def captured(*args, **kwargs):
        assert h.workspace.operations._git.owner is asyncio.current_task()
        assert h.workspace.operations.holds(main=True)
        assert h.workspace.operations.holds(agent_id="agent-1")
        captures.append(True)
        return await capture(*args, **kwargs)

    async def barrier_ran(tx):
        assert ctx.mode._lock.locked()
        assert h.workspace.operations._git.owner is asyncio.current_task()
        assert await h.main_head() == tx.pre
        barriers.append(tx)
        await barrier(tx)

    monkeypatch.setattr(h.workspace._isolation, "select", isolated)
    monkeypatch.setattr(test_tree, "capture_test_tree", captured)
    # suite_guard binds the capture function directly.
    monkeypatch.setattr(suite_guard, "capture_test_tree", captured)
    monkeypatch.setattr(_transaction.Transaction, "barriers", barrier_ran)
    return policies, captures, barriers, select


async def execute(h, reserved):
    await (await start(h, reserved))
    await queued_callbacks_ran()


async def test_order_actor_trailer_and_no_late_or_repeated_effect(h, reserved, transaction):
    before = await h.main_head()
    await execute(h, reserved)
    policies, captures, barriers, _ = transaction
    assert policies == ["required"] and captures == [True]
    [tx] = barriers
    assert tx.barriers_ran and tx.assessment_seq is not None
    pending, final = h.payloads(ChangeAssessment)
    assert all(r.result == "pass" for r in final.rules)
    assert latest(h) == pending and rec(h).evidence.assessment == final
    [resolved] = h.payloads(ChangeResolved)
    assert resolved.decided_by == "rules" and resolved.assessment == final and resolved.reasons
    assert rec(h).state == "closed" and rec(h).cleaned
    log = (await h.main_git("show", "-s", "--format=%P%n%B%n%an <%ae>")).stdout
    assert log.splitlines()[0].split() == [before, head(h)]
    assert "Merged-by: rules" in log.splitlines() and "rules <rules@oversight.invalid>" in log
    assert (await h.main_git("rev-parse", "HEAD^{tree}")).stdout.strip() == final.candidate_tree
    after = await h.main_head()
    calls = len(h.workspace.calls)
    reserved[0].advance(121)
    await h.manager._ctx.assessment.expire(wait(h))
    await _rules.terminal(h.manager._ctx, tx.reservation, "cancelled", "Late callback")
    await _approval.approve_rules(h.manager._ctx, tx.reservation)
    h.manager._admit_rules(rec(h), pending)
    await settled(h)
    assert not terminals(h) and await h.main_head() == after
    assert not any(c.args[0] in {"merge", "commit"} for c in h.workspace.calls[calls:])


@pytest.mark.parametrize("receipt", ["before", "after"])
async def test_definitive_assessment_is_persisted_before_commit(h, reserved, transaction, receipt):
    # The provisional receipt has already been returned when this port runs.
    real = h.manager._ctx.publish

    async def publish(payload):
        if isinstance(payload, ChangeAssessment) and all(r.result == "pass" for r in payload.rules):
            assert transaction[2][0].barriers_ran
            assert await h.main_head() == h.base_commit
            if receipt == "before":
                raise RuntimeError("assessment not persisted")
            await real(payload)
            raise RuntimeError("receipt lost")
        return await real(payload)

    h.manager._ctx.publish = publish
    await execute(h, reserved)
    assert await h.main_head() == h.base_commit and rec(h).state == "ready"
    assert not h.payloads(ChangeResolved) and terminal_code(h) == "git_failed"
    await assert_main_clean(h)


async def test_required_isolation_unavailable_never_launches_or_falls_back(
    h, reserved, transaction, monkeypatch
):
    monkeypatch.setattr(h.workspace._isolation, "select", transaction[3])
    launch = AsyncMock(side_effect=AssertionError("Open suite was launched"))
    monkeypatch.setattr(suite_guard, "run_suite", launch)
    await execute(h, reserved)
    launch.assert_not_awaited()
    assert terminal_code(h) == "isolation_unavailable"
    assert rec(h).state == "ready" and await h.main_head() == h.base_commit
    assert not h.payloads(ChangeResolved)
    await assert_main_clean(h)


@pytest.mark.parametrize("fault", ["merge", "suite", "commit", "cancel", "abort"])
async def test_precommit_failures_abort_and_uncertainty_marks_suspect(
    h, reserved, transaction, monkeypatch, fault
):
    if fault in {"merge", "commit"}:
        h.workspace.arm(fault, mode="after" if fault == "merge" else "fail")
    else:
        real = h.workspace.run_tests

        async def suite(*args, **kwargs):
            await real(*args, **kwargs)
            if fault == "abort":
                h.workspace.arm("merge")
            if fault == "cancel":
                raise asyncio.CancelledError
            raise RulesFailure("suite_failed", "Scripted suite failure")

        monkeypatch.setattr(h.workspace, "run_tests", suite)
    task = await start(h, reserved)
    if fault == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        await task
    assert not h.payloads(ChangeResolved)
    if fault == "abort":
        assert h.manager.main_suspect and rec(h).state == "broken"
        assert terminal_code(h) == "main_suspect"
    else:
        assert rec(h).state == "ready" and not h.manager.main_suspect
        assert terminal_code(h) == (
            "cancelled"
            if fault == "cancel"
            else "suite_failed"
            if fault == "suite"
            else "git_failed"
        )
        assert await h.main_head() == h.base_commit
        await assert_main_clean(h)


@pytest.mark.parametrize("fault", ["after", "cancel"])
async def test_commit_recovery_verifies_and_records_without_late_error(
    h, reserved, transaction, fault
):
    h.workspace.arm("commit", mode=fault)
    task = await start(h, reserved)
    if fault == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await task
        await h.manager._ctx.assessment.expire(wait(h))
        await h.manager._ctx.ports.drain(rec(h))
    else:
        await task
    assert rec(h).merged and rec(h).resolution.decided_by == "rules"
    assert not terminals(h) and not h.manager.main_suspect
    assert (
        len(
            [
                c
                for c in h.workspace.calls
                if c.tree == "main" and c.args[0] == "commit" and "Initial workspace" not in c.args
            ]
        )
        == 1
    )


async def test_rejected_delivery_stays_failed_and_never_repeats(h, reserved, transaction):
    real = h.manager._ctx.publish

    async def publish(payload):
        if isinstance(payload, ChangeResolved):
            raise EventRejected("Scripted resolution rejection")
        return await real(payload)

    h.manager._ctx.publish = publish
    await execute(h, reserved)
    assert rec(h).state == "delivery_failed" and rec(h).merged
    after = await h.main_head()
    await _approval.approve_rules(h.manager._ctx, transaction[2][0].reservation)
    reserved[0].advance(121)
    await h.manager._ctx.assessment.expire(wait(h))
    await h.manager._ctx.ports.drain(rec(h))
    assert rec(h).state == "delivery_failed" and await h.main_head() == after
    assert (
        len(
            [
                c
                for c in h.workspace.calls
                if c.tree == "main" and c.args[0] == "commit" and "Initial workspace" not in c.args
            ]
        )
        == 1
    )
    assert not terminals(h)


@pytest.mark.parametrize(
    "guard", ["reservation", "target", "head", "tree", "main", "evidence", "receipt", "mode"]
)
async def test_each_authorization_is_rechecked_after_persistence(
    h, reserved, transaction, monkeypatch, guard
):
    real = _transaction.Transaction.persist
    ctx = h.manager._ctx

    async def persist(tx, merge_guard, run):
        await real(tx, merge_guard, run)
        if guard == "reservation":
            rec(h).taken = 0
        elif guard == "target":
            rec(h).base_commit = "other"
        elif guard == "evidence":
            rec(h).evidence = replace(rec(h).evidence, reasons=("forged",))
        elif guard == "receipt":
            seq = next(seq for seq, e in rec(h).executions.items() if e.phase == "combined")
            del ctx.assessment.events[seq]
        elif guard == "mode":
            ctx.mode._suspended = True
        else:
            git = ctx.git
            once = True

            async def moved(tree, *args, **kwargs):
                nonlocal once
                expected = ("rev-parse", "HEAD^{tree}" if guard == "tree" else "HEAD")
                target = h.workspace.main if guard == "main" else rec(h).tree
                if once and tree is target and args == expected:
                    once = False
                    return "other"
                return await git(tree, *args, **kwargs)

            monkeypatch.setattr(ctx, "git", moved)

    monkeypatch.setattr(_transaction.Transaction, "persist", persist)
    await execute(h, reserved)
    assert not h.payloads(ChangeResolved) and await h.main_head() == h.base_commit
    assert rec(h).state == "ready"
    assert (
        terminal_code(h)
        == {
            "reservation": "target_changed",
            "target": "target_changed",
            "head": "target_changed",
            "tree": "worktree_unverifiable",
            "main": "main_moved",
            "evidence": "policy_invalid",
            "receipt": "policy_invalid",
            "mode": "policy_invalid",
        }[guard]
    )
    await assert_main_clean(h)


@pytest.mark.parametrize("fact", ["parents", "tree", "commit", "actor"])
async def test_recovery_rejects_each_mismatched_commit_fact(
    h, reserved, transaction, monkeypatch, fact
):
    h.workspace.arm("commit", mode="after")
    ctx, git = h.manager._ctx, h.manager._ctx.git

    async def altered(tree, *args, **kwargs):
        if tree is h.workspace.main:
            query = {
                "parents": ("show", "-s", "--format=%P", "HEAD"),
                "tree": ("rev-parse", "HEAD^{tree}"),
                "commit": ("show", "-s", "--format=%B", "HEAD"),
                "actor": ("show", "-s", "--format=%an <%ae>", "HEAD"),
            }[fact]
            if args == query and transaction[2]:
                return "Unrelated merge\n\nMerged-by: rules" if fact == "commit" else "other"
        return await git(tree, *args, **kwargs)

    monkeypatch.setattr(ctx, "git", altered)
    await execute(h, reserved)
    assert h.manager.main_suspect and rec(h).state == "broken"
    assert terminal_code(h) == "main_suspect" and not h.payloads(ChangeResolved)


@pytest.mark.parametrize("fact", ["head", "index", "tree"])
async def test_abort_verifies_each_original_fact(h, reserved, transaction, monkeypatch, fact):
    from oversight.changes import _recovery

    real = _recovery.verify_rules_abort
    git = h.manager._ctx.git
    seen = 0

    async def verify(ctx, record, pre, tree):
        nonlocal seen
        seen += 1
        if seen > 1:
            query = {
                "head": ("rev-parse", "HEAD"),
                "index": ("write-tree",),
                "tree": ("rev-parse", "HEAD^{tree}"),
            }[fact]

            async def altered(target, *args, **kwargs):
                if target is h.workspace.main and args == query:
                    return "other"
                return await git(target, *args, **kwargs)

            monkeypatch.setattr(ctx, "git", altered)
        await real(ctx, record, pre, tree)

    monkeypatch.setattr(_recovery, "verify_rules_abort", verify)
    h.workspace.arm("commit")
    await execute(h, reserved)
    assert h.manager.main_suspect and terminal_code(h) == "main_suspect"
    assert rec(h).state == "broken" and not h.payloads(ChangeResolved)


async def test_admission_catches_wrong_state_and_documents_unexpected_bucket(
    h, reserved, monkeypatch
):
    from oversight.changes import WrongState, _rules

    def raced(*args):
        raise WrongState("Target changed during capture")

    h.manager._ctx.ports.rules_admission = lambda *args: None
    h.manager.run_ended("agent-1", None)
    await settled(h)
    monkeypatch.setattr(_rules, "capture", raced)
    assert h.manager._admit_rules(rec(h), latest(h)) == "Target changed during capture"
    assert rec(h).state == "ready" and not rec(h).rules_consumed
    assert _approval.approve_rules.__doc__ is not None
    assert "unexpected" in _approval.approve_rules.__doc__
    assert "not a diagnosis" in _approval.approve_rules.__doc__


@pytest.mark.parametrize("outcome", ["failed", "timeout", "skipped", "incomplete"])
async def test_suite_rejects_nonpassing_or_incomplete_evidence(
    h, reserved, transaction, monkeypatch, outcome
):
    real = h.workspace.run_tests

    async def suite(*args, **kwargs):
        run = await real(*args, **kwargs)
        if outcome == "incomplete":
            return replace(run, problems=("missing JUnit case",))
        updates = {"skipped": 1} if outcome == "skipped" else {"outcome": outcome}
        return replace(run, result=run.result.model_copy(update=updates))

    monkeypatch.setattr(h.workspace, "run_tests", suite)
    await execute(h, reserved)
    assert await h.main_head() == h.base_commit and rec(h).state == "ready"
    assert terminal_code(h) == "suite_failed" and not h.payloads(ChangeResolved)
    await assert_main_clean(h)


@pytest.mark.parametrize("empty", ["already_merged", "same_tree"])
async def test_rules_requires_a_nonempty_prepared_combination(
    h, reserved, transaction, monkeypatch, empty
):
    if empty == "already_merged":
        run = h.manager._ctx.run

        async def no_merge(tree, *args):
            if args[0] == "merge":
                return await run(tree, "status", "--porcelain")
            return await run(tree, *args)

        monkeypatch.setattr(h.manager._ctx, "run", no_merge)
    else:
        prepare = prepare_merge_guard

        async def unchanged(*args, **kwargs):
            guard = await prepare(*args, **kwargs)
            return replace(
                guard, candidate_tree=(await h.main_git("rev-parse", "HEAD^{tree}")).stdout.strip()
            )

        monkeypatch.setattr(_merge, "prepare_merge_guard", unchanged)
    await execute(h, reserved)
    assert transaction[0] == [] and not h.payloads(ChangeResolved)
    assert terminal_code(h) == "policy_invalid" and rec(h).state == "ready"
    assert await h.main_head() == h.base_commit
    await assert_main_clean(h)


async def test_lock_order_and_capture_lease_without_holding_mode_during_suite(
    h, reserved, transaction, monkeypatch
):
    monkeypatch.setattr(
        _transaction.Transaction,
        "persist",
        AsyncMock(side_effect=RulesFailure("policy_invalid", "Lock checks complete")),
    )
    await execute(h, reserved)
    assert transaction[0] == ["required"] and transaction[1] == [True]
    assert transaction[2][0].barriers_ran
    assert terminal_code(h) == "policy_invalid" and await h.main_head() == h.base_commit
    await assert_main_clean(h)
