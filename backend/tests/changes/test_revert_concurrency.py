"""Inverse service barriers, drains and concurrent policy changes."""

import asyncio
import json
from dataclasses import replace

import pytest
from changes_revert_helpers import install_spend_evidence as install_spend_evidence
from changes_revert_helpers import pytestmark as pytestmark
from changes_revert_helpers import system as system
from test_revert_preflight import repo as repo

from oversight.changes import (
    _revert,
)
from oversight.workspace import apply_inverse
from oversight_contracts.graph import GraphDelta
from oversight_contracts.policy import EvidenceRef


async def test_revert_shared_snapshot_drains_before_build(system, monkeypatch):
    from functools import partial

    from changes_helpers import Harness

    from oversight.changes import _delivery
    from oversight.changes._versions import main_delta, with_positions

    s, ctx = system, system.service.ctx
    await s.manager.open(Harness.task(2), "fake")
    rec = ctx.state.agents["agent-2"]
    h = await s.repo.commit("later.py", "later = True\n")
    s.command = s.command.model_copy(update={"main_commit": h})
    new = await ctx.scan(h)
    old = ctx.state.snapshot
    rec.delta = main_delta(old, new, h, "merge")
    rec.next_snapshot = with_positions(new, old)
    ctx.state.pending[rec.agent_id] = rec
    # Another older delta is unsent, and must never be published after the inverse.
    other = replace(rec, task=Harness.task(3), sending=None, sent=set())
    ctx.state.pending[other.agent_id] = other
    publish = ctx.publish
    earlier, inverse = asyncio.Event(), asyncio.Event()
    release_earlier, release_inverse = asyncio.Event(), asyncio.Event()

    async def gated(payload):
        if isinstance(payload, GraphDelta):
            if payload.cause == "merge":
                earlier.set()
                await release_earlier.wait()
            else:
                inverse.set()
                await release_inverse.wait()
        return await publish(payload)

    monkeypatch.setattr(ctx, "publish", gated)
    rec.sending = ctx.spawn(_delivery.deliver(ctx, rec.delta))
    rec.sending.add_done_callback(partial(_delivery.receipt, ctx, rec, "delta"))
    await asyncio.wait_for(earlier.wait(), 10)
    task = asyncio.create_task(s.run())
    try:
        await asyncio.sleep(0)
        assert not task.done() and ctx.state.snapshot is old
        release_earlier.set()
        await asyncio.wait_for(inverse.wait(), 10)
        assert ctx.state.snapshot is rec.next_snapshot
        assert ctx.state.snapshot.commit == h
        assert other.delta is other.next_snapshot is None
        release_inverse.set()
        result = await task
        inverse_delta = json.loads(s.events[-1].envelope)["payload"]
        assert inverse_delta["from_version"] == rec.next_snapshot.version
        assert s.manager.snapshot.commit == result.revert_commit
        assert s.service.ctx.state is s.manager._ctx.state
    finally:
        release_earlier.set()
        release_inverse.set()
        await task
        ctx.state.pending.clear()


@pytest.mark.parametrize("system", ["run-20261031-120000"], indirect=True)
async def test_waiting_rules_current_main_barrier_fails_after_revert(system, monkeypatch, tmp_path):
    from changes_helpers import Harness

    from oversight.changes import _rules
    from oversight.changes._assessment import AssessmentWait
    from oversight.changes._mode import StartCapability
    from oversight.changes._protected_paths import capture_proposal_paths
    from oversight.events import NeedsYouWait
    from oversight_contracts.events import Budget, ChangeProposedEvent, RunStarted
    from oversight_contracts.policy import MergeModeState

    s, ctx = system, system.service.ctx
    ctx.grants.run_id = s.store.run_id
    run_receipt = await s.publish(
        RunStarted(
            run_id=ctx.grants.run_id,
            contract_version="1.8.0",
            prompt_version="test",
            workspace="demo-repo",
            base_commit=s.command.main_commit,
            models=[],
            slots=[],
            limits=Harness.task(2).limits,
            budget=Budget(budget_usd=1, spent_usd=0, warn_at=[0.5, 0.8], block_at=0.9),
        )
    )
    s.manager.spend_received(run_receipt)
    await s.manager.open(Harness.task(2), "fake")
    with monkeypatch.context() as proposal_adapter:
        proposal_adapter.setattr(s.ws._isolation, "select", lambda policy, injected, runner: runner)
        assert await s.manager.proposed("agent-2", "Pending rules work")
    s.manager.run_ended("agent-2", None)
    rec = ctx.state.agents["agent-2"]
    proposed = next(
        ChangeProposedEvent.model_validate_json(e.envelope)
        for e in s.events
        if e.type == "change.proposed"
    )
    proposal = proposed.payload
    mode = MergeModeState(mode="merge_when_safe", revision=1)
    ctx.mode._state = mode
    monkeypatch.setattr(ctx.mode, "_availability", lambda: StartCapability(True, "test capability"))
    monkeypatch.setattr(s.ws._isolation, "_born", True)
    monkeypatch.setattr(s.ws._isolation, "_checked", True)
    ledger, pin = await install_spend_evidence(s, tmp_path)
    assessment = s.repo.event.payload.assessment.model_copy(
        deep=True,
        update=dict(
            change_id=rec.change_id,
            agent_id=rec.agent_id,
            revision=rec.revision,
            base_commit=rec.base_commit,
            head_commit=rec.head_commit,
            main_commit=s.command.main_commit,
            mode_revision=1,
        ),
    )
    for rule in assessment.rules:
        rule.evidence[:] = (
            [EvidenceRef(event_seq=pin.seq)]
            if rule.rule == "known_spend"
            else [EvidenceRef(git_object=rec.base_commit)]
        )
        if rule.rule == "combined_tests":
            object.__setattr__(rule, "result", "unknown")
            object.__setattr__(rule, "observed", None)
        elif rule.rule == "protected_paths":
            rule.evidence[:] = [
                EvidenceRef(event_seq=run_receipt.seq),
                EvidenceRef(event_seq=proposed.seq),
            ]
    wait = AssessmentWait(
        rec,
        NeedsYouWait(rec.change_id, rec.revision, rec.head_commit, 0),
        mode,
        proposal,
        proposed.seq,
        s.command.main_commit,
        latest=assessment,
        spend_pin=pin,
        paths=capture_proposal_paths(
            proposal,
            proposal_seq=proposed.seq,
            run_started_seq=run_receipt.seq,
        ),
    )
    ctx.assessment.waits[rec.change_id] = wait
    rec.proposal_mode = mode
    reservation = _rules.capture(ctx, wait, assessment)
    rec.state, rec.fallback = "deciding", "ready"
    rec.taken, rec.evidence, rec.decision = rec.revision, reservation.evidence, reservation.decision
    rec.rules_consumed.add(rec.revision)
    assert reservation.spend_pin is pin and ctx.assessment.spend_receipt is pin
    assert ledger.loaded and ledger.state is not None and ledger.state.spent_usd == 0
    assert _rules.revalidate(ctx, reservation) is None
    result = await s.run()
    assert result.result == "reverted"
    failure = _rules.revalidate(ctx, reservation)
    assert failure is not None and failure[0] == "main_moved"
    assert await s.head() == result.revert_commit
    assert reservation.evidence.assessment is not None
    assert ctx.state.snapshot.commit != reservation.evidence.assessment.main_commit


@pytest.mark.parametrize("phase", ["suite", "effect"])
async def test_revert_allows_concurrent_ask_me_switch(system, monkeypatch, phase):
    from oversight_contracts.commands import SetMergeMode
    from oversight_contracts.policy import MergeModeState

    s, ctx = system, system.service.ctx
    ctx.mode._state = MergeModeState(mode="merge_when_safe", revision=1)
    entered, release = asyncio.Event(), asyncio.Event()
    run = s.ws.run_tests if phase == "suite" else apply_inverse

    async def held(*args, **kwargs):
        assert s.ws.operations.holds(main=True, agent_id="agent-1")
        assert not ctx.mode._lock.locked()
        entered.set()
        await release.wait()
        return await run(*args, **kwargs)

    monkeypatch.setattr(
        s.ws if phase == "suite" else _revert,
        "run_tests" if phase == "suite" else "apply_inverse",
        held,
    )
    reverting = asyncio.create_task(s.run())
    try:
        await asyncio.wait_for(entered.wait(), 10)
        switched = await asyncio.wait_for(
            s.manager.set_merge_mode(SetMergeMode(mode="ask_me", revision=1)), 10
        )
        assert switched.mode == "ask_me" and switched.revision == 2
        assert not reverting.done()
        release.set()
        assert (await reverting).result == "reverted"
        assert not ctx.state.suspect
    finally:
        release.set()
        await reverting


async def test_revert_second_drain_observes_publish_started_during_suite(system, monkeypatch):
    from functools import partial

    from changes_helpers import Harness

    from oversight.changes import _delivery
    from oversight.changes._versions import main_delta

    s, ctx = system, system.service.ctx
    await s.manager.open(Harness.task(2), "fake")
    rec = ctx.state.agents["agent-2"]
    head = await s.repo.commit("later.py", "later = True\n")
    s.command = s.command.model_copy(update={"main_commit": head})
    old, new = ctx.state.snapshot, await ctx.scan(head)
    rec.delta, rec.next_snapshot = main_delta(old, new, head, "merge"), new
    suite, publish = s.ws.run_tests, ctx.publish
    pending, second, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    drains = 0

    async def gated(payload):
        if isinstance(payload, GraphDelta) and payload.cause == "merge":
            pending.set()
            await release.wait()
        return await publish(payload)

    async def late_pending(tree, **kwargs):
        assert drains == 1 and rec.agent_id not in ctx.state.pending
        ctx.state.pending[rec.agent_id] = rec
        rec.sending = ctx.spawn(_delivery.deliver(ctx, rec.delta))
        rec.sending.add_done_callback(partial(_delivery.receipt, ctx, rec, "delta"))
        await pending.wait()
        return await suite(tree, **kwargs)

    async def drain(ctx):
        nonlocal drains
        drains += 1
        if drains == 2:
            assert not rec.sending.done() and ctx.state.snapshot is old
            second.set()
        await _delivery.drain_pending(ctx)

    monkeypatch.setattr(ctx, "publish", gated)
    monkeypatch.setattr(s.ws, "run_tests", late_pending)
    s.service.drain_pending = drain
    task = asyncio.create_task(s.run())
    try:
        await asyncio.wait_for(second.wait(), 10)
        assert ctx.state.snapshot is old and not task.done()
        release.set()
        result = await task
        delta = json.loads(s.events[-1].envelope)["payload"]
        assert drains == 2 and delta["from_version"] == new.version
        assert delta["from_version"] != old.version
        assert s.manager.snapshot.commit == result.revert_commit
    finally:
        release.set()
        await task
        ctx.state.pending.clear()
