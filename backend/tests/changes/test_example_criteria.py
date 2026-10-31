"""E4 is inert until confirmation; real Git/suites, with no model or network."""

import ast
import asyncio
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from changes_helpers import Harness, assert_main_clean
from decision_helpers import observed_target
from test_combined_tests import LOOSE, add_on_main
from test_rules_reservation import terminal_code
from test_rules_transaction import configure_transaction, execute
from test_rules_transaction import reserved as rules_reserved

from oversight.agents import canonical_json
from oversight.changes import _mode
from oversight.changes._example_criteria import (
    RequiredCriteria,
    eligible_target,
    install_criteria,
    render_criteria,
    verify_run,
)
from oversight.changes._policy import _combined_tests
from oversight.changes._state import WrongState
from oversight.changes._versions import parse
from oversight.workspace import (
    ProcessResult,
    SuiteRun,
    WorkspaceError,
    parse_cases,
    render_examples,
    run_process,
    validate_suite_execution,
)
from oversight_contracts.events import (
    CaseResult,
    ChangeProposed,
    ChangeResolved,
    ChangeUpdated,
    ErrorReport,
    SuiteExecution,
    SuiteResult,
)
from oversight_contracts.examples import ExamplePin, TaskExample
from oversight_contracts.review import ChangeFacts

reserved = rules_reserved


def example(**updates) -> TaskExample:
    return TaskExample.model_validate(
        dict(
            example_id="basic",
            inputs='{"node_id":"shop.mod:value","args":[],"kwargs":{}}',
            expected_output="1",
            pin=ExamplePin(source_task="Original task"),
        )
        | updates
    )


def hostile() -> list[TaskExample]:
    return [
        example(
            example_id="A'\n\"\"\"\\é__import__('os')",
            inputs=canonical_json(
                {
                    "node_id": "shop.mod:value",
                    "args": ["'\n\"\"\"\\é__import__('os')"],
                    "kwargs": {},
                }
            ),
            expected_output=canonical_json({"z": "é\n'\\", "a": [True, None, 1.0]}),
        ),
        example(example_id="!!!"),
    ]


def test_render_deterministic_and_hostile_safe() -> None:
    first = render_criteria(hostile(), "change-1")
    assert first == render_criteria(hostile(), "change-1")
    assert (
        first.source.encode("ascii")
        == Path(__file__).with_name("example_criteria_golden.txt").read_bytes()
    )
    assert first.sha256 == hashlib.sha256(first.source.encode("ascii")).hexdigest()
    assert first.sha256 == "75cd366c305f64e70d4db8315fa236039d797b0b676d4bec02b10b45d3c5e202"
    parsed = ast.parse(first.source)
    calls = {ast.unparse(node.func) for node in ast.walk(parsed) if isinstance(node, ast.Call)}
    assert calls == {"importlib.import_module", "getattr", "function", "json.loads", "json.dumps"}
    assert not any(
        isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef)) for node in ast.walk(parsed)
    )
    assert [node.decorator_list for node in parsed.body if isinstance(node, ast.FunctionDef)] == [
        [],
        [],
    ]
    assert first.cases[1][1] == f"{first.path}::test_example_02_case"
    assert not _mode.RULES_ACTIVATION


@pytest.mark.anyio
async def test_render_in_another_process(tmp_path: Path) -> None:
    expected = render_criteria(hostile(), "change-1")
    script = (
        "import sys; from oversight.workspace import render_examples; "
        "from oversight_contracts.examples import TaskExample; import json; "
        "print(render_examples([TaskExample.model_validate(e) for e in "
        "json.loads(sys.argv[1])], 'change-1').source, end='')"
    )
    result = await run_process(
        [sys.executable, "-c", script, json.dumps([case.model_dump() for case in hostile()])],
        cwd=tmp_path,
        timeout_s=10,
    )
    assert result.returncode == 0 and result.stdout == expected.source


def test_render_source_cap() -> None:
    cases = [example(example_id=str(i), expected_output='"' + "é" * 7998 + '"') for i in range(5)]
    with pytest.raises(ValueError, match="source exceeds byte limit"):
        render_examples(cases, "change-1")


@pytest.mark.parametrize("value", [{"z": "é", "a": [1, 1.0, None, True, False, ""]}, "", 1, 1.0])
def test_render_parity_with_canonical_json(value) -> None:
    cases = [example(expected_output=json.dumps(value, ensure_ascii=False))]
    assert parse_cases(cases)[0].expected == canonical_json(value)
    assert render_examples(cases, "change-1") == render_criteria(cases, "change-1")
    left = example(inputs='{"kwargs":{"b":1,"a":2},"args":[],"node_id":"shop.mod:value"}')
    right = example(inputs='{"node_id":"shop.mod:value","args":[],"kwargs":{"a":2,"b":1}}')
    assert render_criteria([left], "change-1") == render_criteria([right], "change-1")


@pytest.mark.parametrize(
    "updates",
    [
        {"inputs": "[]"},
        {"inputs": "{"},
        {"inputs": '{"node_id":"shop.mod:value","args":[]}'},
        {"inputs": '{"node_id":"shop.mod:value","args":[],"kwargs":{},"extra":0}'},
        {"inputs": '{"node_id":"shop.mod:value","args":{},"kwargs":{}}'},
        {"inputs": '{"node_id":"shop.mod:value","args":[],"kwargs":[]}'},
        {"inputs": '{"node_id":"shop.mod:value","args":[],"kwargs":{"x":1,"x":2}}'},
        {"inputs": '{"node_id":"shop.mod:value","args":[NaN],"kwargs":{}}'},
        {"inputs": '{"node_id":"shop.mod:value","args":[],"kwargs":{"x":1e999}}'},
        {"inputs": " " * 4001},
        {"expected_output": None, "expected_behavior": "Does something"},
        {"expected_output": ""},
        {"expected_output": "{"},
        {"expected_output": '{"x":1,"x":2}'},
        {"expected_output": "[Infinity]"},
        {"expected_output": "[-Infinity]"},
        {"expected_output": "[1e999]"},
        {"expected_output": '"' + "x" * 8000 + '"'},
    ]
    + [
        {"inputs": canonical_json({"node_id": target, "args": [], "kwargs": {}})}
        for target in [
            "shop:Class.method",
            "shop:outer.inner",
            "shop:f#2",
            "a..b:f",
            "a:if",
            "if:f",
            1,
        ]
    ],
)
def test_render_refuses_unsupported(updates) -> None:
    with pytest.raises(ValueError):
        render_criteria([example(**updates)], "change-1")


@pytest.mark.parametrize(
    "cases,change_id",
    [
        ([], "change-1"),
        ([example()] * 6, "change-1"),
        ([example()] * 2, "change-1"),
        ([example()], "../x"),
        ([example()], "change-0"),
    ],
)
def test_render_refuses_count_ids_and_path(cases, change_id) -> None:
    with pytest.raises(ValueError):
        render_criteria(cases, change_id)


@pytest.mark.anyio
async def test_install_confirmed_sync_top_level_only(h: Harness, monkeypatch) -> None:
    tree = await h.open(1)
    ctx, rec = h.manager._ctx, h.manager._ctx.state.changes["change-1"]
    assert await eligible_target(rec, "shop.mod:value") is None
    assert await eligible_target(rec, "missing:f") == "target_unknown"
    assert await eligible_target(rec, "shop.mod") == "target_not_function"
    for update, error in [
        ({"pin": None}, "confirmation"),
        ({"inputs": '{"node_id":"missing:f","args":[],"kwargs":{}}'}, "target_unknown"),
    ]:
        with pytest.raises(ValueError, match=error):
            await install_criteria(ctx, rec, [example(**update)])
    original = rec.base_files["shop/mod.py"]
    rec.base_files["shop/mod.py"] = "async def value():\n    return 1\n"
    assert await eligible_target(rec, "shop.mod:value") == "target_async"
    with pytest.raises(ValueError, match="target_async"):
        await install_criteria(ctx, rec, [example()])
    rec.base_files["shop/mod.py"] = "def outer():\n    def value():\n        return 1\n"
    assert await eligible_target(rec, "shop.mod:value") == "target_not_top_level"
    rec.base_files["shop/mod.py"] = "def value(:"
    assert await eligible_target(rec, "shop.mod:value") == "target_unparseable"
    rec.base_files["shop/mod.py"] = original
    path = render_criteria([example()], rec.change_id).path
    for files in [rec.base_files, rec.head_files]:
        files[path] = "existing"
        with pytest.raises(ValueError, match="exists"):
            await install_criteria(ctx, rec, [example()])
        del files[path]
    rec.state = "ready"
    with pytest.raises(WrongState):
        await install_criteria(ctx, rec, [example()])
    rec.state = "working"
    with monkeypatch.context() as patch:
        patch.setattr(tree, "write_text", AsyncMock(side_effect=OSError("write failed")))
        with pytest.raises(OSError):
            await install_criteria(ctx, rec, [example()])
        assert rec.criteria is None and h.payloads(ChangeUpdated) == []
    required = await install_criteria(ctx, rec, [example()])
    assert rec.criteria is required and path in rec.observed_paths and path in rec.touched
    assert (await tree.read_text(path)).hash == required.sha256
    [updated] = h.payloads(ChangeUpdated)
    assert updated.author == "user" and updated.file.path == path
    with pytest.raises(WrongState):
        await install_criteria(ctx, rec, [example()])


@pytest.mark.anyio
async def test_install_rejects_method_nested_and_pending_intent(h: Harness) -> None:
    from unittest.mock import Mock

    await h.open(1)
    ctx, rec = h.manager._ctx, h.manager._ctx.state.changes["change-1"]
    rec.base_files["shop/mod.py"] += "\nclass Shop:\n    def method(self):\n        pass\n"
    rec.base = await parse(rec.base_files, rec.base_commit, rec.base)
    assert await eligible_target(rec, "shop.mod:Shop.method") == "target_not_function"
    original = rec.base
    module = next(node for node in rec.base.graph.nodes if node.id == "shop.mod")
    nodes = [
        node.model_copy(update={"parent_id": "missing"}) if node.id == "shop.mod:value" else node
        for node in rec.base.graph.nodes
    ]
    rec.base = replace(rec.base, graph=rec.base.graph.model_copy(update={"nodes": nodes}))
    assert await eligible_target(rec, "shop.mod:value") == "target_not_top_level"
    rec.base = original
    rec.person_intent = Mock()
    with pytest.raises(WrongState):
        await install_criteria(ctx, rec, [example()])
    rec.person_intent = None
    assert module.kind == "module" and rec.criteria is None


def run(cases=(), **updates) -> SuiteRun:
    return SuiteRun(
        SuiteResult(
            outcome="passed", passed=len(cases), failed=0, skipped=0, duration_ms=1, failed_tests=[]
        ),
        "",
        tuple(cases),
        (),
        "a" * 40,
        **updates,
    )


@pytest.mark.anyio
async def test_verify_run_codes(h: Harness, monkeypatch) -> None:
    await h.open(1)
    ctx, rec = h.manager._ctx, h.manager._ctx.state.changes["change-1"]
    rendered = render_criteria([example()], rec.change_id)
    test_id = rendered.cases[0][1]
    required = RequiredCriteria(rendered.path, rendered.sha256, (test_id,), ("basic",))
    passed = run([CaseResult(test_id=test_id, outcome="passed")])
    spy = AsyncMock(return_value=ProcessResult(0, rendered.source, "", False, False, 0))
    monkeypatch.setattr(ctx, "run", spy)
    assert await verify_run(ctx, rec, passed, phase="proposal") is passed
    rec.criteria = required
    for phase, observed in [
        ("agent", passed),
        ("base_probe", passed),
        ("proposal", replace(passed, tested_tree=None)),
    ]:
        assert await verify_run(ctx, rec, observed, phase=phase) is observed
    spy.assert_not_called()
    assert await verify_run(ctx, rec, passed, phase="combined") is passed
    assert spy.call_count == 1
    for outcome in ["failed", "error", "timeout"]:
        observed = replace(passed, result=passed.result.model_copy(update={"outcome": outcome}))
        assert await verify_run(ctx, rec, observed, phase="proposal") is observed
    assert spy.call_count == 1
    for code, result, observed in [
        ("tree_unreadable", ProcessResult(128, "", "bad object", False, False, 0), passed),
        ("tree_unreadable", ProcessResult(0, "", "", True, False, 0), passed),
        ("tree_unreadable", ProcessResult(0, "", "", False, True, 0), passed),
        (
            "file_missing",
            ProcessResult(128, "", "path does not exist in tree", False, False, 0),
            passed,
        ),
        ("file_edited", ProcessResult(0, "changed", "", False, False, 0), passed),
        ("report_unverifiable", spy.return_value, replace(passed, problems=("bad report",))),
        ("report_unverifiable", spy.return_value, replace(passed, cases=passed.cases * 2)),
        ("case_uncollected", spy.return_value, replace(passed, cases=())),
        *[
            (
                "case_not_passed",
                spy.return_value,
                replace(
                    passed,
                    cases=(CaseResult.model_validate(dict(test_id=test_id, outcome=outcome)),),
                ),
            )
            for outcome in ["failed", "error", "skipped"]
        ],
    ]:
        spy.return_value = result
        diagnostics = len(h.payloads(ErrorReport))
        normalized = await verify_run(ctx, rec, observed, phase="proposal")
        assert len(h.payloads(ErrorReport)) == diagnostics + 1
        assert normalized is not observed and normalized.result.outcome == "error"
        assert normalized.problems == (*observed.problems, f"example_criteria: {code}")
        assert normalized.cases is observed.cases and normalized.tested_tree == observed.tested_tree
        assert normalized.result.failed_tests == observed.result.failed_tests
        assert h.payloads(ErrorReport)[-1].message == f"example_criteria: {code}; required_cases=1"
    spy.return_value = ProcessResult(0, rendered.source, "", False, False, 0)
    full = replace(
        passed,
        cases=tuple(CaseResult(test_id=f"other::{i}", outcome="passed") for i in range(205))
        + passed.cases,
    )
    assert await verify_run(ctx, rec, full, phase="proposal") is full


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure", [OSError("unavailable"), WorkspaceError("git failed"), TimeoutError("timed out")]
)
async def test_verify_read_failure(h: Harness, monkeypatch, failure) -> None:
    await h.open(1)
    rec = h.manager._ctx.state.changes["change-1"]
    rec.criteria = RequiredCriteria("tests/missing.py", "0" * 64, ("required",), ("z", "a"))
    monkeypatch.setattr(h.manager._ctx, "run", AsyncMock(side_effect=failure))
    normalized = await verify_run(h.manager._ctx, rec, run(), phase="combined")
    assert normalized.problems == ("example_criteria: tree_unreadable",)
    assert h.payloads(ErrorReport)[0].message == (
        "example_criteria: tree_unreadable; required_cases=1"
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "fault,code",
    [
        ("edited", "file_edited"),
        ("missing", "file_missing"),
        ("deselected", "case_uncollected"),
        ("skipped", "case_not_passed"),
    ],
)
async def test_proposal_suite_normalized_and_consistent(h: Harness, fault: str, code: str) -> None:
    tree = await h.open(1)
    rec = h.manager._ctx.state.changes["change-1"]
    h.manager._ctx.measured_cone = True
    old = rec.head_files["tests/test_mod.py"]
    new = "import pytest\n" + old.replace(
        "def test_value()", '@pytest.mark.parametrize("unused", [1])\ndef test_value(unused)'
    ).replace("assert value() > 0", "assert value() >= 0")
    new += '\n@pytest.mark.parametrize("n", [1])\ndef test_added(n):\n    assert value() == n\n'
    assert await h.edit(1, "tests/test_mod.py", old, new)
    required = await install_criteria(h.manager._ctx, rec, [example()])
    read = await tree.read_text(required.path)
    if fault == "edited":
        await tree.write_text(required.path, read.content + "\n# edited\n", expected_hash=read.hash)
    elif fault == "missing":
        await asyncio.to_thread((h.worktree_dir("agent-1") / required.path).unlink)
    else:
        action = (
            "items.remove(item)"
            if fault == "deselected"
            else ('item.add_marker(pytest.mark.skip(reason="criteria skipped"))')
        )
        hook = (
            "import pytest\ndef pytest_collection_modifyitems(items):\n"
            "    for item in list(items):\n"
            f"        if 'test_oversight_examples_' in item.nodeid:\n            {action}\n"
        )
        await tree.write_text("tests/conftest.py", hook, expected_hash=None)
    assert await h.finish(1)
    [proposed] = h.payloads(ChangeProposed)
    assert proposed.tests.outcome == "error"
    [execution] = h.payloads(SuiteExecution)
    [seq] = rec.executions
    stored = rec.suite_runs[seq]
    assert stored.result.outcome == "error" and not execution.suite.cases_complete
    validate_suite_execution(
        execution,
        stored,
        change_id=rec.change_id,
        agent_id=rec.agent_id,
        revision=rec.revision,
        phase="proposal",
    )
    [error] = h.payloads(ErrorReport)
    assert error.message == f"example_criteria: {code}; required_cases=1"
    [facts] = h.payloads(ChangeFacts)
    assert {
        f"suite {seq}: example_criteria: {code}",
        f"suite {seq}: incomplete retained cases",
        "integrity parameter ids unavailable: tests/test_mod.py::test_value",
        "added tests: parameter ids unavailable for tests/test_mod.py::test_added",
    } <= set(facts.problems)
    assert any(fact.test_id is None for fact in facts.integrity if fact.path == "tests/test_mod.py")
    assert {"tests/test_mod.py::test_value[1]", "tests/test_mod.py::test_added[1]"} <= {
        case.test_id for case in stored.cases
    }
    measured = rec.measured_cones[rec.revision]
    assert measured.problems == ("proposal test selection unavailable",)
    assert not measured.selection and not measured.suites


@pytest.mark.anyio
@pytest.mark.parametrize("met", [True, False])
async def test_combined_personal_path(h: Harness, met: bool) -> None:
    await h.open(1)
    rec = h.manager._ctx.state.changes["change-1"]
    await install_criteria(h.manager._ctx, rec, [example()])
    assert await h.finish(1)
    if not met:
        assert rec.criteria is not None
        rec.criteria = replace(rec.criteria, sha256="0" * 64)
    pre = await add_on_main(h, "tests/test_twice.py", LOOSE)
    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    [resolved] = h.payloads(ChangeResolved)
    assert resolved.merge == ("merged" if met else "conflict")
    if not met:
        assert await h.main_head() == pre
        assert resolved.conflict is not None and resolved.conflict.tests is not None
        assert resolved.conflict.kind == "tests" and resolved.conflict.tests.outcome == "error"
    await assert_main_clean(h)


@pytest.mark.anyio
async def test_no_combined_tests_with_criteria_when_main_unmoved(h: Harness, monkeypatch) -> None:
    await h.open(1)
    await install_criteria(h.manager._ctx, h.manager._ctx.state.changes["change-1"], [example()])
    assert await h.finish(1)
    spy = AsyncMock(side_effect=AssertionError("no combined suite"))
    monkeypatch.setattr(h.workspace, "run_tests", spy)
    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    spy.assert_not_called()
    assert h.payloads(ChangeResolved)[0].merge == "merged"


@pytest.mark.anyio
@pytest.mark.parametrize("met", [True, False])
async def test_combined_rules_path(h: Harness, reserved, monkeypatch, met: bool) -> None:
    from test_policy_profiles import policy_inputs

    assert not _mode.RULES_ACTIVATION
    configure_transaction(h, monkeypatch)
    rec = h.manager._ctx.state.changes["change-1"]
    # Existing real observed test: criteria do not require E2/E3 or a new proposal phase.
    test_id = "tests/test_mod.py::test_value"
    rec.criteria = RequiredCriteria(
        "tests/test_mod.py",
        hashlib.sha256(rec.head_files["tests/test_mod.py"].encode()).hexdigest()
        if met
        else "0" * 64,
        (test_id,),
        ("basic",),
    )
    pre = await h.main_head()
    await execute(h, reserved)
    [combined] = [event for event in h.payloads(SuiteExecution) if event.phase == "combined"]
    if met:
        assert (
            combined.suite.outcome == "passed" and h.payloads(ChangeResolved)[0].merge == "merged"
        )
    else:
        assert combined.suite.outcome == "error" and terminal_code(h) == "suite_failed"
        assert await h.main_head() == pre and not h.payloads(ChangeResolved)
        assert _combined_tests(replace(policy_inputs(), suite=combined.suite)).result != "pass"
    assert not _mode.RULES_ACTIVATION
    await assert_main_clean(h)
