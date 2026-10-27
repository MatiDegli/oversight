"""Run-rule evidence uses real contract events, without a runtime or detector."""

from dataclasses import FrozenInstanceError, replace
from typing import Literal

import pytest

from oversight.changes._runrule import (
    Edit,
    RunRuleFinding,
    RunRuleResult,
    RunTrace,
    Weakening,
    WeakeningKind,
    evaluate,
    run_rule_line,
    trace_from_events,
)
from oversight.changes._runrule import (
    TestRun as Run,
)
from oversight_contracts.events import (
    AgentFocus,
    AgentFocusEvent,
    AgentStep,
    AgentStepEvent,
    ChangeUpdated,
    ChangeUpdatedEvent,
    FileDiff,
    FocusRange,
    ServerEvent,
    SuiteResult,
)
from oversight_contracts.graph import ChangeGraph

PATH = "tests/test_price.py"
ID = f"{PATH}::test_price"
OTHER = f"{PATH}::test_other"
TS = "2026-10-27T15:00:00.000Z"


def step_event(
    seq: int,
    step: int,
    tool: str = "run_tests",
    tests: SuiteResult | None = None,
    agent_id: str = "agent-1",
    result: Literal["ok", "error"] = "ok",
) -> AgentStepEvent:
    return AgentStepEvent(
        seq=seq,
        ts=TS,
        type="agent.step",
        payload=AgentStep(
            agent_id=agent_id,
            step=step,
            tool=tool,
            summary=f"Edited {PATH}; tests passed",  # text is deliberately misleading
            result=result,
            error=None,
            duration_ms=1,
            tests=tests,
        ),
    )


def suite(
    *ids: str,
    failed: int | None = None,
    outcome: Literal["passed", "failed", "error", "timeout"] = "failed",
) -> SuiteResult:
    return SuiteResult(
        outcome=outcome,
        passed=0,
        failed=len(ids) if failed is None else failed,
        skipped=0,
        duration_ms=1,
        failed_tests=list(ids),
    )


def focus(
    seq: int, *paths: str, mode: Literal["read", "edit"] = "edit", agent: str = "agent-1"
) -> AgentFocusEvent:
    return AgentFocusEvent(
        seq=seq,
        ts=TS,
        type="agent.focus",
        payload=AgentFocus(
            agent_id=agent,
            mode=mode,
            ranges=[FocusRange(path=path, start_line=None, end_line=None) for path in paths],
            node_ids=[],
        ),
    )


def updated(
    seq: int, author: Literal["agent", "user"] = "agent", agent: str = "agent-1"
) -> ChangeUpdatedEvent:
    return ChangeUpdatedEvent(
        seq=seq,
        ts=TS,
        type="change.updated",
        payload=ChangeUpdated(
            change_id="change-1",
            agent_id=agent,
            author=author,
            file=FileDiff(
                path=PATH,
                status="modified",
                original="assert value == 4",
                modified="assert value",
                lines_added=1,
                lines_removed=1,
            ),
            node_ids=[],
            graph=ChangeGraph(
                nodes_added=[],
                nodes_modified=[],
                nodes_removed=[],
                edges_added=[],
                edges_removed=[],
            ),
            files_changed=1,
            lines_added=1,
            lines_removed=1,
        ),
    )


def trace(*entries: Run | Edit) -> RunTrace:
    return RunTrace("agent-1", entries)


def failed(*ids: str, seq: int = 1, step: int = 4) -> Run:
    return Run(seq, step, ids or (ID,), len(ids) or 1, True)


def passed(seq: int = 4, step: int = 7) -> Run:
    return Run(seq, step, (), 0, True)


def fact(kind: WeakeningKind = "loosened", test_id: str = ID) -> Weakening:
    return Weakening(kind=kind, test_id=test_id)


def test_adapter_uses_seq_and_structured_evidence_and_copies_failures() -> None:
    tests = suite(ID, failed=2)
    events = [
        step_event(1, 4, tests=tests),
        updated(2),
        focus(3, PATH),
        step_event(4, 6, "edit_file"),
        step_event(5, 7, tests=suite(outcome="passed")),
    ]
    expected = trace(
        Run(1, 4, (ID,), 2, False), Edit(2, PATH, 6), Edit(3, PATH, 6), Run(5, 7, (), 0, True)
    )
    assert trace_from_events("agent-1", iter(reversed(events))) == expected
    assert events[0].payload.tests == tests
    tests.failed_tests.clear()
    assert expected.entries[0] == Run(1, 4, (ID,), 2, False)


def test_ignores_other_agents_reads_human_edits_and_unstructured_step_text() -> None:
    events: list[ServerEvent] = [
        step_event(1, 1, tests=suite(ID), agent_id="agent-2"),
        focus(2, PATH, agent="agent-2"),
        updated(3, agent="agent-2"),
        focus(4, PATH, mode="read"),
        updated(5, author="user"),
        step_event(6, 1, "edit_file"),
        step_event(7, 2, "read_file", tests=suite(ID)),
        step_event(8, 3),  # absent tests is not a passing run
    ]
    assert trace_from_events("agent-1", events) == trace()
    assert evaluate(trace_from_events("agent-1", events), [fact()]).findings == ()


def test_focus_ranges_and_updated_alone_are_edit_evidence() -> None:
    assert trace_from_events("agent-1", [focus(1, PATH, "other.py"), updated(2)]) == trace(
        Edit(1, PATH), Edit(1, "other.py"), Edit(2, PATH)
    )


def test_edit_step_label_never_crosses_another_own_step_or_uses_other_agent_step() -> None:
    events = [
        focus(1, PATH),
        step_event(2, 9, "edit_file", agent_id="agent-2"),
        step_event(3, 4, "read_file"),
        step_event(4, 5, "edit_file"),
        updated(5),
    ]
    assert trace_from_events("agent-1", events) == trace(Edit(1, PATH), Edit(5, PATH))


@pytest.mark.parametrize("outcome", ["error", "timeout"])
def test_unfinished_runs_never_prove_absence_even_with_all_recorded_failures(
    outcome: Literal["error", "timeout"],
) -> None:
    events = [step_event(1, 4, tests=suite(ID)), focus(2, PATH)]
    events.append(step_event(3, 7, tests=suite(outcome=outcome)))
    result = evaluate(trace_from_events("agent-1", events), [fact()])
    assert result == RunRuleResult("unknown")


@pytest.mark.parametrize(
    "tests,result",
    [(suite(ID, failed=2), "ok"), (suite(ID, ID), "ok"), (suite(), "error")],
)
def test_incomplete_counts_duplicate_ids_and_failed_tool_calls_are_not_complete(
    tests: SuiteResult, result: Literal["ok", "error"]
) -> None:
    entry = trace_from_events("agent-1", [step_event(1, 1, tests=tests, result=result)]).entries[0]
    assert isinstance(entry, Run)
    assert entry.failed_ids == tuple(tests.failed_tests)
    assert entry.failed_count == tests.failed
    assert not entry.complete


@pytest.mark.parametrize("kind", ["removed", "loosened", "skipped", "deleted"])
@pytest.mark.parametrize("source", ["focus", "updated"])
def test_every_weakening_kind_and_both_edit_sources_establish_a_finding(
    kind: WeakeningKind, source: str
) -> None:
    events = [
        step_event(1, 4, tests=suite(ID)),
        focus(2, PATH) if source == "focus" else updated(2),
        step_event(3, 6, "edit_file"),
        step_event(4, 7, tests=suite(OTHER)),  # suite may still fail for another test
    ]
    result = evaluate(trace_from_events("agent-1", events), [fact(kind)])
    assert result == RunRuleResult(
        "findings",
        (RunRuleFinding(ID, kind, failed(), Edit(2, PATH, 6), failed(OTHER, seq=4, step=7)),),
    )


@pytest.mark.parametrize("edit_seq", [0, 1, 4, 5])
def test_edit_must_be_strictly_between_failing_and_later_run(edit_seq: int) -> None:
    result = evaluate(trace(failed(), Edit(edit_seq, PATH, 6), passed()), [fact()])
    assert result.findings == ()


def test_order_is_not_inferred_from_tool_step_numbers() -> None:
    result = evaluate(trace(failed(step=9), Edit(2, PATH, 3), passed(step=1)), [fact()])
    assert result.status == "findings"
    assert result.findings[0].failing_run.step == 9


@pytest.mark.parametrize("complete", [True, False])
def test_test_still_failing_is_no_finding_even_when_list_is_truncated(complete: bool) -> None:
    after = Run(4, 7, (ID,), 1 if complete else 30, complete)
    assert evaluate(trace(failed(), Edit(2, PATH), after), [fact()]) == RunRuleResult("none")


def test_truncated_missing_id_is_unknown_and_complete_run_can_resolve_it() -> None:
    truncated = Run(3, 7, (OTHER,), 21, False)
    entries = (failed(), Edit(2, PATH, 6), truncated)
    assert evaluate(trace(*entries), [fact()]) == RunRuleResult("unknown")
    result = evaluate(trace(*entries, passed(seq=5, step=8)), [fact()])
    assert result.status == "findings"
    assert result.findings[0].later_run == passed(seq=5, step=8)


def test_incomplete_first_run_still_proves_a_listed_test_failed() -> None:
    before = replace(failed(), failed_count=21, complete=False)
    assert evaluate(trace(before, Edit(2, PATH), passed()), [fact()]).status == "findings"


def test_omitted_failure_before_edit_is_unknown_and_never_guessed() -> None:
    before = Run(1, 4, (OTHER,), 21, False)
    assert evaluate(trace(before, Edit(2, PATH), passed()), [fact()]) == RunRuleResult("unknown")


def test_missing_later_run_is_unknown() -> None:
    assert evaluate(trace(failed(), Edit(2, PATH)), [fact()]) == RunRuleResult("unknown")


def test_no_prior_run_cannot_form_a_chain_or_obscure_a_later_finding() -> None:
    assert evaluate(trace(Edit(0, PATH), failed(), passed()), [fact()]) == RunRuleResult("none")
    assert (
        evaluate(trace(Edit(0, PATH), failed(), Edit(2, PATH), passed()), [fact()]).status
        == "findings"
    )


@pytest.mark.parametrize(
    "entries,weakenings",
    [
        ((), ()),
        ((failed(), passed()), (fact(),)),
        ((failed(), Edit(2, "price.py"), passed()), (fact(),)),
        ((failed(OTHER), Edit(2, PATH), passed()), (fact(),)),
        ((failed(), Edit(2, PATH), passed()), (fact(test_id=OTHER),)),
        ((passed(seq=1), Edit(2, PATH), passed()), (fact(),)),
        ((failed(), Edit(2, PATH), passed()), ()),
    ],
)
def test_no_chain_without_all_three_linked_facts(
    entries: tuple[Run | Edit, ...], weakenings: tuple[Weakening, ...]
) -> None:
    assert evaluate(trace(*entries), iter(weakenings)) == RunRuleResult("none")


@pytest.mark.parametrize("by_name", [True, False])
def test_function_weakening_covers_parameter_ids_and_preserves_class_identity(
    by_name: bool,
) -> None:
    target = f"{PATH}::TestPrice::test_price"
    ids = (target + "[one]", target + "[two::[x]]")
    weakening = (
        Weakening(kind="removed", path=PATH, test_name="TestPrice::test_price")
        if by_name
        else fact("removed", target)
    )
    result = evaluate(
        trace(failed(*ids, ID, target + "_extra[x]"), Edit(2, PATH), passed()), [weakening]
    )
    assert result.status == "findings"
    assert tuple(f.test_id for f in result.findings) == ids


def test_parameter_specific_weakening_does_not_cover_other_parameters_or_prefixes() -> None:
    ids = (ID + "[one]", ID + "[two]", ID + "[one][x]", ID + "_extra")
    result = evaluate(trace(failed(*ids), Edit(2, PATH), passed()), [fact(test_id=ids[0])])
    assert tuple(f.test_id for f in result.findings) == (ids[0],)


def test_parameter_failure_after_edit_does_not_hide_another_parameters_finding() -> None:
    one, two = ID + "[one]", ID + "[two]"
    result = evaluate(trace(failed(one, two), Edit(2, PATH), failed(two, seq=4, step=7)), [fact()])
    assert result.status == "findings"
    assert tuple(f.test_id for f in result.findings) == (one,)


@pytest.mark.parametrize("has_finding", [True, False])
@pytest.mark.parametrize(
    "unknown", [Weakening(kind="unknown"), Weakening(kind="unknown", path=PATH)]
)
def test_detector_unknown_always_sets_unknown_and_retains_other_findings(
    has_finding: bool, unknown: Weakening
) -> None:
    facts = [fact(), unknown] if has_finding else [unknown]
    result = evaluate(trace(failed(), Edit(2, PATH), passed()), facts)
    assert result.status == "unknown"
    assert len(result.findings) == int(has_finding)


def test_duplicate_events_and_facts_emit_one_first_witness_per_concrete_test() -> None:
    result = evaluate(
        trace(failed(), Edit(2, PATH, 6), Edit(3, PATH, 6), passed(), passed(seq=5, step=8)),
        [fact(), fact(), Weakening(kind="loosened", path=PATH, test_name="test_price")],
    )
    assert result == RunRuleResult(
        "findings", (RunRuleFinding(ID, "loosened", failed(), Edit(2, PATH, 6), passed()),)
    )


def test_scan_reaches_later_failures_edits_and_runs() -> None:
    result = evaluate(
        trace(
            passed(seq=1),
            Edit(2, PATH),
            failed(seq=3),
            Edit(4, "other.py"),
            Edit(5, PATH, 6),
            failed(seq=6),
            passed(seq=7, step=9),
        ),
        [fact()],
    )
    assert result.status == "findings"
    finding = result.findings[0]
    assert (finding.failing_run.seq, finding.edit.seq, finding.later_run.seq) == (3, 5, 7)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"kind": "removed"},
        {"kind": "removed", "path": PATH},
        {"kind": "removed", "test_id": "test_price"},
        {"kind": "removed", "test_id": ID, "path": PATH},
        {"kind": "unknown", "test_name": "test_price"},
    ],
)
def test_per_test_identity_must_not_be_guessed_from_path_or_bare_name(
    kwargs: dict[str, str],
) -> None:
    with pytest.raises(ValueError):
        Weakening(**kwargs)  # type: ignore[arg-type]


def test_frozen_types_and_input_data_are_unchanged() -> None:
    before, edit, after = failed(), Edit(2, PATH, 6), passed()
    inputs = trace(before, edit, after)
    facts = [fact()]
    result = evaluate(inputs, facts)
    assert inputs == trace(before, edit, after)
    assert facts == [fact()]
    for value in (before, edit, inputs, facts[0], result, result.findings[0]):
        with pytest.raises(FrozenInstanceError):
            setattr(value, next(iter(value.__dataclass_fields__)), None)


def test_neutral_line_names_the_test_kind_steps_and_edit_event() -> None:
    result = evaluate(trace(failed(), Edit(2, PATH, 6), passed()), [fact()])
    assert run_rule_line(result) == (
        f"1 test failed, was loosened, then no longer failed: {ID} (steps 4, 6, 7; edit event 2)"
    )
    assert run_rule_line(RunRuleResult("none")) == "No test failure-weakening sequence found"
    assert run_rule_line(RunRuleResult("unknown")) == "Run comparison unknown"


def test_line_keeps_multiple_findings_unknown_and_missing_edit_steps_on_one_line() -> None:
    result = evaluate(
        trace(failed(ID + "[a\nb]", OTHER), Edit(2, PATH), passed()),
        [fact(), fact("deleted", OTHER), Weakening(kind="unknown")],
    )
    line = run_rule_line(result)
    assert "was deleted" in line and "was loosened" in line
    assert "steps 4, unknown, 7; edit event 2" in line
    assert line.endswith("Run comparison unknown")
    assert "[a b]" in line and "\n" not in line
