"""Contract-backed review evidence, explicit claims and neutral rendering."""

from dataclasses import FrozenInstanceError, replace
from typing import Any

import pytest
from pydantic import TypeAdapter

from oversight.changes._reviewfacts import (
    Discrepancy,
    FinishClaims,
    claims_from_summary,
    facts_from_events,
    facts_lines,
    said_versus_did,
)
from oversight.changes._reviewfacts import tests_line as detector_line
from oversight_contracts.events import ServerEvent

ADAPTER = TypeAdapter(ServerEvent)
GRAPH = {
    "nodes_added": [],
    "nodes_modified": ["shop:price"],
    "nodes_removed": ["shop:old"],
    "edges_added": [],
    "edges_removed": [],
}


def event(seq: int, kind: str, **payload: Any) -> ServerEvent:
    return ADAPTER.validate_python(
        {
            "seq": seq,
            "ts": "2026-10-27T15:00:00.000Z",
            "type": kind,
            "payload": payload,
        }
    )


def suite(outcome: str = "passed", failed: int = 0, ids: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "passed": 3,
        "failed": failed,
        "skipped": 1,
        "duration_ms": 7,
        "failed_tests": list(ids),
    }


def step(
    seq: int,
    tool: str = "read_file",
    tests: dict[str, Any] | None = None,
    agent_id: str = "agent-1",
    result: str = "ok",
) -> ServerEvent:
    return event(
        seq,
        "agent.step",
        agent_id=agent_id,
        step=seq,
        tool=tool,
        summary="Ignored",
        result=result,
        error="tool failed" if result == "error" else None,
        duration_ms=10,
        tests=tests,
    )


def update(
    seq: int,
    path: str = "shop.py",
    agent_id: str = "agent-1",
    change_id: str = "change-1",
    **overrides: Any,
) -> ServerEvent:
    payload = {
        "agent_id": agent_id,
        "change_id": change_id,
        "author": "agent",
        "file": {
            "path": path,
            "status": "modified",
            "original": "a\n",
            "modified": "b\n",
            "lines_added": 1,
            "lines_removed": 1,
        },
        "node_ids": ["shop:price"],
        "graph": GRAPH,
        "files_changed": 1,
        "lines_added": 1,
        "lines_removed": 1,
    }
    return event(seq, "change.updated", **(payload | overrides))


def proposal(seq: int, **overrides: Any) -> ServerEvent:
    payload = {
        "agent_id": "agent-1",
        "change_id": "change-1",
        "revision": 1,
        "task": "Fix",
        "summary": "All tests passed",
        "base_commit": "base",
        "head_commit": "head",
        "files": [
            {"path": "tests/test_shop.py", "status": "added", "lines_added": 6, "lines_removed": 0}
        ],
        "lines_added": 6,
        "lines_removed": 0,
        "graph": GRAPH,
        "tests": suite(),
        "impact": {
            "seeds": [],
            "entries": [],
            "edges_followed": "exact",
            "max_depth": 2,
            "max_nodes": 10,
            "omitted_by_depth": 0,
            "omitted_by_limit": 0,
            "tests_reaching": 0,
        },
    }
    return event(seq, "change.proposed", **(payload | overrides))


def usage(seq: int, cost: float | None, agent_id: str = "agent-1") -> ServerEvent:
    return event(
        seq,
        "usage.tick",
        agent_id=agent_id,
        model=None,
        input_tokens=None,
        output_tokens=None,
        cached_tokens=None,
        cost_usd=cost,
        spent_usd=100,
        budget_usd=200,
        brake="ok",
    )


def facts(*events: ServerEvent):
    return facts_from_events("agent-1", "change-1", events)


def test_latest_totals_and_historical_touches_include_all_graph_node_kinds():
    added = {
        "id": "shop:new",
        "kind": "function",
        "name": "new",
        "qualname": "new",
        "path": "shop.py",
        "start_line": 1,
        "end_line": 2,
        "parent_id": "shop",
        "is_test": False,
    }
    first = update(1, graph=GRAPH | {"nodes_added": [added]})
    last = proposal(3, lines_added=12, lines_removed=4)
    result = facts(last, update(2, "other.py", author="user", files_changed=2), first)
    assert result.files == ("other.py", "shop.py", "tests/test_shop.py")
    assert result.nodes == ("shop:new", "shop:old", "shop:price")
    assert (result.files_changed, result.lines_added, result.lines_removed) == (1, 12, 4)
    # Historical files differ from current size after a reversion.
    assert len(result.files) == 3


def test_updated_totals_are_whole_change_and_can_shrink():
    result = facts(update(1, lines_added=100), update(2, lines_added=2, lines_removed=0))
    assert (result.lines_added, result.lines_removed, result.files_changed) == (2, 0, 1)


def test_missing_size_and_cost_are_unknown():
    result = facts()
    assert result.files == result.nodes == result.tests == result.errors == ()
    assert (result.files_changed, result.lines_added, result.lines_removed, result.cost_usd) == (
        None,
        None,
        None,
        None,
    )
    assert result.steps == result.duration_ms == 0


def test_agent_and_change_filtering_and_global_errors():
    result = facts(
        update(1, agent_id="agent-2"),
        update(2, change_id="change-2"),
        proposal(3, agent_id="agent-2", change_id="change-2"),
        step(4, agent_id="agent-2", result="error"),
        usage(5, 9, agent_id="agent-2"),
        event(
            6,
            "error",
            source="server",
            message="global",
            agent_id=None,
            change_id=None,
            fatal=False,
        ),
        event(
            7,
            "error",
            source="git",
            message="wrong agent",
            agent_id="agent-2",
            change_id="change-1",
            fatal=False,
        ),
        event(
            8,
            "error",
            source="git",
            message="wrong change",
            agent_id="agent-1",
            change_id="change-2",
            fatal=False,
        ),
    )
    assert result == facts()


def test_known_agent_binding_prevents_wrong_change_test_and_cost_attribution():
    result = facts(update(1, change_id="change-2"), step(2, "run_tests", suite()), usage(3, 2))
    assert result.steps == 0
    assert result.tests == () and result.cost_usd is None


def test_test_runs_keep_order_outcomes_ids_and_missing_results():
    ids = tuple(f"tests/test_shop.py::test_price[{i}]" for i in range(20))
    result = facts(
        step(5, "run_tests", suite()),
        step(3, "run_tests", suite("failed", 21, ids)),
        step(2, "search", suite()),
        step(4, "run_tests", result="error"),
    )
    first, missing, last = result.tests
    assert (first.seq, first.step, first.result, first.outcome) == (3, 3, "ok", "failed")
    assert (first.passed, first.failed, first.skipped, first.duration_ms) == (3, 21, 1, 7)
    assert first.failed_tests == ids and first.failed_tests_truncated is True
    assert missing.outcome is missing.passed is missing.failed is missing.skipped is None
    assert missing.duration_ms is missing.failed_tests_truncated is None
    assert missing.failed_tests == () and missing.result == "error"
    assert last.outcome == "passed" and last.failed_tests_truncated is False
    assert len(result.tests) == 3  # A non-run_tests step cannot be test evidence.


@pytest.mark.parametrize(
    ("failed", "ids", "truncated"),
    [
        (0, (), False),
        (1, (), True),
        (1, ("test.py::test_one",), False),
        (20, tuple(f"test.py::test_{i}" for i in range(20)), False),
    ],
)
def test_failed_id_completeness_uses_count_not_cap(failed, ids, truncated):
    assert (
        facts(step(1, "run_tests", suite("failed", failed, ids))).tests[0].failed_tests_truncated
        is truncated
    )


def test_proposal_verification_and_focus_are_not_agent_actions():
    focus = event(
        1,
        "agent.focus",
        agent_id="agent-1",
        mode="edit",
        ranges=[{"path": "unwritten.py", "start_line": None, "end_line": None}],
        node_ids=["unwritten:f"],
    )
    result = facts(focus, proposal(2))
    assert result.tests == () and result.steps == result.duration_ms == 0
    assert "unwritten.py" not in result.files and "unwritten:f" not in result.nodes
    assert said_versus_did(FinishClaims(ran_tests=True), result) == (Discrepancy("tests_not_run"),)


def test_steps_duration_and_error_observations():
    result = facts(
        step(1),
        step(2, result="error"),
        step(3, "run_tests", suite("timeout")),
        event(
            4,
            "error",
            source="agent",
            message="halted",
            agent_id="agent-1",
            change_id=None,
            fatal=True,
        ),
        event(
            5,
            "error",
            source="tests",
            message="report",
            agent_id=None,
            change_id="change-1",
            fatal=False,
        ),
        event(
            6,
            "error",
            source="git",
            message="scoped",
            agent_id="agent-1",
            change_id="change-1",
            fatal=False,
        ),
    )
    assert (result.steps, result.duration_ms) == (3, 30)
    assert [(e.seq, e.source, e.message) for e in result.errors] == [
        (2, "read_file", "tool failed"),
        (4, "agent", "halted"),
        (5, "tests", "report"),
        (6, "git", "scoped"),
    ]


@pytest.mark.parametrize(
    "costs, expected",
    [
        ([], None),
        ([None], None),
        ([0], 0),
        ([0.1, 0.2], 0.3),
        ([1, None], None),
        ([None, 1], None),
        ([float("nan")], None),
        ([float("inf")], None),
        ([-1], None),
    ],
)
def test_cost_is_per_call_and_unknown_is_not_zero(costs, expected):
    result = facts(*(usage(i + 1, cost) for i, cost in enumerate(costs)))
    if expected is None:
        assert result.cost_usd is None
    else:
        assert result.cost_usd == pytest.approx(expected)


def test_duplicate_delivery_is_idempotent_and_conflicts_raise():
    run = step(1, "run_tests", suite())
    cost = usage(2, 0.2)
    assert facts(run, cost, run, cost) == facts(run, cost)
    with pytest.raises(ValueError, match="conflicting events at seq 1"):
        facts(run, step(1))


def test_dataclasses_and_nested_evidence_are_immutable_snapshots():
    run = step(1, "run_tests", suite("failed", 1, ("test.py::test_one",)))
    result = facts(run)
    assert run.type == "agent.step" and run.payload.tests is not None
    run.payload.tests.failed_tests.append("test.py::test_two")
    assert result.tests[0].failed_tests == ("test.py::test_one",)
    for obj, name, value in [
        (result, "steps", 4),
        (result.tests[0], "step", 9),
        (FinishClaims(), "ran_tests", True),
        (Discrepancy("tests_not_run"), "path", "a.py"),
        (facts(step(1, result="error")).errors[0], "source", "other"),
    ]:
        with pytest.raises(FrozenInstanceError):
            setattr(obj, name, value)


@pytest.mark.parametrize(
    "text, ran, passed",
    [
        ("I ran the tests. All tests passed.", True, True),
        ("Ran tests\nTests pass", True, True),
        ("The tests were run. The tests passed.", True, True),
        ("I did not run tests. Tests failed.", False, False),
        ("The tests were not run. Tests did not pass.", False, False),
        ("All tests passed", None, True),
        ("I ran tests. I did not run tests. Tests passed. Tests failed.", None, None),
        ("I will run tests. Tests should pass.", None, None),
        ("I never said tests passed. I have not run tests.", None, None),
        ('He said "tests passed". If tests pass, ship it.', None, None),
        ("Tests did not fail. Not all tests passed. No tests passed.", None, None),
        ("", None, None),
    ],
)
def test_explicit_claims_only(text, ran, passed):
    assert claims_from_summary(text, ()) == FinishClaims(ran_tests=ran, tests_pass=passed)


def test_file_claims_are_exact_allowlisted_lists_and_sorted():
    result = claims_from_summary(
        "Updated `src/b.py` and `src/a.py`.\nFiles touched: src/a.py, tests/test_a.py.",
        ["src/a.py", "src/b.py", "tests/test_a.py"],
    )
    assert result.files == ("src/a.py", "src/b.py", "tests/test_a.py")


@pytest.mark.parametrize(
    "text",
    [
        "Read src/a.py",
        "Did not edit src/a.py",
        "Will edit src/a.py",
        "See src/a.py for details",
        "Updated /tmp/src/a.py",
        "Updated src/a.py.bak",
        "Updated other/src/a.py",
        "Updated src/a.py and unknown.py",
        "Updated src/a.py:12",
    ],
)
def test_incidental_negated_future_and_partial_path_mentions_stay_unknown(text):
    assert claims_from_summary(text, ["src/a.py"]).files is None


def test_explicit_empty_file_claim_and_conflicting_lists():
    assert claims_from_summary("No files changed.", ["a.py"]).files == ()
    assert claims_from_summary("I changed no files.", ["a.py"]).files == ()
    assert claims_from_summary("No files changed. Edited a.py.", ["a.py"]).files is None


def test_all_discrepancies_and_deterministic_path_order():
    result = facts(update(1, "b.py"), update(2, "a.py"), step(3, "run_tests", suite("failed", 1)))
    claims = FinishClaims(files=("z.py", "c.py"), ran_tests=True, tests_pass=True)
    assert said_versus_did(claims, result) == (
        Discrepancy("tests_not_passing"),
        Discrepancy("file_not_touched", "c.py"),
        Discrepancy("file_not_touched", "z.py"),
        Discrepancy("file_not_mentioned", "a.py"),
        Discrepancy("file_not_mentioned", "b.py"),
    )
    assert said_versus_did(FinishClaims(ran_tests=True), facts()) == (Discrepancy("tests_not_run"),)
    assert said_versus_did(FinishClaims(files=()), facts(update(1))) == (
        Discrepancy("file_not_mentioned", "shop.py"),
    )


@pytest.mark.parametrize("outcome", ["failed", "error", "timeout"])
def test_last_nonpassing_test_outcome_is_a_discrepancy(outcome):
    result = facts(step(1, "run_tests", suite()), step(2, "run_tests", suite(outcome)))
    assert said_versus_did(FinishClaims(tests_pass=True), result) == (
        Discrepancy("tests_not_passing"),
    )


def test_last_pass_and_unknown_results_claims_produce_no_discrepancy():
    for result in [
        facts(),
        facts(step(1, "run_tests")),
        facts(step(1, "run_tests", suite("failed")), step(2, "run_tests", suite())),
    ]:
        assert said_versus_did(FinishClaims(tests_pass=True), result) == ()
    touched = facts(update(1))
    assert said_versus_did(FinishClaims(), touched) == ()
    assert (
        said_versus_did(
            FinishClaims(files=("shop.py",), ran_tests=False, tests_pass=False), touched
        )
        == ()
    )


def test_detector_line_mapping_order_and_pluralization():
    assert detector_line(
        {"expected_value_changed": 1, "assertion_loosened": 2, "test_removed": 1}
    ) == ("tests in this change: 1 removed, 2 checks changed, 1 expected value changed")
    assert detector_line(
        {"skip_added": 2, "assertion_loosened": 1, "expected_value_changed": 2}
    ) == ("tests in this change: 1 check changed, 2 expected values changed, 2 skips added")
    assert detector_line({"skip_added": 1}) == "tests in this change: 1 skip added"
    assert detector_line({"test_removed": 2}) == "tests in this change: 2 removed"
    assert (
        detector_line({"literal_branch": 4, "stub_left": 3})
        == detector_line({})
        == ("tests in this change: 0 recorded test changes")
    )


@pytest.mark.parametrize(
    "counts, reason",
    [
        (None, "not supplied"),
        ("detector unavailable", "detector unavailable"),
        ({"parse_error": 1, "test_removed": 2}, "parse error"),
        ({"new_kind": 1}, "unsupported finding kind"),
        ({"skip_added": -1}, "negative count"),
    ],
)
def test_detector_unknown_does_not_become_zero(counts, reason):
    assert detector_line(counts) == f"test facts unknown: {reason}"


def test_header_order_and_neutral_lines_with_truncated_failed_ids():
    result = facts(
        update(1), usage(2, 0.01), step(3, "run_tests", suite("failed", 2, ("test.py::test_one",)))
    )
    discrepancies = said_versus_did(FinishClaims(tests_pass=True), result)
    lines = facts_lines(
        result, discrepancies, detector_line({}), "1 test fails before and passes after"
    )
    assert lines == (
        "size: 1 files, 2 observed nodes, +1/-1 lines; 1 steps, 0 error records; "
        "tool duration 10 ms; cost USD 0.010000",
        "tests run at step 3 (event 3): failed; 3 passed, 2 failed, 1 skipped; "
        "failed ids: test.py::test_one; failed ids truncated",
        "tests in this change: 0 recorded test changes",
        "said versus did: said tests pass; last agent run did not pass",
        "1 test fails before and passes after",
    )
    for word in ("alarm", "risk", "shortcut", "safe", "suspicious", "should"):
        assert word not in " ".join(lines)


def test_header_missing_results_empty_runs_and_all_discrepancy_wording():
    unknown = facts_lines(facts(), (), "test facts unknown: not supplied")
    assert "unknown files" in unknown[0] and "+unknown/-unknown lines" in unknown[0]
    assert unknown[0].endswith("cost unknown")
    assert unknown[1:] == (
        "tests run by agent: 0 recorded calls",
        "test facts unknown: not supplied",
    )
    lines = facts_lines(
        facts(step(1, "run_tests")),
        (
            Discrepancy("tests_not_run"),
            Discrepancy("file_not_touched", "x.py"),
            Discrepancy("file_not_mentioned", "y.py"),
        ),
        "detector facts",
    )
    assert lines[1:] == (
        "tests run at step 1 (event 1): result unknown",
        "detector facts",
        "said versus did: said tests were run; no agent run_tests call recorded",
        "said versus did: named x.py; no touch recorded",
        "said versus did: did not name y.py; touch recorded",
    )
    assert "failed ids:" not in facts_lines(facts(step(1, "run_tests", suite())), (), "")[1]
    assert facts_lines(replace(facts(), cost_usd=0), (), "")[0].endswith("cost USD 0.000000")
