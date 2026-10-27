"""Suite evidence keeps phase totals separate from individual JUnit outcomes."""

import sys
from dataclasses import replace
from pathlib import Path

import pytest

from oversight.workspace import Workspace, parse_junit, validate_suite_execution
from oversight.workspace.suite import SuiteRun, _take_cases
from oversight_contracts.events import CaseResult, SuiteExecution, SuiteResult


def captured() -> tuple[SuiteExecution, SuiteRun]:
    cases = (CaseResult(test_id="tests/test_x.py::test_x", outcome="error"),)
    result = SuiteResult(
        outcome="failed",
        passed=0,
        failed=2,
        skipped=0,
        duration_ms=1,
        failed_tests=[cases[0].test_id],
    )
    run = SuiteRun(result, "unchanged model output", cases, (), "tree", "commit")
    execution = SuiteExecution(
        change_id="change-1",
        agent_id="agent-1",
        revision=1,
        phase="proposal",
        tested_tree="tree",
        tested_commit="commit",
        suite=result.model_copy(update={"cases": list(cases), "cases_complete": True}),
    )
    return execution, run


def validate(execution: SuiteExecution, run: SuiteRun) -> None:
    validate_suite_execution(
        execution, run, change_id="change-1", agent_id="agent-1", revision=1, phase="proposal"
    )


def test_validator_does_not_compare_phase_counts_with_cases() -> None:
    execution, run = captured()
    validate(execution, run)


@pytest.mark.parametrize(
    "field,value",
    [
        ("tested_tree", "other"),
        ("tested_commit", None),
        ("change_id", "change-2"),
        ("agent_id", "agent-2"),
        ("revision", 2),
        ("phase", "agent"),
    ],
)
def test_validator_rejects_forged_identity(field: str, value: object) -> None:
    execution, run = captured()
    with pytest.raises(ValueError):
        validate(execution.model_copy(update={field: value}), run)


def test_validator_rejects_changed_counts_cases_and_false_completeness() -> None:
    execution, run = captured()
    for update in (
        {"passed": 1},
        {"cases": [CaseResult(test_id="invented", outcome="passed")]},
        {"cases": []},
        {"cases_complete": False},
    ):
        with pytest.raises(ValueError):
            validate(
                execution.model_copy(update={"suite": execution.suite.model_copy(update=update)}),
                run,
            )
    with pytest.raises(ValueError):
        validate(execution, replace(run, problems=("ambiguous_test_id",)))


def test_parser_is_shared_with_beforeafter_and_retains_phase_error(tmp_path: Path) -> None:
    from oversight.changes._beforeafter import parse_junit as probe_parser

    assert probe_parser is parse_junit  # Extraction prevents runner/probe drift.
    report = tmp_path / "suite.xml"
    report.write_text(
        '<testsuite><testcase file="tests/test_x.py" classname="tests.test_x" '
        'name="test_x"><failure/></testcase><testcase file="tests/test_x.py" '
        'classname="tests.test_x" name="test_x"><error/></testcase></testsuite>'
    )
    cases, problems = _take_cases(report)
    assert not problems and [(c.test_id, c.outcome) for c in cases] == [
        ("tests/test_x.py::test_x", "error")
    ]
    assert not report.exists()
    assert _take_cases(report) == ((), ("JUnit report unavailable",))
    report.write_text("<testsuite>")
    assert _take_cases(report) == ((), ("invalid_xml",))


@pytest.mark.anyio
async def test_dirty_tree_is_recorded_without_changing_staging(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(workspace, "_demo_python", Path(sys.executable))
    tree = workspace.main
    async with workspace.locks(main=True):
        old_index = await workspace.git_ok(tree, "write-tree")
        original = await tree.read_text("shop/mod.py")
        await tree.write_text(
            "shop/mod.py", original.content + "\n# observed\n", expected_hash=original.hash
        )
    run = await workspace.run_tests(tree, timeout_s=60)
    assert run.tested_tree != old_index and run.tested_commit is None
    assert run.cases and not run.problems
    async with workspace.locks(main=True):
        assert await workspace.git_ok(tree, "write-tree") == old_index
        assert "# observed" in await workspace.git_ok(
            tree, "show", f"{run.tested_tree}:shop/mod.py"
        )
    assert not list(workspace.tmp_dir.glob("suite-index-*"))
