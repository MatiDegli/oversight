"""Failure evidence follows attempt lifecycle and bench-plan membership."""

import json

import pytest
from tools.eval.grade import Tariff
from tools.eval.ledger import Ledger
from tools.eval.report import failure_counts, failure_table
from tools.eval.tests.test_arms import OFF, ON
from tools.eval.tests.test_planning import pilot
from tools.eval.tests.test_runner import TASK


@pytest.fixture
def pair(tmp_path):
    ledger = Ledger(tmp_path / "results.jsonl", 20, None)
    try:
        left, right = pilot(ledger), pilot(ledger, ON)
        left.agent_id, right.agent_id = "a", "b"
        ledger.state.plans["pair"] = {"arms": [a.model_dump(mode="json") for a in (OFF, ON)]}
        yield ledger, left, right
    finally:
        ledger.close()


def store(ledger, record, events):
    (ledger.out.parent / f"events-{record.run_id}.jsonl").write_text(
        "".join(json.dumps({"run_id": record.run_id, "event": event}) + "\n" for event in events)
    )


@pytest.mark.parametrize(
    "states,expected",
    [
        (["working"], None),
        (["working", "ready_for_review", "done"], "ready_for_review"),
        (["halted", "done"], "halted"),
        (["done"], "done"),
        (["ready_for_review", "stopped", "done"], "stopped"),
    ],
)
def test_terminal_status_matches_attempt_observation(pair, states, expected):
    ledger, record, _ = pair
    store(
        ledger,
        record,
        [
            {
                "type": "agent.status",
                "payload": {"agent_id": record.agent_id, "state": state, "detail": state + "\n"},
            }
            for state in states
        ],
    )
    assert failure_counts(ledger, record)["terminal_status"] == (
        None if expected is None else {"state": expected, "detail": expected + "\n"}
    )


@pytest.mark.parametrize(
    "tool,result,tests,with_passing",
    [
        ("edit_file", "ok", None, 1),
        ("edit_file", "error", None, 2),
        ("run_tests", "ok", {"outcome": "failed"}, 1),
        ("run_tests", "error", {"outcome": "passed"}, 1),
        ("run_tests", "ok", None, 1),
    ],
)
def test_finish_requires_passing_tests_since_last_edit(pair, tool, result, tests, with_passing):
    ledger, record, _ = pair
    steps = [
        ("run_tests", "ok", {"outcome": "passed"}),
        ("finish", "ok", None),
        (tool, result, tests),
        ("finish", "error", None),
    ]
    store(
        ledger,
        record,
        [
            {
                "type": "agent.step",
                "payload": {"agent_id": record.agent_id, "tool": t, "result": r, "tests": s},
            }
            for t, r, s in steps
        ],
    )
    counts = failure_counts(ledger, record)
    assert counts["finish_with_passing_tests"] == with_passing
    assert counts["finish_without_passing_tests"] == 2 - with_passing


def test_failure_table_excludes_detection_records_and_tasks(pair):
    ledger, left, _ = pair
    ledger.state.records.append(
        left.model_copy(update={"arm_id": "detection", "task_id": "detection-case"})
    )
    ledger.state.plans["detection"] = {"kind": "detection", "pilot": False}
    table = failure_table(ledger)["arms"]
    assert set(table) == {OFF.arm_id, ON.arm_id}
    assert all(set(tasks) == {TASK.id} for tasks in table.values())


def test_pairing_requires_matching_recorded_plan_tariffs(pair):
    ledger, _, _ = pair
    ledger.state.plans["pair"]["arms"][1]["tariff"] = Tariff(input=1, output=2).model_dump()
    table = failure_table(ledger)["arms"]
    assert table[OFF.arm_id][TASK.id][0]["pairing"] == "unpaired"


def test_manifest_without_plan_never_establishes_pairing(pair):
    ledger, _, _ = pair
    ledger.state.plans.clear()
    # configuration() omits the different tariffs: a manifest alone cannot establish a pair.
    (ledger.out.parent / "manifest.json").write_text(
        json.dumps(
            {
                "runs": {
                    arm.arm_id: {
                        "experiment": {"arm_id": arm.arm_id, "configuration": arm.configuration()}
                    }
                    for arm in (OFF, ON.model_copy(update={"tariff": Tariff(input=1, output=2)}))
                }
            }
        )
    )
    assert failure_table(ledger)["arms"] == {}
