"""Confirmed criteria survive the task, model prompt, statuses and cumulative step limit."""

from dataclasses import replace
from typing import cast

import pytest
from agent_helpers import Recorder, limits, script

from oversight.agents import AgentRun, AgentTask, UsageLedger, done_when_text, task_examples
from oversight.workspace import Tree, Workspace
from oversight_contracts.events import AgentStatus, AgentStep
from oversight_contracts.examples import ExamplePin, TaskExample

EXAMPLE = TaskExample(
    example_id="one",
    inputs='{"args":[],"kwargs":{},"node_id":"shop.mod.value"}',
    expected_output='{"z": 2, "a": 1}',
    note="Exact result",
    pin=ExamplePin(source_task="Do it."),
)


def task() -> AgentTask:
    return AgentTask(
        "agent-1", "change-1", "A", "Do it.", limits(2), examples=(EXAMPLE,), prior_steps=3
    )


@pytest.mark.parametrize(
    "kind", ["unpinned", "other_person", "other_task", "six", "negative_steps"]
)
def test_agent_task_refusals(kind: str) -> None:
    example = EXAMPLE.model_copy(deep=True)
    if kind == "unpinned":
        example = example.model_copy(update={"pin": None})
    elif kind == "other_person":
        example = example.model_copy(
            update={"pin": ExamplePin.model_construct(source_task="Do it.", confirmed_by="model")}
        )
    elif kind == "other_task":
        example = example.model_copy(update={"pin": ExamplePin(source_task="Other task.")})
    with pytest.raises(ValueError):
        replace(
            task(),
            examples=(example,) * (6 if kind == "six" else 1),
            prior_steps=-1 if kind == "negative_steps" else 3,
        )


@pytest.mark.parametrize("output", [None, "not JSON", "", "NaN", "1e999"])
def test_agent_task_rejects_unusable_output(output: str | None) -> None:
    example = TaskExample.model_validate(
        EXAMPLE.model_dump()
        | {"expected_output": output, "expected_behavior": "Return the exact result."}
    )
    with pytest.raises(ValueError):
        replace(task(), examples=(example,))


@pytest.mark.parametrize("output", ["null", '""'])
def test_agent_task_keeps_json_null_and_empty_string(output: str) -> None:
    example = TaskExample.model_validate(EXAMPLE.model_dump() | {"expected_output": output})
    retained = replace(task(), examples=(example,))
    assert retained.examples[0].expected_output == output
    assert f"Expected output: {output}\n" in done_when_text(retained.examples)


def test_done_when_text_and_copies() -> None:
    retained = task()
    assert done_when_text(retained.examples) == (
        "Done when: the person confirmed these exact results.\n"
        "Example one\n"
        'Inputs: {"args":[],"kwargs":{},"node_id":"shop.mod.value"}\n'
        'Expected output: {"a":1,"z":2}\n'
        "Note: Exact result"
    )
    assert done_when_text(()) == "" and task_examples(()) is None
    copies = task_examples(retained.examples)
    assert copies is not None
    assert copies[0] is not retained.examples[0]
    assert copies[0].pin is not retained.examples[0].pin
    copies[0] = copies[0].model_copy(update={"note": "changed"})
    assert retained.examples[0].note == "Exact result"
    assert retained.examples[0].pin == ExamplePin(source_task="Do it.")
    assert retained.examples[0] is not EXAMPLE
    assert retained.examples[0].pin is not EXAMPLE.pin


@pytest.mark.anyio
@pytest.mark.parametrize("confirmed", [False, True])
async def test_examples_run_carry(small_tree: tuple[Workspace, Tree], confirmed: bool) -> None:
    model = script([("list_files", {})], [("list_files", {})], [("finish", {"summary": "Done."})])
    recorder = Recorder()
    run_task = task() if confirmed else replace(task(), examples=(), prior_steps=0)
    workspace, tree = small_tree
    run = AgentRun(
        run_task,
        model=model,
        workspace=workspace,
        tree=tree,
        sink=recorder,
        publish=recorder.publish,
        ledger=UsageLedger(budget_usd=10),
        monotonic=lambda: 0.0,
    )
    end = await run.run()
    assert end.state == "halted" and end.steps == run_task.prior_steps + 2
    assert run.steps == run.max_steps == run_task.prior_steps + 2
    expected = "Do it." + ("\n\n" + done_when_text(run_task.examples) if confirmed else "")
    assert model.requests[0][1] == {"role": "user", "content": expected}
    statuses = cast(list[AgentStatus], recorder.of("AgentStatus"))
    assert statuses[0].step == run_task.prior_steps
    assert all(s.max_steps == run_task.prior_steps + 2 for s in statuses)
    assert all(s.examples == task_examples(run_task.examples) for s in statuses)
    assert [cast(AgentStep, s).step for s in recorder.of("AgentStep")] == [
        run_task.prior_steps + 1,
        run_task.prior_steps + 2,
    ]
    if confirmed:
        assert statuses[0].examples is not None and statuses[-1].examples is not None
        assert statuses[0].examples[0] is not statuses[-1].examples[0]
        assert statuses[0].examples[0].pin is not statuses[-1].examples[0].pin
        statuses[0].examples[0] = statuses[0].examples[0].model_copy(update={"note": "changed"})
        assert statuses[-1].examples[0].note == run_task.examples[0].note == "Exact result"

    revised = await run.revise("Keep these results.", None)
    assert revised.state == "ready_for_review" and revised.steps == run_task.prior_steps + 3
    assert run.max_steps == run_task.prior_steps + 4
    revised_statuses = cast(list[AgentStatus], recorder.of("AgentStatus"))[len(statuses) :]
    assert revised_statuses[0].state == "revising"
    assert all(s.examples == task_examples(run_task.examples) for s in revised_statuses)
    assert model.requests[-1][1]["content"] == expected
