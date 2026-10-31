"""Person admission at deterministic rules boundaries, using real Git and event receipts."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
import test_rules_transaction as tx_tests
import test_spend_evidence as spend_tests
from changes_helpers import SERVER_EVENT, assert_main_clean, bounded, queued_callbacks_ran
from test_assessment import head, rec, settled, terminals, wait
from test_mode import select
from test_rules_reservation import start

from oversight.agents import LedgerState
from oversight.changes import (
    ChangeManager,
    WrongState,
    _candidate,
    _recovery,
    _rules,
    _spend,
    _transaction,
)
from oversight.changes._files import check_editable
from oversight.changes._state import APPROVABLE, ChangeState
from oversight.changes._support import RulesFailure
from oversight_contracts.events import ChangeResolved, ErrorReport
from oversight_contracts.policy import ChangeAssessment
from oversight_contracts.review import ReviewCard

pytestmark = pytest.mark.anyio
reserved, transaction = tx_tests.reserved, tx_tests.transaction
spend = spend_tests.spend
COMMENT = "Please keep this exact comment."
T1 = "change-1: rules authorization has started; wait for its result; if merged, use Revert"
T2 = "change-1 was merged by rules; use Revert to undo it"
T3 = "change-1: a decision is already being applied; wait for its result"


def person(h, decision="reject", **kwargs):
    return getattr(h.manager, decision)(
        "change-1",
        COMMENT,
        revision=kwargs.pop("revision", 1),
        head_commit=kwargs.pop("head_commit", head(h)),
        **kwargs,
    )


def refused(h, decision, text):
    with pytest.raises(WrongState) as error:
        person(h, decision)
    assert str(error.value) == text


@pytest.fixture
async def held_suite(h, reserved, transaction, monkeypatch):
    entered, release, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()
    suite = h.workspace.run_tests

    async def held(*args, **kwargs):
        entered.set()
        try:
            await release.wait()
            return await suite(*args, **kwargs)
        finally:
            cleaned.set()

    monkeypatch.setattr(h.workspace, "run_tests", held)
    task = await start(h, reserved)
    await entered.wait()
    yield task, release, cleaned
    release.set()


def verify_abort(h, monkeypatch, *, hold=False, fail=False):
    entered, release = asyncio.Event(), asyncio.Event()
    original = _recovery.abort_rules
    captured = rec(h).evidence, rec(h).taken, rec(h).decision

    async def abort(ctx, record, pre, tree):
        assert (record.evidence, record.taken, record.decision) == captured
        assert record.evidence is captured[0] and record.evidence.actor == "rules"
        assert record.evidence.head_commit == head(h)
        entered.set()
        if hold:
            await release.wait()
        if fail:
            raise RuntimeError("Scripted abort verification failure")
        await original(ctx, record, pre, tree)
        await assert_main_clean(h)
        assert await h.main_head() == h.base_commit

    spy = AsyncMock(side_effect=abort)
    monkeypatch.setattr(_recovery, "abort_rules", spy)
    return entered, release, spy


@bounded
async def test_person_reject_during_suite_before_authorization(h, held_suite, monkeypatch):
    rules_task, _, cleaned = held_suite
    _, _, abort = verify_abort(h, monkeypatch)
    h.hold = ChangeResolved
    calls = len(h.workspace.calls)
    task = person(h)
    assert not task.done() and rules_task.cancelling() == 1
    await h.held.wait()
    assert rules_task.done() and cleaned.is_set()
    abort.assert_awaited_once()
    assert not rec(h).cleaned and rec(h).tree.root.exists()
    h.release.set()
    await task
    [resolved] = h.payloads(ChangeResolved)
    assert resolved.decision == "rejected" and resolved.decided_by == "person"
    assert rec(h).state == "closed" and rec(h).cleaned and rec(h).decision == 2
    assert rec(h).person_intent is None and not terminals(h)
    assert not [e for e in h.payloads(ErrorReport)]
    assert await h.main_head() == h.base_commit
    assert not any(c.tree == "main" and c.args[0] == "commit" for c in h.workspace.calls[calls:])


@bounded
async def test_person_request_changes_during_suite_before_authorization(h, held_suite, monkeypatch):
    rules_task, _, _ = held_suite
    entered, release, abort = verify_abort(h, monkeypatch, hold=True)
    task = person(h, "request_changes")
    await entered.wait()
    assert not task.done() and not h.payloads(ChangeResolved)
    release.set()
    result = await task
    assert result is not None and result.comment.split("\n\n")[0] == COMMENT
    assert rules_task.cancelled() and rec(h).requests == 1
    abort.assert_awaited_once()
    [resolved] = h.payloads(ChangeResolved)
    assert resolved.comment == COMMENT and resolved.decision == "changes_requested"
    assert rec(h).state == "working" and not rec(h).cleaned and rec(h).tree.root.exists()
    assert not h.payloads(ErrorReport)


async def authorization_gate(h, reserved, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    ready = _transaction.Transaction.ready

    async def held(tx):
        if rec(h).rules_phase == "authorizing" and not entered.is_set():
            entered.set()
            await release.wait()
        await ready(tx)

    monkeypatch.setattr(_transaction.Transaction, "ready", held)
    task = await start(h, reserved)
    await entered.wait()
    return task, release


@pytest.mark.parametrize("failure", [False, True])
@bounded
async def test_person_reject_after_authorization_starts(
    h, reserved, transaction, monkeypatch, failure
):
    task, release = await authorization_gate(h, reserved, monkeypatch)
    refused(h, "reject", T1)
    assert task.cancelling() == 0 and rec(h).person_intent is None
    if failure:
        h.workspace.arm("commit")
    release.set()
    await task
    assert rec(h).merged is not failure
    if failure:
        assert rec(h).state == "ready"
        await person(h)
        assert rec(h).state == "closed"


@bounded
async def test_person_request_changes_after_authorization_starts(
    h, reserved, transaction, monkeypatch
):
    task, release = await authorization_gate(h, reserved, monkeypatch)
    refused(h, "request_changes", T1)
    assert task.cancelling() == 0 and rec(h).requests == 0
    release.set()
    await task
    assert rec(h).merged and not rec(h).requests


@bounded
async def test_person_reject_after_final_barrier_and_after_commit_names_revert(
    h, reserved, transaction
):
    h.workspace.arm("commit", mode="hold")
    task = await start(h, reserved)
    await h.workspace.entered.wait()
    refused(h, "reject", T1)
    refused(h, "request_changes", T1)
    assert task.cancelling() == 0
    h.workspace.resume.set()
    await task
    calls = len(h.workspace.calls)
    refused(h, "reject", T2)
    refused(h, "request_changes", T2)
    assert len(h.workspace.calls) == calls
    [resolved] = h.payloads(ChangeResolved)
    assert resolved.decided_by == "rules" and resolved.decision == "approved"
    log = (await h.main_git("show", "-s", "--format=%B%n%an <%ae>")).stdout
    assert "Merged-by: rules" in log and "rules <rules@oversight.invalid>" in log


@bounded
async def test_disable_still_wins_after_person_cutoff_before_final_guard(
    h, reserved, transaction, monkeypatch
):
    task, release = await authorization_gate(h, reserved, monkeypatch)
    refused(h, "reject", T1)
    h.manager._ctx.mode._timeout_s = 0.02
    with pytest.raises(WrongState, match="timed out"):
        await select(h, "ask_me", 1)
    release.set()
    await task
    assert not rec(h).merged and await h.main_head() == h.base_commit
    assert terminals(h) and not h.payloads(ChangeResolved)


@pytest.mark.parametrize("lock", ["main", "mode"])
@bounded
async def test_preemption_while_rules_waits_for_main_or_mode(
    h, reserved, transaction, monkeypatch, lock
):
    if lock == "main":
        async with h.workspace.locks(main=True):
            rules_task = await start(h, reserved)
            task = person(h)
            await queued_callbacks_ran()
            assert not task.done()
        await task
    else:
        entered = asyncio.Event()
        suite = h.workspace.run_tests
        held = h.manager._ctx.mode.held

        @asynccontextmanager
        async def waiting_mode():
            entered.set()
            async with held():
                yield

        async def ended(*args, **kwargs):
            result = await suite(*args, **kwargs)
            await h.manager._ctx.mode._lock.acquire()
            return result

        monkeypatch.setattr(h.workspace, "run_tests", ended)
        monkeypatch.setattr(h.manager._ctx.mode, "held", waiting_mode)
        rules_task = await start(h, reserved)
        await entered.wait()
        try:
            await queued_callbacks_ran()
            assert rec(h).rules_phase == "suite" and not rules_task.done()
            task = person(h)
            # Recovery takes no mode lock, so the person completes under this holder.
            await task
        finally:
            h.manager._ctx.mode._lock.release()
    assert rules_task.cancelled() and rec(h).state == "closed"
    assert not h.payloads(ErrorReport)
    await assert_main_clean(h)


@bounded
async def test_preemption_keeps_rules_evidence_until_abort(h, held_suite, monkeypatch):
    entered, release, abort = verify_abort(h, monkeypatch, hold=True)
    before = rec(h).evidence, rec(h).taken, rec(h).decision
    task = person(h)
    await entered.wait()
    assert (rec(h).evidence, rec(h).taken, rec(h).decision) == before
    assert not _rules.current(
        h.manager._ctx, _rules.RulesReservation(wait(h), before[0], before[2])
    )
    release.set()
    await task
    abort.assert_awaited_once()
    assert rec(h).evidence.actor == "person" and rec(h).decision == before[2] + 1


@bounded
async def test_old_done_callback_cannot_restore_person_decision(h, reserved, monkeypatch):
    original = ChangeManager._decide.__get__(h.manager)
    reject = h.manager._reject
    accepted = []
    entered, release = asyncio.Event(), asyncio.Event()

    async def held_reject(record, comment):
        entered.set()
        await release.wait()
        await reject(record, comment)

    def decide(record, work):
        task = original(record, work)
        if record.evidence.actor == "rules":
            record.rules_task, record.rules_phase = task, "queued"
            accepted.append(person(h))  # cancel before the task's first instruction
        return task

    monkeypatch.setattr(h.manager, "_decide", decide)
    monkeypatch.setattr(h.manager, "_reject", held_reject)
    h.hold = ChangeResolved
    h.manager.run_ended("agent-1", None)
    await settled(h)
    await entered.wait()
    assert rec(h).state == "deciding" and rec(h).decision == 2
    h.manager._ctx.state.restore(rec(h), 1)
    assert rec(h).state == "deciding" and rec(h).decision == 2
    release.set()
    await h.held.wait()
    h.release.set()
    await accepted[0]
    assert rec(h).state == "closed" and not h.payloads(ErrorReport)


@bounded
async def test_two_person_intents_and_stale_or_model_commands(h, held_suite, monkeypatch):
    rules_task = held_suite[0]
    for kwargs in [{"revision": 0}, {"head_commit": "stale"}]:
        with pytest.raises(WrongState, match="target changed"):
            person(h, **kwargs)
    with pytest.raises(ValueError):
        person(
            h,
            answers=[
                ReviewCard(
                    node_id="unknown",
                    question="Unknown?",
                    example=None,
                    evidence_seq=1,
                    answer="yes",
                    answered_by="person",
                )
            ],
        )
    assert rules_task.cancelling() == 0 and rec(h).person_intent is None
    with pytest.raises(WrongState, match=r"deciding.*refresh and review"):
        person(h, "approve")
    with pytest.raises(WrongState, match=r"deciding.*stop needs"):
        h.manager.stop("agent-1", None)
    with pytest.raises(WrongState, match="cross-review request needs"):
        h.manager._take("change-1", APPROVABLE, "cross-review request", person=False)
    entered, release, _ = verify_abort(h, monkeypatch, hold=True)
    task = person(h)
    refused(h, "reject", T3)
    refused(h, "request_changes", T3)
    await entered.wait()
    refused(h, "reject", T3)
    assert rules_task.cancelling() == 1
    release.set()
    await task
    assert len(h.payloads(ChangeResolved)) == 1


@pytest.mark.parametrize("protected_hit", [False, True])
@bounded
async def test_preemption_beats_path_measurement(
    h, reserved, transaction, monkeypatch, protected_hit
):
    entered = asyncio.Event()
    measure = _candidate.measure
    abort, rules_task = None, None

    async def held(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    if protected_hit:

        async def hit(*args, **kwargs):
            result = await measure(*args, **kwargs)
            return replace(
                result,
                rules=tuple(
                    r.model_copy(
                        update={"result": "fail", "observed": {"violations": 1, "complete": True}}
                    )
                    if r.rule == "protected_paths"
                    else r
                    for r in result.rules
                ),
            )

        monkeypatch.setattr(_candidate, "measure", hit)
        await (await start(h, reserved))
        assert rec(h).state == "ready" and rec(h).rules_task is None
        task = person(h)
        assert rec(h).person_intent is None
    else:
        monkeypatch.setattr(_candidate, "measure", held)
        rules_task = await start(h, reserved)
        await entered.wait()
        _, _, abort = verify_abort(h, monkeypatch)
        task = person(h)
    await task
    if not protected_hit:
        assert abort is not None and rules_task is not None
        abort.assert_awaited_once()
        assert rules_task.cancelled() and len(h.payloads(ChangeAssessment)) == 1
    assert rec(h).state == "closed" and rec(h).person_acted
    assert not h.payloads(ErrorReport)


@pytest.mark.parametrize("case", ["disconnect", "abort_failure", "timeout", "shutdown"])
@bounded
async def test_preemption_disconnect_timeout_abort_failure_shutdown_restart(
    h, held_suite, monkeypatch, case
):
    entered, release, _ = verify_abort(h, monkeypatch, hold=True, fail=case == "abort_failure")
    task = person(h, "request_changes")
    await entered.wait()
    if case == "disconnect":

        async def revise():
            return await task

        caller = asyncio.create_task(revise())
        await queued_callbacks_ran()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
    if case == "timeout":
        h.workspace.arm("merge", mode="timeout")
    close = asyncio.create_task(h.manager.aclose()) if case == "shutdown" else None
    release.set()
    if not task.cancelled():
        result = await task
        assert (result is None) is (case in {"abort_failure", "timeout"})
    if close:
        await close
    await asyncio.gather(*h.manager._ctx.state.tasks, return_exceptions=True)
    if case in {"abort_failure", "timeout"}:
        assert rec(h).state == "broken" and h.manager.main_suspect
        assert not rec(h).cleaned and rec(h).requests == 0 and not h.payloads(ChangeResolved)
        [error] = [e for e in h.payloads(ErrorReport) if "did not run:" in e.message]
        assert error.message.startswith("Request changes of change-1 did not run: ")
        assert not error.fatal and error.source == "server" and terminals(h)
        await person(h)  # ordinary Reject of broken is still permitted
    else:
        assert rec(h).state == "working" and rec(h).requests == 1
        assert len(h.payloads(ChangeResolved)) == 1
    restarted = ChangeManager(
        h.workspace, h.publish, main_graph=h.main_graph, main_snapshot=h.manager.snapshot
    )
    assert restarted.merge_mode().mode == "ask_me" and not restarted._ctx.state.changes
    await restarted.aclose()


@pytest.mark.parametrize("loss", ["ledger", "unknown"])
@bounded
async def test_preemption_preserves_fresh_spend_barriers(h, spend, monkeypatch, loss):
    tx_tests.configure_transaction(h, monkeypatch)
    entered = asyncio.Event()
    ready = AsyncMock(wraps=_spend.ready)
    monkeypatch.setattr(_spend, "ready", ready)

    async def held(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(h.workspace, "run_tests", held)
    rules_task = await start(h, (spend.clock, spend.decisions))
    await entered.wait()
    assert ready.await_count == 4  # scheduler, two preflight reads, candidate readiness
    if loss == "ledger":
        spend.owner._ledger_lost = True
    else:
        spend.ledger.state = LedgerState(0, (spend_tests.UNKNOWN,))
    with pytest.raises(RulesFailure, match="Current Meta spend"):
        _spend.guard(spend.ctx, spend.ctx.assessment.inputs(wait(h)))
    await person(h)
    assert rules_task.cancelled() and ready.await_count == 4
    assert rec(h).state == "closed" and await h.main_head() == h.base_commit
    assert not h.payloads(ErrorReport)


@bounded
async def test_no_admission_between_rules_restore_and_person_take(h, held_suite, monkeypatch):
    restore = ChangeState.restore
    checked = []

    def restored(state, record, decision):
        restore(state, record, decision)
        if record.person_intent is not None and record.state == "ready":
            checked.append(True)
            refused(h, "approve", T3)
            with pytest.raises(WrongState, match=f"^{T3}$"):
                h.manager.stop("agent-1", None)
            with pytest.raises(WrongState):
                check_editable(h.manager._ctx, record)
            assert not h.manager._ctx.assessment.valid(wait(h))
            with pytest.raises(WrongState):
                h.manager._take("change-1", APPROVABLE, "cross-review request", person=False)

    monkeypatch.setattr(ChangeState, "restore", restored)
    await person(h)
    assert checked and rec(h).state == "closed" and rec(h).decision == 2


@bounded
async def test_stop_with_pending_intent_after_request_changes_uses_t3(h, held_suite, monkeypatch):
    restore, checked = ChangeState.restore, []

    def restored(state, record, decision):
        restore(state, record, decision)
        if record.person_intent is not None and record.state == "working":
            checked.append(True)
            with pytest.raises(WrongState) as error:
                h.manager.stop("agent-1", None)
            assert str(error.value) == T3

    monkeypatch.setattr(ChangeState, "restore", restored)
    await person(h, "request_changes")
    assert checked and rec(h).state == "working" and rec(h).requests == 1
    assert len(h.payloads(ChangeResolved)) == 1 and not h.payloads(ErrorReport)


def coordinator(h):
    return next(t for t in h.manager._ctx.state.tasks if t.get_coro().__name__ == "finish")


@pytest.mark.parametrize("decision", ["reject", "request_changes"])
@bounded
async def test_coordinator_cancel_waits_for_ordinary_decision(h, held_suite, decision):
    h.hold = ChangeResolved
    task = person(h, decision)
    finishing = coordinator(h)
    await h.held.wait()
    try:
        for _ in range(2):
            finishing.cancel()
            await queued_callbacks_ran()
            assert not finishing.done() and rec(h).person_intent is not None
            assert not h.payloads(ErrorReport)
    finally:
        h.release.set()
        await task
    assert rec(h).person_intent is None and len(h.payloads(ChangeResolved)) == 1
    assert not h.payloads(ErrorReport)


@bounded
async def test_coordinator_clears_intent_before_cancellable_error_publish(
    h, held_suite, monkeypatch
):
    abort_entered, abort_release, _ = verify_abort(h, monkeypatch, hold=True)
    entered, release = asyncio.Event(), asyncio.Event()
    error = h.manager._ctx.error

    async def held_error(source, message, record, *, fatal):
        if "did not run:" in message:
            entered.set()
            await release.wait()
        return await error(source, message, record, fatal=fatal)

    monkeypatch.setattr(h.manager._ctx, "error", held_error)
    task = person(h)
    finishing = coordinator(h)
    await abort_entered.wait()
    finishing.cancel()
    await entered.wait()
    try:
        assert rec(h).person_intent is None
    finally:
        finishing.cancel()
        release.set()
        abort_release.set()
        await asyncio.gather(task, finishing, held_suite[0], return_exceptions=True)
    assert rec(h).person_intent is None


@pytest.mark.parametrize(
    ("decision", "failure"),
    [("reject", "target"), ("request_changes", "target"), ("request_changes", "suspect")],
)
@bounded
async def test_ordinary_failure_reports_one_error(h, held_suite, monkeypatch, decision, failure):
    original = getattr(h.manager, f"_{decision}")

    async def failed(record, comment):
        if failure == "suspect":
            h.manager._ctx.state.suspect = True
        else:
            record.revision += 1
        return await original(record, comment)

    monkeypatch.setattr(h.manager, f"_{decision}", failed)
    assert await person(h, decision) is None
    [error] = h.payloads(ErrorReport)
    assert error.source == "server" and error.change_id == "change-1" and not error.fatal
    assert rec(h).person_intent is None and rec(h).requests == 0 and not rec(h).cleaned
    assert not h.payloads(ChangeResolved)
    for stored in h.log:
        SERVER_EVENT.validate_json(stored.envelope)


@pytest.mark.parametrize("phase", ["measurement", "suite", "authorization"])
@bounded
async def test_settling_refusal_only_names_authorization_if_entered(
    h, reserved, transaction, monkeypatch, phase
):
    entered, release = asyncio.Event(), asyncio.Event()
    abort = _recovery.abort_rules

    async def held_abort(*args, **kwargs):
        entered.set()
        await release.wait()
        await abort(*args, **kwargs)

    monkeypatch.setattr(_recovery, "abort_rules", held_abort)
    failure = AsyncMock(side_effect=RulesFailure("suite_failed", "Scripted early failure"))
    if phase == "measurement":
        monkeypatch.setattr(_candidate, "measure", failure)
    elif phase == "suite":
        monkeypatch.setattr(h.workspace, "run_tests", failure)
    else:
        ready = _transaction.Transaction.ready

        async def failed(tx):
            if rec(h).rules_phase == "authorizing":
                await failure()
            await ready(tx)

        monkeypatch.setattr(_transaction.Transaction, "ready", failed)
    rules_task = await start(h, reserved)
    await entered.wait()
    try:
        assert rec(h).rules_phase == "settling"
        for decision in ("reject", "request_changes"):
            if phase == "authorization":
                refused(h, decision, T1)
            else:
                with pytest.raises(WrongState, match=r"deciding.*refresh and review"):
                    person(h, decision)
        assert rec(h).person_intent is None and rules_task.cancelling() == 0
    finally:
        release.set()
    await rules_task
    assert rec(h).state == "ready" and not rec(h).merged


@pytest.mark.parametrize("failure", [False, True])
@bounded
async def test_every_emitted_event_validates(h, held_suite, monkeypatch, failure):
    verify_abort(h, monkeypatch, fail=failure)
    await person(h)
    for stored in h.log:
        SERVER_EVENT.validate_json(stored.envelope)
    assert bool(terminals(h)) is failure
