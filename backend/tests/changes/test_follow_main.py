"""Idle rules authority follows only a verified and graph-delivered main commit."""

import asyncio
from dataclasses import replace
from unittest.mock import Mock

import pytest
from changes_helpers import PATH, Harness, approve, bounded, commit_on_main, edit_main
from test_assessment import clock as clock
from test_assessment import full_evidence, propose, rec, settled, terminals, wait
from test_assessment import ready as ready
from test_mode import available, select

from oversight.changes._policy import assess
from oversight.events import parse_needs_you
from oversight_contracts.events import SuiteExecution
from oversight_contracts.graph import GraphDelta
from oversight_contracts.policy import ChangeAssessment, EvidenceRef

pytestmark = pytest.mark.anyio


async def ready_wait(h, clock):
    available(h)
    await select(h, "merge_when_safe", 0)
    await propose(h)
    clock.advance(10)
    h.manager.run_ended("agent-1", None)
    await settled(h)
    assert wait(h).deadline == 130
    return wait(h)


async def save(h, old="return 1", new="return 4"):
    content, base = await edit_main(h, PATH, old, new)
    await h.manager.save_main(PATH, content, base)
    return await h.main_head()


def fail_scan(h, monkeypatch):
    scan = h.main_graph.scan

    def fail(_commit):
        raise RuntimeError("scan unavailable")

    monkeypatch.setattr(h.main_graph, "scan", fail)
    return scan


async def expired(h, clock, old):
    clock.advance(130)
    assert wait(h).timer is not None
    await wait(h).timer
    [error] = terminals(h)
    note = parse_needs_you(error.message)
    assert note is not None and note.code == "policy_invalid"
    assert note.reason == "Required rules evidence was not confirmed within 120 seconds."
    if wait(h) is not old:
        assert old.timer.cancelled()


def assessed_after_delta(h, post):
    delta = max(e.seq for e in h.events() if e.type == "graph.delta")
    assessments = [
        e for e in h.events() if e.type == "change.assessed" and e.payload.main_commit == post
    ]
    assert assessments and assessments[-1].seq > delta


@pytest.mark.parametrize("move", ["save", "rescan", "merge"])
async def test_idle_ready_wait_follows_each_kind_of_main_move(h: Harness, clock, move):
    if move == "merge":
        await h.run_agent(2, (PATH, "return value() + value()", "return value() * 2"))
    old = await ready_wait(h, clock)
    calls = clock.calls.call_count
    clock.advance(30)
    if move == "save":
        post = await save(h)
    elif move == "rescan":
        post = await commit_on_main(h, PATH, "return 1", "return 4")
        await h.manager.rescan()
    else:
        await approve(h, 2)
        post = await h.main_head()
    await settled(h)
    new = wait(h)
    assert new is not old and new.proposal == old.proposal
    assert (new.target.revision, new.target.head, new.mode) == (
        old.target.revision,
        old.target.head,
        old.mode,
    )
    assert new.target.generation == old.target.generation + 1
    assert new.main_commit == h.manager.snapshot.commit == post
    assert new.deadline == 130 and clock.calls.call_count == calls + 1
    assessed_after_delta(h, post)
    assert "error" not in h.types()
    assert not new.rec.rules_consumed and new.rec.state == "ready"
    assert terminals(h) == []
    await expired(h, clock, old)


@pytest.mark.parametrize("matching_paths", [True, False])
@bounded
async def test_follow_does_not_carry_main_dependent_state(h: Harness, clock, matching_paths):
    old = await ready_wait(h, clock)
    sched = h.manager._ctx.assessment
    combined = h.payloads(SuiteExecution)[0].model_copy(
        update=dict(
            phase="combined",
            tested_commit=old.main_commit,
            tested_tree=old.facts.target.candidate_tree,
        )
    )
    receipt = await h.publish(combined)
    h.manager.rules_received(receipt)
    await settled(h)
    assert sched.inputs(old).suite == combined.suite
    if not matching_paths:
        paths, files = old.paths
        old.paths = (paths, replace(files, evidence=(EvidenceRef(event_seq=999),)))
    expected_paths = sched.path_sources(old)
    source_assessment = assess(sched.inputs(old), "safe-merge-paths-v1")
    old_result = next(r for r in source_assessment.rules if r.rule == "protected_paths")
    old.spend_pin = receipt
    # Gate before evaluate can mutate any reset state.
    entered, release = asyncio.Event(), asyncio.Event()
    evaluate = sched.evaluate

    async def held(wait):
        entered.set()
        await release.wait()
        await evaluate(wait)

    sched.evaluate = held
    try:
        post = await save(h)
        await entered.wait()
        new = wait(h)
        assert new is not old
        assert new.latest is new.spend_pin is new.terminal_seq is None
        assert new.timer is not old.timer and new.task is not old.task
        for name in (
            "facts",
            "facts_seq",
            "inventory",
            "parse_errors",
            "unresolved_removed",
            "possible_callers",
            "grant_measurement",
            "paths",
        ):
            assert getattr(new, name) == getattr(old, name)
        assert sched.path_sources(new) == expected_paths
        assert sched.inputs(new).suite is None
    finally:
        release.set()
    await settled(h)
    assessed_after_delta(h, post)
    assert new.latest is not None
    result = next(r for r in new.latest.rules if r.rule == "protected_paths")
    assert result.result == old_result.result
    assert (result.result != "unknown") == matching_paths
    assert clock.calls.call_args.args[0].suite is None


@pytest.mark.parametrize("move_main", [False, True])
@bounded
async def test_main_moves_during_assessment_receipt(h: Harness, clock, ready, move_main):
    full_evidence(h, ready)
    admission = Mock(return_value=None)
    h.manager._ctx.ports.rules_admission = admission
    old = await ready_wait(h, clock)
    admission.assert_called_once_with(rec(h), old.latest)
    admission.reset_mock()
    sched = h.manager._ctx.assessment
    h.gate = ChangeAssessment
    sched.wake(rec(h))
    await h.at_gate.wait()
    post = await save(h) if move_main else old.main_commit
    h.open_gate.set()
    await settled(h)
    new = wait(h)
    admission.assert_called_once_with(rec(h), new.latest)
    assert new.latest is not None
    assert new.latest.main_commit == post and new.deadline == 130 and not terminals(h)
    if move_main:
        assert old.task.cancelled() and new is not old
        assert all(call.args[1].main_commit != old.main_commit for call in admission.call_args_list)
        assessed_after_delta(h, post)
    else:
        assert new is old and not old.task.cancelled()
    await expired(h, clock, old)


@pytest.mark.parametrize("failure", ["scan", "publish"])
@pytest.mark.parametrize("recover", [True, False])
async def test_follow_waits_for_snapshot_receipt_after_scan_or_delivery_failure(
    h: Harness, clock, ready, monkeypatch, failure, recover
):
    full_evidence(h, ready)
    admission = Mock(return_value=None)
    h.manager._ctx.ports.rules_admission = admission
    old = await ready_wait(h, clock)
    admission.assert_called_once_with(rec(h), old.latest)
    admission.reset_mock()
    sched = h.manager._ctx.assessment
    calls = clock.calls.call_count
    scan = fail_scan(h, monkeypatch) if failure == "scan" else None
    if failure == "publish":
        h.fail_once = GraphDelta
    post = await save(h)
    await settled(h)
    assert wait(h) is old and old.parked and old.active
    assert sched.committed_main == post != h.manager.snapshot.commit
    assert clock.calls.call_count == calls and not admission.called
    if scan:
        monkeypatch.setattr(h.main_graph, "scan", scan)
    if recover:
        await h.manager.rescan()
        await settled(h)
        assert wait(h) is not old and wait(h).main_commit == post
        assert wait(h).deadline == 130 and clock.calls.call_count == calls + 1
        admission.assert_called_once_with(rec(h), wait(h).latest)
    else:
        await expired(h, clock, old)
        events = len(h.payloads(ChangeAssessment))
        await h.manager.rescan()
        await settled(h)
        assert wait(h) is old and not old.active
        assert len(h.payloads(ChangeAssessment)) == events and len(terminals(h)) == 1


@pytest.mark.parametrize("first_delivered", [False, True])
async def test_follow_coalesces_moves_without_extending_deadline(
    h: Harness, clock, monkeypatch, first_delivered
):
    old = await ready_wait(h, clock)
    sched = h.manager._ctx.assessment
    scan = h.main_graph.scan
    if not first_delivered:
        fail_scan(h, monkeypatch)
    first = await save(h)
    await settled(h)
    if first_delivered:
        old = wait(h)
        assert old.main_commit == h.manager.snapshot.commit == first
        fail_scan(h, monkeypatch)
    post = await save(h, "return 4", "return 5")
    assert first != post and wait(h) is old and old.parked
    calls = clock.calls.call_count
    sched.main_delivered(h.manager.snapshot.commit if first_delivered else first)
    await settled(h)
    assert wait(h) is old and old.parked and clock.calls.call_count == calls
    monkeypatch.setattr(h.main_graph, "scan", scan)
    await h.manager.rescan()
    await settled(h)
    new = wait(h)
    assert new.target.generation == old.target.generation + 1 and new.main_commit == post
    assert clock.calls.call_count == calls + 1
    calls = clock.calls.call_count
    sched.main_changed(post)
    sched.main_delivered(post)
    await settled(h)
    assert wait(h) is new and clock.calls.call_count == calls
    for value in (6, 7):
        clock.advance(value * 10)
        post = await save(h, f"return {value - 1}", f"return {value}")
        await settled(h)
        sched.spend_changed()
        await settled(h)
        assert wait(h).deadline == 130 and wait(h).main_commit == post
    await expired(h, clock, old)


async def test_working_wait_follows_and_starts_its_deadline_when_ready(h: Harness, clock):
    available(h)
    await select(h, "merge_when_safe", 0)
    await propose(h)
    old = wait(h)
    assert old.deadline is old.timer is None and not clock.calls.called
    post = await save(h)
    await settled(h)
    new = wait(h)
    assert new is not old and new.main_commit == post and new.deadline is new.timer is None
    clock.advance(10)
    h.manager.run_ended("agent-1", None)
    await settled(h)
    assert new.deadline == 130 and clock.calls.call_count == 1


async def test_proposal_during_pending_delivery_starts_parked(h: Harness, clock, monkeypatch):
    available(h)
    await select(h, "merge_when_safe", 0)
    scan = fail_scan(h, monkeypatch)
    post = await save(h, "return value() + value()", "return value() * 2")
    await propose(h)
    old = wait(h)
    assert old.parked and not clock.calls.called
    clock.advance(10)
    h.manager.run_ended("agent-1", None)
    monkeypatch.setattr(h.main_graph, "scan", scan)
    await h.manager.rescan()
    await settled(h)
    assert wait(h) is not old and wait(h).main_commit == post
    assert wait(h).deadline == 130 and clock.calls.call_count == 1


@pytest.mark.parametrize("when", ["birth", "while_working"])
async def test_parked_wait_starts_its_deadline_when_ready(h: Harness, clock, monkeypatch, when):
    available(h)
    await select(h, "merge_when_safe", 0)
    if when == "while_working":
        await propose(h)
    scan = fail_scan(h, monkeypatch)
    # Leave EDIT's old text available for a proposal born after the failed scan.
    content, base = await edit_main(h, PATH, "return value() + value()", "return value() * 2")
    await h.manager.save_main(PATH, content, base)
    if when == "birth":
        await propose(h)
    old = wait(h)
    assert old.parked and old.deadline is old.timer is None
    clock.advance(10)
    h.manager.run_ended("agent-1", None)
    await settled(h)
    assert old.deadline == 130 and old.timer is not None and not clock.calls.called
    await expired(h, clock, old)
    monkeypatch.setattr(h.main_graph, "scan", scan)
    await h.manager.rescan()
    await settled(h)
    assert wait(h) is old and not clock.calls.called and len(terminals(h)) == 1
