"""Plans schedule one to six stable subtask ids, retaining pre-1.8 payloads."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from oversight_contracts.commands import UpdatePlan
from oversight_contracts.plans import Plan
from oversight_contracts.queue import QueuedTask

ROOT = Path(__file__).resolve().parents[2]


def task(state: str | None = "waiting", agent=None, deps=()):
    return dict(
        task="Work",
        zone=dict(node_ids=[], paths=[]),
        depends_on=list(deps),
        state=state,
        agent_id=agent,
    )


def plan(tasks, state="proposed"):
    return dict(
        plan_id="plan-1", revision=1, task="Work", graph_version="v1", state=state, subtasks=tasks
    )


def draft(tasks):
    return [{k: v for k, v in row.items() if k not in {"state", "agent_id"}} for row in tasks]


@pytest.mark.parametrize(
    "state,subtask",
    [
        ("proposed", task()),
        ("running", task("running", "agent-1")),
    ],
)
def test_one_subtask_fallback(state, subtask):
    parsed = Plan.model_validate(plan([subtask], state))
    assert parsed.subtasks is not None and len(parsed.subtasks) == 1


def test_three_subtask_chain_progresses_after_merges():
    tasks = [task("merged", "agent-1"), task("running", "agent-2", [0]), task(deps=[1])]
    parsed = Plan.model_validate(plan(tasks, "running"))
    assert parsed.subtasks is not None and parsed.subtasks[1].state == "running"
    tasks[1] = task("merged", "agent-2", [0])
    tasks[2] = task("merged", "agent-3", [1])
    assert Plan.model_validate(plan(tasks, "completed")).state == "completed"


@pytest.mark.parametrize(
    "tasks",
    [
        [task(), task(deps=[2]), task(deps=[1])],  # independent root hides a separate cycle
        [task(deps=[0])],
        [task(deps=[1])],
        [task(), task(deps=[0, 0])],
        [task(deps=[-1])],
        [task(deps=[6])],
        [],
        [task()] * 7,
    ],
)
def test_plan_and_update_share_dependency_checks(tasks):
    with pytest.raises(ValidationError):
        Plan.model_validate(plan(tasks))
    with pytest.raises(ValidationError):
        UpdatePlan.model_validate(dict(revision=1, graph_version="v1", subtasks=draft(tasks)))


@pytest.mark.parametrize(
    "tasks,state",
    [
        ([task("running", "agent-1"), task("running", "agent-2", [0])], "running"),
        ([task("merged", "agent-1"), task("merged", "agent-1", [0])], "completed"),
        ([task(None), task()], "proposed"),
        ([task("waiting", "agent-1")], "proposed"),
        ([task("running")], "running"),
        ([task("merged")], "completed"),
        ([task()], "completed"),
        ([task()], "rejected"),
        ([task("rejected", "agent-1"), task(deps=[0])], "running"),
        ([task("cancelled"), task(deps=[0])], "proposed"),
    ],
)
def test_stateful_plan_invariants(tasks, state):
    with pytest.raises(ValidationError):
        Plan.model_validate(plan(tasks, state))


def test_rejected_prerequisite_cancels_dependents_transitively():
    tasks = [task("rejected", "agent-1"), task("cancelled", deps=[0]), task("cancelled", deps=[1])]
    assert Plan.model_validate(plan(tasks, "completed")).state == "completed"
    tasks[0] = task("cancelled", "agent-1")
    assert Plan.model_validate(plan(tasks, "rejected")).state == "rejected"


@pytest.mark.parametrize("count", [1, 6])
def test_update_accepts_bounded_lists(count):
    tasks = [task(deps=[] if i == 0 else [i - 1]) for i in range(count)]
    assert (
        len(
            UpdatePlan.model_validate(
                dict(
                    revision=1,
                    graph_version="v1",
                    subtasks=draft(tasks),
                )
            ).subtasks
        )
        == count
    )


@pytest.mark.parametrize(
    "is_plan,count,state,valid",
    [
        (True, 1, "running", True),
        (True, 6, "starting", True),
        (True, 1, "completed", True),
        (True, 0, "running", False),
        (True, 7, "running", False),
        (True, 0, "completed", False),
        (False, 1, "running", True),
        (False, 2, "running", False),
        (False, 0, "starting", False),
        (False, 2, "completed", False),
    ],
)
def test_queue_dispatch_cardinality(is_plan, count, state, valid):
    spec = (
        dict(plan_id="plan-1", plan_revision=1)
        if is_plan
        else dict(agent=dict(task="Work", slot=None, model=None, zone=None))
    )
    raw = dict(
        **spec,
        queued_task_id="queued-task-1",
        revision=1,
        state=state,
        agent_ids=[f"agent-{i + 1}" for i in range(count)],
        detail=None,
    )
    if valid:
        assert len(QueuedTask.model_validate(raw).agent_ids) == count
    else:
        with pytest.raises(ValidationError):
            QueuedTask.model_validate(raw)


@pytest.mark.anyio
async def test_versioned_fixture_and_historical_inputs_match_serialized_schema(tmp_path):
    import json

    from pydantic import TypeAdapter

    from oversight.workspace import run_process
    from oversight_contracts.events import ServerEvent

    adapter = TypeAdapter(ServerEvent)
    records = []
    for path in sorted((ROOT / "fixtures/contracts").glob("*.json")):
        original = path.read_bytes()
        for raw in json.loads(original):
            event = adapter.validate_python(raw)
            assert adapter.validate_json(adapter.dump_json(event)) == event
            records.append(dict(source=path.name, event=event.model_dump(mode="json")))
        assert path.read_bytes() == original
    current = json.loads((ROOT / "fixtures/contracts/model-plans-1.8.json").read_text())
    assert {row["type"] for row in current} == {
        "model.attempt_failed",
        "agent.step",
        "plan.updated",
        "queue.updated",
    }
    serialized = tmp_path / "serialized.json"
    serialized.write_text(json.dumps(records))
    result = await run_process(
        ["node", "contracts/tests/validate_serialized.cjs", str(serialized)],
        cwd=ROOT,
        timeout_s=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
