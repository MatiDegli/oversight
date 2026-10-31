"""Evidence arrives after commit; terminal recognition is target and generation scoped."""

import asyncio
from dataclasses import replace

import pytest
from changes_helpers import EDIT, Harness
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

from oversight.changes._facts import build_change_facts
from oversight.events import format_needs_you, read_store
from oversight_contracts.events import ChangeProposed, ChangeResolved, ErrorReport, SuiteExecution
from oversight_contracts.policy import EvidenceRef
from oversight_contracts.review import AddedTestProbe, ChangeFacts

pytestmark = pytest.mark.anyio


@pytest.fixture
def clock(h, monkeypatch):
    return make_clock(h, monkeypatch)


async def test_combined_receipt_and_duplicate_wakes(h: Harness, clock):
    available(h)
    await select(h, "merge_when_safe", 0)
    await h.run_agent(1, EDIT)
    await settled(h)
    before = clock.calls.call_count
    suite = h.payloads(SuiteExecution)[0].model_copy(
        update={"phase": "combined", "tested_commit": h.manager.snapshot.commit}
    )
    receipt = await h.publish(suite)
    h.manager.rules_received(receipt)
    await settled(h)
    assert clock.calls.call_count == before + 1
    assert next(r for r in latest(h).rules if r.rule == "combined_tests").result == "pass"
    h.manager.rules_received(receipt)
    await settled(h)
    assert clock.calls.call_count == before + 1


async def test_uncommitted_facts_never_appear_in_assessment(h: Harness, clock):
    available(h)
    await select(h, "merge_when_safe", 0)
    h.gate = ChangeFacts
    proposal = asyncio.create_task(propose(h))
    await h.at_gate.wait()
    try:
        h.manager.run_ended("agent-1", None)
        await settled(h)
        assert next(r for r in latest(h).rules if r.rule == "test_integrity").result == "unknown"
        assert h.payloads(ChangeFacts) == []
    finally:
        h.open_gate.set()
        await proposal
    await settled(h)
    assert next(r for r in latest(h).rules if r.rule == "test_integrity").result == "pass"
    for event in h.events():
        if event.type == "change.assessed":
            assert all(
                ref.event_seq is None or ref.event_seq < event.seq
                for rule in event.payload.rules
                for ref in rule.evidence
            )
    calls = clock.calls.call_count
    evidence = wait(h).facts
    assert evidence is not None
    receipt = await h.publish(build_change_facts(evidence))
    h.manager.rules_received(receipt)
    await settled(h)
    assert clock.calls.call_count == calls + 1 and wait(h).deadline == 120


async def test_uncommitted_references_are_unknown(h: Harness, clock):
    available(h)
    await select(h, "merge_when_safe", 0)
    full_evidence(
        h,
        policy_inputs(),
        lambda inputs: replace(
            inputs, evidence={rule: (EvidenceRef(event_seq=99999),) for rule in inputs.evidence}
        ),
    )
    await h.run_agent(1, EDIT)
    await settled(h)
    assert all(
        rule.result == "unknown" and not rule.evidence
        for rule in latest(h).rules
        if rule.rule not in {"known_spend", "protected_paths"}
    )
    assert next(r for r in latest(h).rules if r.rule == "known_spend").result == "pass"
    # Protected paths own two separate confirmed sources; generic refs cannot replace them.
    paths = next(r for r in latest(h).rules if r.rule == "protected_paths")
    assert paths.result == "pass"
    assert paths.evidence == [
        EvidenceRef(event_seq=h.manager._ctx.assessment.run_started_seq),
        EvidenceRef(event_seq=wait(h).proposal_seq),
    ]
    assert terminals(h) == []


async def test_added_probe_veto_cannot_be_erased_by_late_clean_review(h: Harness, clock):
    available(h)
    await select(h, "merge_when_safe", 0)
    await propose(h)
    first = next(e for e in h.events() if e.type == "tests.executed")
    second = await h.publish(first.payload)
    h.manager.rules_received(second)
    full_evidence(
        h,
        policy_inputs(),
        lambda inputs: replace(
            inputs,
            added_tests=(
                AddedTestProbe(
                    test_id="tests/test_new.py::test_a",
                    before_seq=first.seq,
                    after_seq=second.seq,
                    result="fails_after",
                ),
            ),
        ),
    )
    h.manager.run_ended("agent-1", None)
    await settled(h)
    assert next(r for r in latest(h).rules if r.rule == "added_tests_fail_before").result == "fail"
    calls = clock.calls.call_count
    complete_review = policy_inputs().review
    assert complete_review is not None
    review = complete_review.model_copy(
        update=dict(revision=1, base_commit=rec(h).base_commit, head_commit=head(h))
    )
    receipt = await h.publish(review)
    h.manager.rules_received(receipt)
    await settled(h)
    assert clock.calls.call_count == calls and terminals(h) == []


async def test_unrelated_terminal_or_generation_does_not_end_wait(h: Harness, clock):
    available(h)
    await select(h, "merge_when_safe", 0)
    await h.run_agent(1, EDIT)
    await settled(h)
    current = wait(h)
    for change, revision, generation in (
        ("change-2", 1, current.target.generation),
        ("change-1", 2, current.target.generation),
        ("change-1", 1, current.target.generation - 1),
    ):
        receipt = await h.publish(
            ErrorReport(
                source="server",
                change_id=change,
                agent_id="agent-1",
                fatal=False,
                message=format_needs_you(revision, head(h), "ledger", "Historical diagnostic."),
            )
        )
        h.manager.rules_received(receipt, generation=generation)
        assert current.active and current.terminal_seq is None
    # The same-target historical receipt still wins store arbitration; expiry acknowledges it.
    clock.advance(120)
    assert current.timer is not None
    await current.timer
    assert current.terminal_seq is not None
    assert sum(row.type == "error" for row in read_store(h.store.path)) == 3


async def test_resolution_receipt_cancels_without_admission(h: Harness, clock):
    available(h)
    await select(h, "merge_when_safe", 0)
    await h.run_agent(1, EDIT)
    await settled(h)
    resolved = ChangeResolved(
        change_id="change-1",
        agent_id="agent-1",
        revision=1,
        head_commit=head(h),
        decision="rejected",
        decided_by="person",
        reason=None,
        comment=None,
        merge=None,
        merge_commit=None,
        conflict=None,
    )
    stored = await h.publish(resolved)
    h.manager.rules_received(stored)
    await settled(h)
    pending_timer = wait(h).timer
    assert pending_timer is not None and not wait(h).active and pending_timer.cancelled()


async def test_suspended_proposal_never_arms_after_confirmation(h: Harness, clock):
    available(h)
    await select(h, "merge_when_safe", 0)
    h.manager._ctx.mode._suspended = True
    await h.run_agent(1, EDIT)
    h.manager._ctx.mode._suspended = False
    h.manager._ctx.assessment.wake(rec(h))
    await settled(h)
    assert clock.calls.call_count == 0 and clock.registrations == []
    assert "change-1" not in h.manager._ctx.assessment.waits


async def test_mode_is_captured_before_proposal_receipt(h: Harness, clock):
    available(h)
    captured = await select(h, "merge_when_safe", 0)
    h.gate = ChangeProposed
    pending = asyncio.create_task(propose(h))
    await h.at_gate.wait()
    try:
        await select(h, "ask_me", 1)
        await select(h, "merge_when_safe", 2)
    finally:
        h.open_gate.set()
        await pending
    h.manager.run_ended("agent-1", None)
    await settled(h)
    assert rec(h).proposal_mode == captured
    assert "change-1" not in h.manager._ctx.assessment.waits
    assert clock.calls.call_count == 0 and clock.registrations == []
