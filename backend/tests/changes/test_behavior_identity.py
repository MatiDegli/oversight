"""Only equal typed inputs contribute counts and publishable behavior witnesses."""

import time
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from oversight.changes import behavior_inputs
from oversight.changes._behavior import BehaviorDiff, FunctionBehavior, _compare
from oversight.changes._behavior_inputs import BehaviorInputs
from oversight.changes._behavior_publish import capture_behavior, project_behavior
from oversight.changes._review_samples import select
from oversight.workspace import BehaviorObservation


def observation(values):
    return {
        "sample:f": BehaviorObservation(
            tuple(
                {"kind": "value", "value": ["int", hex(i)], "fingerprint": f"{i:064x}"}
                for i in values
            )
        )
    }


async def compare(inputs, worker):
    node = SimpleNamespace(id="sample:f", path="sample.py", name="f")
    rec = SimpleNamespace(head=SimpleNamespace(graph=SimpleNamespace(nodes=[node])))
    found = [FunctionBehavior(node.id)]
    await _compare(
        cast(Any, SimpleNamespace(run_behavior=worker)),
        cast(Any, rec),
        cast(Any, ["base", "head"]),
        {node.id: inputs},
        found,
        time.monotonic() + 30,
        set(),
    )
    return found[0]


@pytest.mark.anyio
@pytest.mark.parametrize("repeat", ["stable", "unstable", "incomplete"])
async def test_added_optional_parameter_counts_and_publishes_only_after_both_repeats(repeat):
    generated = behavior_inputs(
        "sample",
        "f",
        {"sample.py": "def f(): return 1\n"},
        {"sample.py": "def f(extra=2): return 1 + extra\n"},
    )
    assert len(generated.inputs) == 1
    worker = AsyncMock(
        side_effect=[
            observation([1]),
            observation([3]),
            observation([1]),
            {"sample:f": BehaviorObservation(problem="unavailable")}
            if repeat == "incomplete"
            else observation([4 if repeat == "unstable" else 3]),
        ]
    )
    measured = await compare(generated, worker)
    assert measured.compared == 1
    assert measured.differing == int(repeat == "stable")
    assert sum(group.count for group in measured.groups or ()) == int(repeat == "stable")
    assert measured.unconfirmed == int(repeat != "stable")
    assert all(row.inputs_equal for row in measured.rows)
    assert worker.await_count == 4
    evidence = capture_behavior(BehaviorDiff(1, (measured,)))
    assert evidence is not None
    public = project_behavior(evidence, revision=1)
    assert public is not None
    assert public[0].compared_count == 1
    assert public[0].differing_count == int(repeat == "stable")
    witnesses = tuple(row.observation for row in evidence.functions[0].rows if row.observation)
    selection = select(witnesses, 8)
    assert len(selection.samples) == int(repeat == "stable")
    assert not any("comparison or repetition incomplete" in reason for reason in selection.problems)
    if repeat == "stable":
        assert public[0].rows[0].inputs == "f()"
        assert measured.reason is None


@pytest.mark.anyio
@pytest.mark.parametrize("retain_equal", [False, True])
@pytest.mark.parametrize("different_result", [False, True])
async def test_unequal_input_identities_contribute_no_counts_rows_or_repeats(
    retain_equal, different_result
):
    generated = behavior_inputs(
        "sample",
        "f",
        {"sample.py": "def f(x=1): return x\n"},
        {"sample.py": "def f(x=2): return x\n", "test_sample.py": "from sample import f\nf()\n"},
    )
    omitted = next(item for item in generated.inputs if item.call == "f()")
    explicit = next(item for item in generated.inputs if item.call == "f(x=1)")
    assert omitted.input_identity is None
    inputs = BehaviorInputs((omitted, explicit) if retain_equal else (omitted,))
    old, new = (
        ([1, 1], [2 if different_result else 1, 3])
        if retain_equal
        else ([1], [2 if different_result else 1])
    )
    worker = AsyncMock(
        side_effect=[observation(old), observation(new), observation([1]), observation([3])]
    )
    measured = await compare(inputs, worker)
    assert measured.compared == measured.differing == int(retain_equal)
    assert len(measured.rows) == int(retain_equal)
    assert sum(group.count for group in measured.groups or ()) == int(retain_equal)
    assert measured.unconfirmed == 0
    assert all(row.inputs_equal and row.call == "f(x=1)" for row in measured.rows)
    assert measured.reason == (None if retain_equal else "no equal typed inputs")
    assert worker.await_count == (4 if retain_equal else 2)


@pytest.mark.anyio
async def test_equal_inputs_with_unrepresentable_results_keep_existing_reason():
    generated = behavior_inputs(
        "sample",
        "f",
        {"sample.py": "def f(x=1): return x\n"},
        {"sample.py": "def f(x=1): return x\n"},
    )
    inputs = replace(generated, inputs=generated.inputs[:1])
    worker = AsyncMock(
        side_effect=[
            observation([1]),
            {"sample:f": BehaviorObservation(({"kind": "unsupported"},))},
        ]
    )
    measured = await compare(inputs, worker)
    assert measured.compared == measured.differing == 0
    assert measured.rows == ()
    assert measured.reason == "unrepresentable input"
