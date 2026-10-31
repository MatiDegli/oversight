"""A queued rules owner follows Revert only after its decision finishes."""

import asyncio

import pytest
from changes_revert_helpers import install_spend_evidence
from follow_revert_helpers import idle_wait
from test_revert import system as system
from test_revert_preflight import repo as repo
from test_spend_evidence import complete_inputs

from oversight.changes._assessment import RulesScheduler
from oversight_contracts.events import ChangeAssessedEvent

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("system", ["run-20261031-120000"], indirect=True)
async def test_revert_rearms_a_queued_rules_attempt(system, monkeypatch, tmp_path):
    s, ctx = system, system.service.ctx
    old = await idle_wait(s, monkeypatch)
    await install_spend_evidence(s, tmp_path)
    if old.task is not None:
        await old.task
    monkeypatch.setattr(s.ws._isolation, "_born", True)
    monkeypatch.setattr(s.ws._isolation, "_checked", True)

    def collect(w):
        return RulesScheduler.inputs(ctx.assessment, w)

    monkeypatch.setattr(ctx.assessment, "inputs", lambda w: complete_inputs(collect, w))
    entered, release = asyncio.Event(), asyncio.Event()
    suite = s.ws.run_tests

    async def held(*args, **kwargs):
        entered.set()
        await release.wait()
        return await suite(*args, **kwargs)

    monkeypatch.setattr(s.ws, "run_tests", held)
    reverting = asyncio.create_task(s.run())
    await entered.wait()
    ctx.assessment.wake(old.rec)
    assert old.task is not None
    await old.task
    queued = old.rec.rules_task
    assert queued is not None and not queued.done() and not old.active
    assert old.rec.rules_consumed == {old.rec.revision}
    # Observe the renewed assessment without granting a second merge in this fixture.
    ctx.ports.rules_admission = lambda *_: None
    release.set()
    result = await reverting
    await queued
    await asyncio.gather(*[t for t in (ctx.assessment.waits[old.rec.change_id].task,) if t])
    new = ctx.assessment.waits[old.rec.change_id]
    assert new is not old and new.main_commit == result.main_commit == s.manager.snapshot.commit
    assert new.deadline == old.deadline == 130 and new.active
    assert new.target.generation > old.target.generation and new.latest is not None
    assessed = [
        ChangeAssessedEvent.model_validate_json(e.envelope)
        for e in s.events
        if e.type == "change.assessed"
    ]
    assert assessed[-1].payload.main_commit == result.main_commit
    assert old.rec.follow_pending is None
