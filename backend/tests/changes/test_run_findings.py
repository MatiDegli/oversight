"""Stored temporal witnesses, and refusals at the facts publication boundary."""

import pytest
from changes_helpers import TESTS, Harness

from oversight_contracts.review import ChangeFacts


async def failing_test(h: Harness) -> None:
    tree = await h.open(1)
    assert await h.edit(1, "shop/mod.py", "return 1", "return 0")
    run = await h.workspace.run_tests(tree, timeout_s=60)
    assert run.result.failed == 1
    await h.manager.tests_executed("agent-1", run)


@pytest.mark.anyio
async def test_failure_assertion_edit_pass_has_stored_witnesses(h: Harness) -> None:
    await failing_test(h)
    assert await h.edit(1, TESTS, " > 0", " >= 0")
    assert await h.finish(1)
    [facts] = h.payloads(ChangeFacts)
    [finding] = facts.run_findings
    events = {e.seq: e for e in h.events()}
    assert [
        events[s].type for s in (finding.failed_seq, finding.edited_seq, finding.passed_seq)
    ] == ["tests.executed", "change.updated", "tests.executed"]
    assert finding.kind == "loosened" and finding.test_id == f"{TESTS}::test_value"
    fact = facts.integrity[finding.fact_index]
    assert fact.kind == "assertion_loosened" and fact.test_id == finding.test_id
    assert any(ref.event_seq == finding.edited_seq for ref in fact.evidence)
    assert not facts.complete
    assert "zone producer unavailable" in facts.problems
    assert facts.reach is not None
    assert "reach producer unavailable" not in facts.problems
    assert not any("package 5" in p for p in facts.problems)


@pytest.mark.anyio
async def test_loosening_before_failure_and_unrelated_edit_is_unknown(h: Harness) -> None:
    tree = await h.open(1)
    assert await h.edit(1, TESTS, " > 0", " >= 0")
    assert await h.edit(1, "shop/mod.py", "return 1", "return -1")
    run = await h.workspace.run_tests(tree, timeout_s=60)
    await h.manager.tests_executed("agent-1", run)
    assert await h.edit(1, TESTS, "from shop.mod", "# unrelated\nfrom shop.mod")
    assert await h.edit(1, "shop/mod.py", "return -1", "return 0")
    assert await h.finish(1)
    [facts] = h.payloads(ChangeFacts)
    assert not facts.run_findings
    assert any(p.startswith("run rule unknown:") for p in facts.problems)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mode",
    [
        "timeout",
        "error",
        "truncated",
        "retention",
        "skipped",
        "uncollected",
        "still_failed",
        "missing",
    ],
)
async def test_incomplete_or_nonpassing_later_run_cannot_witness(
    h: Harness,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    from dataclasses import replace

    from oversight.changes import _proposal
    from oversight.workspace import SuiteRun, Tree
    from oversight_contracts.events import CaseResult, SuiteResult

    await failing_test(h)
    assert await h.edit(1, TESTS, " > 0", " >= 0")
    real = h.workspace.run_tests

    async def later(tree: Tree, *, timeout_s: float) -> SuiteRun:
        run = await real(tree, timeout_s=timeout_s)
        fields = {}
        cases = run.cases
        problems = ()
        if mode in {"timeout", "error"}:
            fields["outcome"] = mode
            problems = ("interrupted report",)
        elif mode == "truncated":
            fields.update(outcome="failed", failed=21, failed_tests=["other"])
        elif mode == "retention":
            cases += tuple(
                CaseResult(test_id=f"other::test_{n}", outcome="passed") for n in range(201)
            )
        elif mode == "skipped":
            cases = (CaseResult(test_id=f"{TESTS}::test_value", outcome="skipped"),)
            fields.update(passed=0, skipped=1)
        elif mode == "uncollected":
            cases = ()
            fields.update(passed=0)
        elif mode == "still_failed":
            cases = (CaseResult(test_id=f"{TESTS}::test_value", outcome="failed"),)
            fields.update(outcome="failed", passed=0, failed=1, failed_tests=[cases[0].test_id])
        return replace(
            run, result=run.result.model_copy(update=fields), cases=cases, problems=problems
        )

    async def missing(*args, **kwargs) -> SuiteResult:
        return SuiteResult(
            outcome="error", passed=0, failed=0, skipped=0, duration_ms=0, failed_tests=[]
        )

    if mode == "missing":
        monkeypatch.setattr(_proposal, "run_tests", missing)
    else:
        monkeypatch.setattr(h.workspace, "run_tests", later)
    assert await h.finish(1)
    [facts] = h.payloads(ChangeFacts)
    assert not facts.run_findings and not facts.complete
    assert any(p.startswith("run rule unknown:") for p in facts.problems)


@pytest.mark.anyio
async def test_frozen_capture_duplicate_edits_and_revision_isolation(
    h: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from decision_helpers import observed_target

    from oversight.changes import _proposal
    from oversight.changes._facts import build_change_facts

    captured = []
    real = _proposal.publish_facts

    async def capture(ctx, evidence):
        captured.append(evidence)
        await real(ctx, evidence)

    monkeypatch.setattr(_proposal, "publish_facts", capture)
    await failing_test(h)
    assert await h.edit(1, TESTS, " > 0", " >= 0")
    # Re-reporting an unchanged file cannot create another weakening.
    assert await h.manager.file_edited("agent-1", TESTS)
    assert await h.finish(1)
    [first] = h.payloads(ChangeFacts)
    assert len(first.run_findings) == 1
    await h.manager.request_changes("change-1", "Again", **observed_target(h.manager, "change-1"))
    assert await h.finish(1)
    second = h.payloads(ChangeFacts)[-1]
    assert not second.run_findings
    assert any(p.startswith("run rule unknown:") for p in second.problems)
    rec = h.manager._agents["agent-1"]
    rec.run_edits.clear()
    rec.run_suites.clear()
    for execution in rec.executions.values():
        execution.suite.failed_tests.append("forged")
    for run in rec.suite_runs.values():
        run.result.failed_tests.append("forged")
    rec.executions.clear()
    rec.suite_runs.clear()
    rec.head_files.clear()
    assert build_change_facts(captured[0]) == first


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "wrong_type",
        "foreign_agent",
        "foreign_change",
        "chronology",
        "test",
        "index",
        "tree",
        "source",
        "broad_kind",
    ],
)
async def test_forged_projection_is_refused_before_store(
    h: Harness,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    from oversight.changes import _facts
    from oversight_contracts.events import ErrorReport

    await failing_test(h)
    assert await h.edit(1, TESTS, " > 0", " >= 0")
    real = ChangeFacts

    def forge(**fields):
        facts = real(**fields)
        [finding] = facts.run_findings
        edit_seq = finding.edited_seq
        if mutation == "missing":
            facts.run_findings[0] = finding.model_copy(update={"failed_seq": 100000})
        elif mutation == "wrong_type":
            first_edit = next(e.seq for e in h.events() if e.type == "change.updated")
            facts.run_findings[0] = finding.model_copy(update={"failed_seq": first_edit})
        elif mutation == "chronology":
            facts.run_findings[0] = finding.model_copy(update={"passed_seq": finding.failed_seq})
        elif mutation == "test":
            facts.run_findings[0] = finding.model_copy(update={"test_id": f"{TESTS}::other"})
        elif mutation == "index":
            facts.run_findings[0] = finding.model_copy(update={"fact_index": 100})
        elif mutation == "broad_kind":
            facts.run_findings[0] = finding.model_copy(update={"kind": "removed"})
        elif mutation == "tree":
            facts = facts.model_copy(update={"candidate_tree": "forged-tree"})
        elif mutation == "source":
            facts.integrity[0] = facts.integrity[0].model_copy(update={"path": "other.py"})
        elif mutation == "foreign_agent":
            # A receipt's valid type is insufficient: ownership is part of its payload.
            rec = h.manager._agents["agent-1"]
            seq = finding.failed_seq
            rec.executions[seq] = rec.executions[seq].model_copy(update={"agent_id": "agent-2"})
            facts.run_findings[0] = finding.model_copy(update={"failed_seq": edit_seq})
        else:
            facts = facts.model_copy(update={"change_id": "change-2"})
        return facts

    monkeypatch.setattr(_facts, "ChangeFacts", forge)
    assert not await h.finish(1)
    assert not h.payloads(ChangeFacts)
    assert any("differ" in e.message for e in h.payloads(ErrorReport))


@pytest.mark.anyio
async def test_concrete_class_parameters_keep_independent_uncertainty(h: Harness) -> None:
    tree = await h.open(1)
    old = (await tree.read_text(TESTS)).content
    new = (
        "import pytest\nfrom shop.mod import value\n\nclass TestValue:\n"
        '    @pytest.mark.parametrize("n", [0, 1], ids=["zero", "one"])\n'
        "    def test_value(self, n):\n        assert value() > n\n"
    )
    assert await h.edit(1, TESTS, old, new)
    assert await h.edit(1, "shop/mod.py", "return 1", "return 0")
    await h.manager.tests_executed("agent-1", await h.workspace.run_tests(tree, timeout_s=60))
    assert await h.edit(1, TESTS, " > n", " >= n")
    assert await h.finish(1)
    [facts] = h.payloads(ChangeFacts)
    assert [f.test_id for f in facts.run_findings] == [f"{TESTS}::TestValue::test_value[zero]"]
    assert any("test_value[one]" in p for p in facts.problems)
    assert not facts.complete


@pytest.mark.anyio
async def test_human_edit_cannot_be_a_temporal_witness(h: Harness) -> None:
    from oversight.changes import _proposal

    await failing_test(h)
    rec = h.manager._agents["agent-1"]
    old = (await rec.tree.read_text(TESTS)).content
    async with h.workspace.locks(agent_id=rec.agent_id):
        await rec.tree.write_text(
            TESTS,
            old.replace(" > 0", " >= 0"),
            expected_hash=(await rec.tree.read_text(TESTS)).hash,
        )
        await _proposal.refresh(h.manager._ctx, rec, {TESTS})
        await _proposal.send_file(h.manager._ctx, rec, TESTS, "user", previous=old)
    assert await h.finish(1)
    [facts] = h.payloads(ChangeFacts)
    assert not facts.run_findings
    assert any(p.startswith("run rule unknown:") for p in facts.problems)


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["edit", "suite"])
async def test_cancellation_preserves_committed_receipts(h: Harness, kind: str) -> None:
    import asyncio

    from oversight_contracts.events import ChangeUpdated, SuiteExecution

    await failing_test(h)
    rec = h.manager._agents["agent-1"]
    if kind == "edit":
        h.hold = ChangeUpdated
        task = asyncio.create_task(h.edit(1, TESTS, " > 0", " >= 0"))
    else:
        assert await h.edit(1, TESTS, " > 0", " >= 0")
        run = await h.workspace.run_tests(rec.tree, timeout_s=60)
        h.hold = SuiteExecution
        task = asyncio.create_task(h.manager.tests_executed("agent-1", run))
    await h.held.wait()
    task.cancel()
    h.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await h.finish(1)
    [facts] = h.payloads(ChangeFacts)
    assert len(facts.run_findings) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("listed", [True, False])
async def test_truncated_initial_failure_only_supports_a_listed_case(
    h: Harness, listed: bool
) -> None:
    from dataclasses import replace

    tree = await h.open(1)
    assert await h.edit(1, "shop/mod.py", "return 1", "return 0")
    run = await h.workspace.run_tests(tree, timeout_s=60)
    run = replace(
        run,
        result=run.result.model_copy(
            update={
                "failed": 21,
                "failed_tests": [f"{TESTS}::test_value" if listed else "other"],
            }
        ),
    )
    await h.manager.tests_executed("agent-1", run)
    assert await h.edit(1, TESTS, " > 0", " >= 0")
    assert await h.finish(1)
    [facts] = h.payloads(ChangeFacts)
    assert bool(facts.run_findings) is listed
    if not listed:
        assert any(p.startswith("run rule unknown:") for p in facts.problems)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mutation",
    [
        "foreign_agent",
        "foreign_change",
        "wrong_type",
        "missing_receipt",
        "source",
        "phase",
    ],
)
async def test_validator_recomputes_receipts_and_source_support(
    h: Harness,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    from dataclasses import replace

    from oversight.changes import _facts, _proposal
    from oversight.changes._facts import build_change_facts

    captured = []
    real = _proposal.publish_facts

    async def capture(ctx, evidence):
        captured.append(evidence)
        await real(ctx, evidence)

    monkeypatch.setattr(_proposal, "publish_facts", capture)
    await failing_test(h)
    assert await h.edit(1, TESTS, " > 0", " >= 0")
    assert await h.finish(1)
    [evidence] = captured
    valid = build_change_facts(evidence)
    observed = evidence.run_evidence
    assert observed is not None
    suites = list(observed.suites)
    edits = list(observed.edits)
    if mutation in {"foreign_agent", "foreign_change", "phase"}:
        from oversight.changes._run_findings import EVENT

        event = EVENT.validate_json(suites[0].event.envelope)
        fields = (
            {"agent_id": "agent-2"}
            if mutation == "foreign_agent"
            else (
                {"change_id": "change-2"} if mutation == "foreign_change" else {"phase": "combined"}
            )
        )
        event = event.model_copy(update={"payload": event.payload.model_copy(update=fields)})
        suites[0] = replace(
            suites[0], event=replace(suites[0].event, envelope=event.model_dump_json())
        )
    elif mutation == "wrong_type":
        edits[0] = replace(edits[0], event=suites[0].event)
    elif mutation == "missing_receipt":
        suites.clear()
    else:
        edits[-1] = replace(edits[-1], before=edits[-1].before.replace(" > 0", " >= 0"))
    altered = replace(
        evidence, run_evidence=replace(observed, suites=tuple(suites), edits=tuple(edits))
    )
    before = list(h.log)
    # Force the original claimed projection; recomputation against the altered
    # capture must reject it before the real publisher is reached.
    monkeypatch.setattr(_facts, "ChangeFacts", lambda **kwargs: valid)
    with pytest.raises(ValueError):
        await real(h.manager._ctx, altered)
    assert h.log == before


@pytest.mark.anyio
async def test_missing_source_retains_suite_but_cannot_witness_a_pass(
    h: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from oversight_contracts.events import SuiteExecution

    await failing_test(h)
    assert await h.edit(1, TESTS, " > 0", " >= 0")
    real = h.manager._ctx.run

    async def missing(tree, *args):
        if args[0] == "show":
            raise OSError("source capture unavailable")
        return await real(tree, *args)

    monkeypatch.setattr(h.manager._ctx, "run", missing)
    assert await h.finish(1)
    assert len(h.payloads(SuiteExecution)) == 2
    [facts] = h.payloads(ChangeFacts)
    assert not facts.run_findings
    assert any(p.startswith("run rule unknown:") for p in facts.problems)
