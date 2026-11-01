"""Deterministic queued attempts with real Git, receipts and the scripted suite adapter."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace

from changes_helpers import PATH, commit_on_main, edit_main, queued_callbacks_ran
from test_assessment import rec, settled, terminals, wait
from test_follow_main import save
from test_rules_reservation import start

from oversight.changes import _candidate
from oversight.changes._state import ChangeState
from oversight.changes._support import RulesFailure
from oversight_contracts.policy import EvidenceRef


async def drain(h, decisions):
    # Decision completion installs another evaluation; drain each actual owner once.
    await settled(h)
    index = 0
    while index < len(decisions):
        await asyncio.gather(decisions[index], return_exceptions=True)
        index += 1
        await settled(h)


def general_evidence(h, monkeypatch):
    sched = h.manager._ctx.assessment
    collect = sched.inputs

    def inputs(w):
        value = collect(w)
        ref = EvidenceRef(event_seq=w.proposal_seq)
        return replace(value, evidence={rule: (ref,) for rule in value.evidence})

    monkeypatch.setattr(sched, "inputs", inputs)
    monkeypatch.setattr(h.workspace._isolation, "select", lambda policy, injected, runner: runner)


async def peer(h):
    admission = h.manager._ctx.ports.rules_admission
    h.manager._ctx.ports.rules_admission = lambda record, assessment: None
    try:
        await h.run_agent(2, ("shop/__init__.py", "", "def extra():\n    return 2\n"))
        await settled(h)
    finally:
        h.manager._ctx.ports.rules_admission = admission
    return h.manager._agents["agent-2"]


@asynccontextmanager
async def moving_main(h, monkeypatch, move):
    other = await peer(h) if move != "save" else None
    if move == "save":
        content, base = await edit_main(h, PATH, "return value() + value()", "return value() * 2")
        h.workspace.arm("commit", mode="hold")
        moving = asyncio.create_task(h.manager.save_main(PATH, content, base))
        await h.workspace.entered.wait()
        try:
            yield moving
        finally:
            h.workspace.resume.set()
    else:
        assert other is not None
        async with h.workspace.locks(main=True):
            if move == "rules_merge":
                h.manager._ctx.assessment.wake(other)
                await settled(h)
                moving = other.rules_task
            else:
                moving = h.manager.approve(
                    other.change_id, None, revision=other.revision, head_commit=other.head_commit
                )
                await queued_callbacks_ran()
            yield moving


@asynccontextmanager
async def queued_move(h, reserved, monkeypatch, move="save", *, settle=True):
    general_evidence(h, monkeypatch)
    old = wait(h)
    async with moving_main(h, monkeypatch, move) as moving:
        h.manager.run_ended("agent-1", None)
        await settled(h)
        task = rec(h).rules_task
        assert task is not None and not task.done() and not old.active
        yield old, task
    assert moving is not None
    await moving
    await asyncio.gather(task, return_exceptions=True)
    if settle:
        await settled(h)
    else:
        await queued_callbacks_ran()


def on_pending(h, monkeypatch, action):
    restore = ChangeState.restore
    seen = []

    def restored(state, record, decision):
        restore(state, record, decision)
        if record is rec(h) and record.follow_pending == decision and not seen:
            seen.append(decision)
            action(record)

    monkeypatch.setattr(ChangeState, "restore", restored)
    return seen


async def closed_failure(h, reserved, monkeypatch, failure):
    general_evidence(h, monkeypatch)
    if failure == "conflict":
        await commit_on_main(h, PATH, "return 1", "return 4")
        await h.manager.rescan()
        await settled(h)
    elif failure == "suite_failed":
        suite = h.workspace.run_tests

        async def failed(*args, **kwargs):
            await suite(*args, **kwargs)
            raise RulesFailure("suite_failed", "Scripted failing combined suite")

        monkeypatch.setattr(h.workspace, "run_tests", failed)
    else:
        measure = _candidate.measure

        async def veto(*args, **kwargs):
            measured = await measure(*args, **kwargs)
            return replace(
                measured,
                rules=tuple(
                    r.model_copy(update={"result": "fail"}) if r.rule == "small_change" else r
                    for r in measured.rules
                ),
            )

        monkeypatch.setattr(_candidate, "measure", veto)
    task = await start(h, reserved)
    await task
    old, count = wait(h), len(terminals(h))
    assert count == (0 if failure == "CandidateVeto" else 1)
    assert rec(h).follow_pending is None and not rec(h).merged
    await save(h, "return value() + value()", "return value() * 2")
    await settled(h)
    assert wait(h) is old and len(reserved[1]) == 1 and len(terminals(h)) == count
