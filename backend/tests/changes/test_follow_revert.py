"""Revert's private projection notifies the same idle follow boundary."""

import asyncio

import pytest
from follow_revert_helpers import idle_wait
from test_revert import system as system
from test_revert_preflight import repo as repo

from oversight.changes import RevertRefused
from oversight_contracts.events import ChangeAssessedEvent

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("outcome", ["success", "conflict", "refused"])
async def test_revert_notifies_changed_before_the_outcome_and_delivered_after_the_snapshot(
    system, monkeypatch, outcome
):
    s, calls = system, []
    sched = s.service.ctx.assessment
    changed, delivered = sched.main_changed, sched.main_delivered

    def announce(post):
        assert not any(e.type == "revert.resolved" for e in s.events)
        calls.append(("changed", post))
        changed(post)

    def receive(post):
        assert s.manager.snapshot.commit == post
        assert s.events[-1].type == "graph.delta"
        calls.append(("delivered", post))
        delivered(post)

    monkeypatch.setattr(sched, "main_changed", announce)
    monkeypatch.setattr(sched, "main_delivered", receive)
    if outcome == "conflict":
        post = await s.repo.commit("value.py", "value = 3\n")
        s.command = s.command.model_copy(update={"main_commit": post})
    if outcome == "refused":
        with pytest.raises(RevertRefused):
            await s.run(principal="model")
    else:
        result = await s.run()
        if outcome == "success":
            assert result.result == "reverted"
            assert calls == [("changed", result.main_commit), ("delivered", result.main_commit)]
    if outcome != "success":
        assert calls == []


@pytest.mark.parametrize("system", ["run-20261031-120000"], indirect=True)
async def test_revert_rearms_an_idle_ready_wait(system, monkeypatch):
    s = system
    old = await idle_wait(s, monkeypatch)
    result = await s.run()
    new = s.service.ctx.assessment.waits["change-2"]
    await asyncio.gather(new.task)
    assert new is not old and not old.active and old.parked
    assert new.target.generation == old.target.generation + 1
    assert new.main_commit == result.main_commit == s.manager.snapshot.commit
    assert old.timer is not None
    assert new.deadline == old.deadline == 130 and old.timer.cancelled()
    assessed = [
        ChangeAssessedEvent.model_validate_json(e.envelope)
        for e in s.events
        if e.type == "change.assessed"
    ]
    assert assessed[-1].payload.main_commit == result.main_commit
    assert assessed[-1].seq > max(e.seq for e in s.events if e.type == "graph.delta")
