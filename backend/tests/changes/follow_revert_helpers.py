"""An unconsumed second proposal beside the real Revert integration fixture."""

import asyncio

from changes_helpers import Harness
from test_assessment import ManualClock

from oversight.changes._assessment import RulesScheduler
from oversight.changes._mode import StartCapability
from oversight_contracts.events import Budget, RunStarted
from oversight_contracts.policy import MergeModeState


async def idle_wait(s, monkeypatch):
    ctx = s.service.ctx
    clock = ManualClock()
    ctx.assessment = RulesScheduler(ctx, clock, clock.sleep_until)
    ctx.grants.run_id = s.store.run_id
    receipt = await s.publish(
        RunStarted(
            run_id=s.store.run_id,
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
    s.manager.spend_received(receipt)
    mode = MergeModeState(mode="merge_when_safe", revision=1)
    ctx.mode._state = mode
    monkeypatch.setattr(ctx.mode, "_availability", lambda: StartCapability(True, "test"))
    await s.manager.open(Harness.task(2), "fake")
    tree = ctx.state.agents["agent-2"].tree
    (tree.root / "notes.txt").write_text("Pending work\n")
    assert await s.manager.file_edited("agent-2", "notes.txt")
    with monkeypatch.context() as proposal:
        proposal.setattr(s.ws._isolation, "select", lambda policy, injected, runner: runner)
        assert await s.manager.proposed("agent-2", "Pending work")
    clock.advance(10)
    s.manager.run_ended("agent-2", None)
    wait = ctx.assessment.waits["change-2"]
    assert wait.task is not None
    await asyncio.gather(wait.task)
    assert wait.active and wait.rec.state == "ready" and not wait.rec.rules_consumed
    assert wait.deadline == 130
    return wait
