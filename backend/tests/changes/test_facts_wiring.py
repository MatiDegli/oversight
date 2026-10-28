"""Real runner/store integration for every revision's neutral review evidence."""

from dataclasses import replace

import pytest
from changes_helpers import EDIT, TESTS, Harness
from decision_helpers import observed_target

from oversight.changes._facts import MISSING_PRODUCERS, FactsEvidence, validate_change_facts
from oversight.changes._shortcut_detector import detect_shortcuts
from oversight.changes._suite_evidence import retain_cases
from oversight_contracts.events import CaseResult, ChangeProposed, SuiteExecution
from oversight_contracts.review import ChangeFacts, ChangeTarget, FinishDeclaration


def test_retention_keeps_added_then_failures_then_other_cases() -> None:
    full = tuple(
        CaseResult(test_id=f"tests/test_x.py::test_{i:03}", outcome="passed") for i in range(205)
    )
    full += (CaseResult(test_id="tests/test_x.py::test_failed", outcome="failed"),)
    cases, complete = retain_cases(full, added_test_ids=frozenset({full[-2].test_id}))
    assert not complete and len(cases) == 200
    assert [c.test_id for c in cases[:2]] == [full[-2].test_id, full[-1].test_id]
    assert retain_cases(full[:200], added_test_ids=frozenset())[1]
    assert not retain_cases((full[0], full[0]), added_test_ids=frozenset())[1]


def test_ungrouped_detector_preserves_individual_observations() -> None:
    before = "def test_a():\n    assert value() == 1\n\ndef test_b():\n    assert value() == 2\n"
    after = before.replace("== 1", ">= 1").replace("== 2", ">= 2")
    grouped = detect_shortcuts([("tests/test_x.py", before, after)])
    raw = detect_shortcuts([("tests/test_x.py", before, after)], grouped=False)
    assert len(grouped) == 1 and len(raw) == 2
    assert {f.line for f in raw} == {2, 5}
    assert all("Lines:" not in f.message for f in raw)


@pytest.mark.anyio
async def test_proposal_emits_facts_after_proposed_and_links_agent_suite(h: Harness) -> None:
    tree = await h.open(1)
    assert await h.edit(1, *EDIT)
    run = await h.workspace.run_tests(tree, timeout_s=60)
    await h.manager.tests_executed("agent-1", run)
    declared = FinishDeclaration(files=[], tests_run=False, tests=["not-observed"])
    assert await h.manager.proposed(
        "agent-1", "Contradictory claims survive.", declaration=declared
    )
    h.manager.run_ended("agent-1", None)
    events = h.events()
    facts_event = next(e for e in events if e.type == "change.facts_recorded")
    assert events[facts_event.seq - 2].type == "change.proposed"
    facts = facts_event.payload
    assert facts.declared == declared and facts.edited_paths == ["shop/mod.py"]
    executions = {e.seq: e.payload for e in events if e.type == "tests.executed"}
    assert facts.test_runs == sorted(executions) and [e.phase for e in executions.values()] == [
        "agent",
        "proposal",
    ]
    assert all(e.suite.cases_complete for e in executions.values())
    assert not facts.complete and set(facts.problems) == set(MISSING_PRODUCERS)
    [proposal] = h.payloads(ChangeProposed)
    assert executions[facts.test_runs[-1]].tested_commit == proposal.head_commit
    await h.manager.request_changes(
        "change-1", "Please revise.", **observed_target(h.manager, "change-1")
    )
    assert await h.manager.proposed(
        "agent-1", "Again.", declaration=FinishDeclaration(tests_run=True)
    )
    h.manager.run_ended("agent-1", None)
    first, second = h.payloads(ChangeFacts)
    assert (first.revision, second.revision) == (1, 2)
    assert set(first.test_runs) < set(second.test_runs)


@pytest.mark.anyio
async def test_individual_test_facts_and_unknown_are_not_success(h: Harness) -> None:
    await h.open(1)
    old = (await h.workspace.worktree("agent-1").read_text(TESTS)).content
    new = old.replace(" > 0", " >= 0") + "\n\ndef test_other():\n    assert 1 == 1\n"
    assert await h.edit(1, TESTS, old, new)
    assert await h.finish(1)
    [facts] = h.payloads(ChangeFacts)
    assert any(
        f.test_id == "tests/test_mod.py::test_value" and f.kind == "assertion_loosened"
        for f in facts.integrity
    )
    assert all("Lines:" not in f.text for f in facts.integrity)
    # A collection config edit makes the detector explicitly unknown.
    await h.manager.request_changes("change-1", "Again.", **observed_target(h.manager, "change-1"))
    assert await h.edit(1, "pyproject.toml", "pythonpath", "# config\npythonpath")
    assert await h.finish(1)
    assert any(p.startswith("detector unknown:") for p in h.payloads(ChangeFacts)[-1].problems)


@pytest.mark.anyio
async def test_combined_suite_records_the_staged_tree_without_claiming_a_commit(h: Harness) -> None:
    await h.run_agent(1, EDIT)
    await h.run_agent(2, ("shop/mod.py", "return value() + value()", "return 2 * value()"))
    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    await h.manager.approve("change-2", None, **observed_target(h.manager, "change-2"))
    combined = [e for e in h.payloads(SuiteExecution) if e.phase == "combined"]
    assert len(combined) == 1 and combined[0].tested_commit is None
    assert combined[0].revision == 1 and combined[0].change_id == "change-2"


@pytest.mark.anyio
async def test_facts_validator_rejects_wrong_target_foreign_runs_and_fake_completeness(
    h: Harness,
) -> None:
    await h.run_agent(1, EDIT)
    [facts] = h.payloads(ChangeFacts)
    rec = h.manager._agents["agent-1"]
    target = ChangeTarget.model_validate(facts.model_dump(include=set(ChangeTarget.model_fields)))
    evidence = FactsEvidence(
        "agent-1",
        target,
        rec.declared,
        tuple(facts.edited_paths),
        tuple(facts.integrity),
        tuple(facts.problems),
        rec.executions,
        rec.suite_runs,
    )
    validate_change_facts(facts, evidence)
    for update in (
        {"candidate_tree": "another-tree"},
        {"test_runs": []},
        {"complete": True},
        {"problems": []},
        {"edited_paths": []},
    ):
        with pytest.raises(ValueError):
            validate_change_facts(facts.model_copy(update=update), evidence)
    seq = facts.test_runs[0]
    forged = rec.executions[seq].model_copy(update={"agent_id": "agent-2"})
    with pytest.raises(ValueError):
        validate_change_facts(facts, replace(evidence, executions={seq: forged}))


@pytest.mark.anyio
async def test_module_skip_retains_a_fact_for_each_test(h: Harness) -> None:
    await h.open(1)
    old = (await h.workspace.worktree("agent-1").read_text(TESTS)).content
    new = (
        "import pytest\npytestmark = pytest.mark.skip\n"
        + old
        + "\n\ndef test_other():\n    assert True\n"
    )
    assert await h.edit(1, TESTS, old, new)
    assert await h.finish(1)
    [facts] = h.payloads(ChangeFacts)
    skip_ids = [f.test_id for f in facts.integrity if f.kind == "skip_added"]
    assert None not in skip_ids
    assert sorted(str(test_id) for test_id in skip_ids) == [
        "tests/test_mod.py::test_other",
        "tests/test_mod.py::test_value",
    ]


@pytest.mark.anyio
async def test_source_facts_and_status_share_one_capture(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def spy(changes, **kwargs):
        calls.append(tuple(changes))
        return detect_shortcuts(changes, **kwargs)

    monkeypatch.setattr("oversight.changes._facts.detect_shortcuts", spy)
    await h.run_agent(1, EDIT)
    rec = h.manager._ctx.state.agents["agent-1"]
    assert calls == [((EDIT[0], rec.base_files[EDIT[0]], rec.head_files[EDIT[0]]),)]
