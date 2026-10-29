"""Publication boundaries keep refused evidence out of the real SQLite store."""

import asyncio
import threading
from dataclasses import replace
from typing import Any, Literal

import pytest
from changes_helpers import EDIT, TESTS, Harness, assert_main_clean, queued_callbacks_ran
from decision_helpers import observed_target

from oversight.changes import _facts, _proposal, _suite_evidence
from oversight.changes._facts import FactsEvidence
from oversight.changes._shortcuts_common import Source
from oversight.changes._state import ChangeRecord
from oversight.events import StoredEvent, read_store
from oversight.workspace import SuiteInterrupted, SuiteRun, SuiteUnavailable, Tree
from oversight_contracts.events import CaseResult, ChangeProposed, ErrorReport, SuiteExecution
from oversight_contracts.review import ChangeFacts


def evidence_events(h: Harness) -> list[StoredEvent]:
    return [
        event
        for event in read_store(h.tmp_path / "run.sqlite")
        if event.type in {"tests.executed", "change.facts_recorded"}
    ]


@pytest.mark.anyio
async def test_retention_parses_off_loop_and_keeps_added_ids_first(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = await h.open(1)
    old = (await tree.read_text(TESTS)).content
    new = old + "\n\ndef test_z_added():\n    assert True\n"
    assert await h.edit(1, TESTS, old, new)
    run = await h.workspace.run_tests(tree, timeout_s=60)
    loop_thread = threading.get_ident()
    parsed: list[str] = []

    def source(text: str) -> Source:
        assert threading.get_ident() != loop_thread, "retention parsed on the event loop"
        parsed.append(text)
        return Source(text)

    monkeypatch.setattr(_suite_evidence, "Source", source)
    cases = (
        *(CaseResult(test_id=f"{TESTS}::test_a_{i:03}", outcome="passed") for i in range(205)),
        CaseResult(test_id=f"{TESTS}::test_z_added", outcome="passed"),
    )
    await h.manager.tests_executed("agent-1", replace(run, cases=cases))
    [execution] = h.payloads(SuiteExecution)
    assert old in parsed and new in parsed
    assert execution.suite.cases is not None and len(execution.suite.cases) == 200
    assert execution.suite.cases[0].test_id == f"{TESTS}::test_z_added"
    assert execution.suite.cases_complete is False


@pytest.mark.anyio
async def test_cancelled_retention_holds_lock_until_worker_finishes(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = await h.open(1)
    run = await h.workspace.run_tests(tree, timeout_s=60)
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    loop_thread = threading.get_ident()

    def retention(rec: ChangeRecord, run: SuiteRun) -> frozenset[str]:
        assert threading.get_ident() != loop_thread, "retention ran on the event loop"
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(10), "test did not release retention"
        return frozenset()

    monkeypatch.setattr(_suite_evidence, "added_ids", retention)
    publication = asyncio.create_task(h.manager.tests_executed("agent-1", run))
    try:
        async with asyncio.timeout(5):
            await entered.wait()
        publication.cancel()
        await queued_callbacks_ran()
        assert not publication.done()
        assert not h.workspace.operations.idle
        assert not evidence_events(h)
    finally:
        release.set()
        publication.cancel()
        with pytest.raises(asyncio.CancelledError):
            await publication
    assert h.workspace.operations.idle
    assert [event.type for event in evidence_events(h)] == ["tests.executed"]
    [execution] = h.payloads(SuiteExecution)
    assert execution.tested_tree == run.tested_tree


def refuse_cases(
    full_cases: tuple[CaseResult, ...], *, added_test_ids: frozenset[str]
) -> tuple[list[CaseResult], bool]:
    """An invalid projection must be refused by the real public suite validator."""
    return [CaseResult(test_id="tests/invented.py::test_forged", outcome="passed")], False


@pytest.mark.anyio
@pytest.mark.parametrize(
    "phase,mode",
    [
        ("agent", "normal"),
        ("proposal", "normal"),
        ("combined", "normal"),
        ("combined", "interrupted"),
        ("combined", "unavailable"),
    ],
)
async def test_suite_validation_refusal_never_reaches_store(
    h: Harness, monkeypatch: pytest.MonkeyPatch, phase: str, mode: str
) -> None:
    if phase == "combined":
        await h.run_agent(1, EDIT)
        await h.run_agent(2, ("shop/mod.py", "return value() + value()", "return 2 * value()"))
        await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    else:
        await h.open(1)
        assert await h.edit(1, *EDIT)
    agent_id = "agent-2" if phase == "combined" else "agent-1"
    rec = h.manager._agents[agent_id]
    before = evidence_events(h)
    executions, runs = dict(rec.executions), dict(rec.suite_runs)
    proposals = h.payloads(ChangeProposed)
    pre = await h.main_head()
    real_run = h.workspace.run_tests
    captured: list[SuiteRun] = []

    async def run_tests(tree: Tree, *, timeout_s: float) -> SuiteRun:
        run = await real_run(tree, timeout_s=timeout_s)
        captured.append(run)
        if mode == "interrupted":
            raise SuiteInterrupted(replace(run, problems=("suite interrupted",)))
        if mode == "unavailable":
            raise SuiteUnavailable(replace(run, cases=(), problems=("unavailable",)), OSError())
        return run

    monkeypatch.setattr(h.workspace, "run_tests", run_tests)
    monkeypatch.setattr(_suite_evidence, "retain_cases", refuse_cases)
    if phase == "agent":
        run = await h.workspace.run_tests(rec.tree, timeout_s=60)
        with pytest.raises(ValueError, match="cases differ"):
            await h.manager.tests_executed(agent_id, run)
    elif phase == "proposal":
        assert not await h.finish(1)
    else:
        try:
            await h.manager.approve("change-2", None, **observed_target(h.manager, "change-2"))
        except asyncio.CancelledError:
            pytest.fail(
                "suite validation must refuse the projection before propagating cancellation"
            )
        assert await h.main_head() == pre
        await assert_main_clean(h)
        assert captured[0].tested_tree is not None and captured[0].tested_commit is None
    if phase != "agent":
        assert any("suite cases differ" in report.message for report in h.payloads(ErrorReport))
    assert len(captured) == 1
    assert evidence_events(h) == before
    assert rec.executions == executions and rec.suite_runs == runs
    assert h.payloads(ChangeProposed) == proposals


@pytest.mark.anyio
async def test_facts_construction_keeps_event_loop_responsive(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = asyncio.get_running_loop()
    responsive = threading.Event()
    build = _facts.build_change_facts

    def wait_for_loop(evidence: FactsEvidence) -> ChangeFacts:
        loop.call_soon_threadsafe(responsive.set)
        assert responsive.wait(5), "facts construction blocked the event loop"
        return build(evidence)

    monkeypatch.setattr(_proposal, "build_change_facts", wait_for_loop)
    await h.open(1)
    assert await h.edit(1, *EDIT)
    assert await h.finish(1)
    assert [event.type for event in evidence_events(h)] == [
        "tests.executed",
        "change.facts_recorded",
    ]
    [facts] = h.payloads(ChangeFacts)
    assert facts.edited_paths == [EDIT[0]]


@pytest.mark.anyio
async def test_facts_validation_refusal_never_reaches_store(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    await h.open(1)
    assert await h.edit(1, *EDIT)

    def forged_facts(**fields: Any) -> ChangeFacts:
        return ChangeFacts(**fields).model_copy(update={"edited_paths": []})

    monkeypatch.setattr(_facts, "ChangeFacts", forged_facts)
    assert not await h.finish(1)
    # A valid suite and proposal may precede the refusal; no forged facts may be stored.
    assert [event.type for event in evidence_events(h)] == ["tests.executed"]
    assert len(h.payloads(ChangeProposed)) == 1
    assert not h.payloads(ChangeFacts)
    assert any("edits differ" in report.message for report in h.payloads(ErrorReport))


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["interrupted", "unavailable"])
async def test_combined_partial_suite_is_stored_before_recovery(
    h: Harness,
    monkeypatch: pytest.MonkeyPatch,
    mode: Literal["interrupted", "unavailable"],
) -> None:
    await h.run_agent(1, EDIT)
    await h.run_agent(2, ("shop/mod.py", "return value() + value()", "return 2 * value()"))
    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    before = evidence_events(h)
    pre = await h.main_head()
    real_run = h.workspace.run_tests
    captured: list[SuiteRun] = []

    async def run_tests(tree: Tree, *, timeout_s: float) -> SuiteRun:
        run = await real_run(tree, timeout_s=timeout_s)
        partial = replace(
            run,
            result=run.result.model_copy(
                update={"outcome": "timeout" if mode == "interrupted" else "error"}
            ),
            cases=run.cases if mode == "interrupted" else (),
            problems=(
                "suite interrupted" if mode == "interrupted" else "JUnit report unavailable",
            ),
        )
        captured.append(partial)
        if mode == "interrupted":
            raise SuiteInterrupted(partial)
        raise SuiteUnavailable(partial, OSError("spawn failed"))

    monkeypatch.setattr(h.workspace, "run_tests", run_tests)
    decision = h.manager.approve("change-2", None, **observed_target(h.manager, "change-2"))
    if mode == "interrupted":
        with pytest.raises(asyncio.CancelledError):
            await decision
    else:
        await decision
    stored = evidence_events(h)
    assert stored[:-1] == before and stored[-1].type == "tests.executed"
    [execution] = [e for e in h.payloads(SuiteExecution) if e.phase == "combined"]
    [run] = captured
    assert execution.change_id == "change-2" and execution.revision == 1
    assert execution.tested_tree == run.tested_tree and execution.tested_commit is None
    assert execution.suite.outcome == ("timeout" if mode == "interrupted" else "error")
    assert execution.suite.cases == list(run.cases) and execution.suite.cases_complete is False
    assert await h.main_head() == pre
    await assert_main_clean(h)
