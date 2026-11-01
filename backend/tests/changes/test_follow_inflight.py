"""Following stops at transaction entry; fresh candidates retain suite, paths and spend guards."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from changes_helpers import PATH, bounded, edit_main
from follow_settle_helpers import (
    closed_failure,
    drain,
    general_evidence,
    on_pending,
    peer,
    queued_move,
)
from test_assessment import rec, settled, terminals, wait
from test_rules_reservation import start
from test_rules_transaction import reserved as reserved
from test_spend_evidence import receipt, spend_rule
from test_spend_evidence import spend as spend

from oversight.agents import LedgerState
from oversight.changes import _approval, _candidate, _transaction
from oversight.changes._assessment import RulesScheduler
from oversight_contracts.events import ChangeResolved, SuiteExecution
from oversight_contracts.policy import EvidenceRef

pytestmark = pytest.mark.anyio


@bounded
async def test_main_move_requested_during_rules_suite_queues(h, reserved, monkeypatch):
    general_evidence(h, monkeypatch)
    other = await peer(h)
    admission = h.manager._ctx.ports.rules_admission
    h.manager._ctx.ports.rules_admission = lambda r, a: admission(r, a) if r is rec(h) else None
    old_peer = h.manager._ctx.assessment.waits[other.change_id]
    entered, release = asyncio.Event(), asyncio.Event()
    suite = h.workspace.run_tests

    async def held(*args, **kwargs):
        entered.set()
        await release.wait()
        return await suite(*args, **kwargs)

    monkeypatch.setattr(h.workspace, "run_tests", held)
    h.manager.run_ended("agent-1", None)
    await entered.wait()
    old = wait(h)
    pre = (h.workspace.main.root / ".git/refs/heads/main").read_text().strip()
    content, base = await edit_main(h, PATH, "return value() + value()", "return value() * 2")
    queued = asyncio.Event()
    locks = h.workspace.locks

    @asynccontextmanager
    async def waiting_main(*, main=False, agent_id=None):
        if main and agent_id is None:
            queued.set()
        async with locks(main=main, agent_id=agent_id):
            yield

    monkeypatch.setattr(h.workspace, "locks", waiting_main)
    saving = asyncio.create_task(h.manager.save_main(PATH, content, base))
    await queued.wait()
    assert not saving.done()
    assert (h.workspace.main.root / ".git/refs/heads/main").read_text().strip() == pre
    release.set()
    await drain(h, reserved[1])
    await saving
    await settled(h)
    new_peer = h.manager._ctx.assessment.waits[other.change_id]
    assert new_peer is not old_peer and new_peer.main_commit == await h.main_head()
    assert new_peer.deadline == old_peer.deadline and wait(h) is old
    assert rec(h).merged and rec(h).follow_pending is None and not terminals(h)
    assert h.payloads(ChangeResolved)[0].decided_by == "rules"


@bounded
async def test_external_main_move_during_suite_never_retries(h, reserved, monkeypatch):
    general_evidence(h, monkeypatch)
    suite = h.workspace.run_tests

    async def attacked(*args, **kwargs):
        run = await suite(*args, **kwargs)
        (h.workspace.main.root / ".git/refs/heads/main").write_text(rec(h).head_commit + "\n")
        return run

    monkeypatch.setattr(h.workspace, "run_tests", attacked)
    calls = len(h.workspace.calls)
    task = await start(h, reserved)
    await task
    old = wait(h)
    assert h.manager.main_suspect or h.workspace.integrity.rules_suspended
    h.manager._ctx.assessment.main_changed(rec(h).head_commit)
    h.manager._ctx.assessment.main_delivered(rec(h).head_commit)
    await settled(h)
    assert wait(h) is old and rec(h).follow_pending is None and len(reserved[1]) == 1
    assert not h.payloads(ChangeResolved) and len(terminals(h)) == 1
    assert not any(c.tree == "main" and c.args[0] == "commit" for c in h.workspace.calls[calls:])


@pytest.mark.parametrize("failure", ["conflict", "suite_failed", "CandidateVeto"])
async def test_follow_conflict_or_failed_suite_needs_person_once(h, reserved, monkeypatch, failure):
    await closed_failure(h, reserved, monkeypatch, failure)


async def test_follow_discards_old_combined_evidence_and_remeasures_paths(h, reserved, monkeypatch):
    old = wait(h)
    combined = h.payloads(SuiteExecution)[0].model_copy(
        update={
            "phase": "combined",
            "tested_commit": old.main_commit,
            "tested_tree": old.facts.target.candidate_tree,
        }
    )
    receipt_old = await h.publish(combined)
    h.manager.rules_received(receipt_old)
    assert RulesScheduler.inputs(h.manager._ctx.assessment, old).suite == combined.suite
    measured = []
    measure = _candidate.measure

    async def capture(ctx, guard, pending, *args, **kwargs):
        result = await measure(ctx, guard, pending, *args, **kwargs)
        measured.append((guard.pre, guard.candidate_tree, result))
        return result

    monkeypatch.setattr(_candidate, "measure", capture)
    captures = []
    wake = h.manager._ctx.assessment.wake

    def waking(record):
        current = wait(h)
        if current is not old:
            captures.append(
                (
                    current.latest,
                    current.spend_pin,
                    RulesScheduler.inputs(h.manager._ctx.assessment, current).suite,
                )
            )
        wake(record)

    monkeypatch.setattr(h.manager._ctx.assessment, "wake", waking)
    async with queued_move(h, reserved, monkeypatch):
        pass
    await drain(h, reserved[1])
    assert captures[0] == (None, None, None)
    [(post, tree, candidate)] = measured
    assert post == wait(h).main_commit != old.main_commit
    paths = next(r for r in candidate.rules if r.rule == "protected_paths")
    assert EvidenceRef(git_object=tree) in paths.evidence
    final = rec(h).evidence.assessment
    assert final.main_commit == post and final.candidate_tree == tree
    suites = [s for s in h.payloads(SuiteExecution) if s.phase == "combined"]
    assert len(suites) == 2 and suites[-1].tested_tree == tree


@pytest.mark.parametrize("expensive", [False, True])
async def test_follow_reads_fresh_spend_at_each_guard(h, spend, monkeypatch, expensive):
    general_evidence(h, monkeypatch)
    pins, guarded = [], []
    revalidate = _transaction.Transaction.revalidate

    def guarded_spend(tx):
        guarded.append(spend.ledger.state.spent_usd)
        revalidate(tx)

    monkeypatch.setattr(_transaction.Transaction, "revalidate", guarded_spend)

    approve = _approval.approve_rules

    async def second_guard(ctx, reservation):
        if pins and expensive:
            spend.ledger.state = LedgerState(0.5)
        await approve(ctx, reservation)

    monkeypatch.setattr(_approval, "approve_rules", second_guard)

    def changed(record):
        pins.append(wait(h).latest)
        spend.ledger.state = LedgerState(0.01)

    on_pending(h, monkeypatch, changed)
    async with queued_move(h, (spend.clock, spend.decisions), monkeypatch) as (old, _):
        old_pin = old.spend_pin
        new_receipt = await receipt(h, total=0)
        assert new_receipt != old_pin
    await drain(h, spend.decisions)
    assert pins and wait(h) is not old
    assert spend_rule(wait(h).latest).evidence != spend_rule(pins[0]).evidence
    if expensive:
        assert not rec(h).merged and not guarded
        assert "code=policy_invalid;" in terminals(h)[0].message
    else:
        assert rec(h).merged and guarded and set(guarded) == {0.01}
        assert not terminals(h)


async def test_queued_control_inflight_file(h, reserved, monkeypatch):
    async with queued_move(h, reserved, monkeypatch) as (old, _):
        pass
    await drain(h, reserved[1])
    assert wait(h) is not old and rec(h).merged and not terminals(h)
