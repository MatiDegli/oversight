"""Own unavailable receipts cross proposal, combination and selected-case boundaries."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from changes_helpers import EDIT, TESTS, Harness, assert_main_clean
from decision_helpers import observed_target

from oversight.changes import _probes
from oversight.workspace import SuiteUnavailable
from oversight.workspace.suite import OwnSourceTestsUnavailable
from oversight_contracts.events import ErrorReport, SuiteExecution

pytestmark = pytest.mark.anyio
REASON = "mapped package outside tested tree"


def unavailable(run):
    return SuiteUnavailable(
        replace(
            run,
            result=run.result.model_copy(
                update={
                    "outcome": "error",
                    "passed": 0,
                    "failed": 0,
                    "skipped": 0,
                    "failed_tests": [],
                    "cases": [],
                    "cases_complete": False,
                }
            ),
            cases=(),
            problems=(REASON,),
            output="tests unavailable: " + REASON,
        ),
        OwnSourceTestsUnavailable(REASON),
    )


def assert_error(execution):
    assert execution.suite.outcome == "error"
    assert (execution.suite.passed, execution.suite.failed, execution.suite.skipped) == (0, 0, 0)
    assert execution.suite.failed_tests == [] and execution.suite.cases == []
    assert execution.suite.cases_complete is False


@pytest.mark.parametrize("phase", ["proposal", "combined"])
async def test_unavailable_publishes_error_reason_and_combined_recovers(
    h: Harness, monkeypatch, phase
):
    if phase == "combined":
        await h.run_agent(1, EDIT)
        await h.run_agent(2, ("shop/mod.py", "return value() + value()", "return 2 * value()"))
        await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    else:
        await h.open(1)
        assert await h.edit(1, *EDIT)
    pre = await h.main_head()
    real = h.workspace.run_tests

    async def refused(tree, **kwargs):
        raise unavailable(await real(tree, **kwargs))

    monkeypatch.setattr(h.workspace, "run_tests", refused)
    if phase == "combined":
        await h.manager.approve("change-2", None, **observed_target(h.manager, "change-2"))
        assert await h.main_head() == pre
        await assert_main_clean(h)
    else:
        await h.finish(1)
    execution = [e for e in h.payloads(SuiteExecution) if e.phase == phase][-1]
    assert_error(execution)
    rec = h.manager._agents["agent-2" if phase == "combined" else "agent-1"]
    run = next(run for seq, run in rec.suite_runs.items() if rec.executions[seq] == execution)
    assert run.problems == (REASON,) and REASON in run.output
    assert any(REASON in e.message for e in h.payloads(ErrorReport))


async def test_unavailable_probes_publish_diagnostics_without_added_evidence(
    h: Harness, monkeypatch
):
    tree = await h.open(1)
    old = (await tree.read_text(TESTS)).content
    assert await h.edit(1, TESTS, old, old + "\ndef test_added(): assert True\n")
    assert await h.finish(1)
    rec = h.manager._agents["agent-1"]
    real = h.workspace.run_tests

    async def refused(tree, **kwargs):
        kwargs.pop("isolation", None)
        raise unavailable(await real(tree, **kwargs))

    monkeypatch.setattr(h.workspace, "run_tests", refused)
    assert rec.head_commit is not None
    async with h.workspace.locks(main=True, agent_id="agent-1"):
        probes, problems = await _probes.probe_added_tests(
            h.manager._ctx,
            rec,
            head=rec.head_commit,
            revision=rec.revision,
        )
    assert probes == ()
    assert sum(REASON in p for p in problems) == 2
    for execution in h.payloads(SuiteExecution):
        if execution.phase in ("base_probe", "head_probe"):
            assert_error(execution)
    assert {e.phase for e in h.payloads(SuiteExecution)} >= {"base_probe", "head_probe"}


async def test_native_probe_refuses_before_checkout_restore_or_pytest(h: Harness, monkeypatch):
    await h.open(1)
    rec = h.manager._agents["agent-1"]
    checkout, tests = AsyncMock(), AsyncMock()
    before = len(h.workspace.calls)
    monkeypatch.setattr(_probes, "base_probe_checkout", checkout)
    monkeypatch.setattr(h.workspace, "run_tests", tests)
    with monkeypatch.context() as forced:
        forced.setattr(_probes, "sys", SimpleNamespace(platform="win32"))
        probes, problems = await _probes.probe_added_tests(
            h.manager._ctx, rec, head="unused", revision=1
        )
    assert probes == () and len(problems) == 2
    assert all("native Windows isolation is unavailable" in p for p in problems)
    checkout.assert_not_called()
    tests.assert_not_called()
    assert len(h.workspace.calls) == before
