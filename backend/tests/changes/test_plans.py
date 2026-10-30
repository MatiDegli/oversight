"""Plan lifecycle, readiness and failure policy without a runtime or model."""

from typing import Any

import pytest

from oversight.changes import (
    WrongState,
    queue_candidates,
    queue_transition,
    queued,
    refusal_state,
)
from oversight.changes import (
    plan_core as core,
)
from oversight.graph import parse_version
from oversight_contracts.commands import EnqueueTask, UpdatePlan
from oversight_contracts.graph import ParseError
from oversight_contracts.plans import Plan, PlanZone, SubtaskDraft

ZONE = PlanZone(node_ids=[], paths=["m.py"])


def proposed(count: int, *, chain: bool = False) -> Plan:
    graph = parse_version({"m.py": b"def f(): pass\n"}, "head").graph
    plan = core.planning("The person's task\n  verbatim.", graph, 1)
    return core.propose(
        plan,
        [
            SubtaskDraft(task=f"task {i}", zone=ZONE, depends_on=[i - 1] if i and chain else [])
            for i in range(count)
        ],
    )


@pytest.mark.parametrize("count", range(1, 7))
@pytest.mark.parametrize("chain", [False, True])
def test_readiness_and_completion_for_one_to_six(count: int, chain: bool) -> None:
    plan = proposed(count, chain=chain)
    previous = plan.model_copy(deep=True)
    assert core.ready(plan) == ([0] if chain else list(range(count)))
    plan = core.link(plan, plan.revision, "queued-task-1")
    assert core.content_matches(plan, previous)
    for i in range(count):
        assert i in core.ready(plan)
        before = plan
        plan = core.launched(plan, i, f"agent-{i + 1}", more_initial=not chain and i < count - 1)
        assert plan.revision == before.revision + 1
        assert plan.state == ("starting" if not chain and i < count - 1 else "running")
        before = plan
        plan = core.resolved(plan, f"agent-{i + 1}", "merged")
        assert plan.revision == before.revision + 1
        assert plan.state == ("completed" if i == count - 1 else before.state)
    assert previous == proposed(count, chain=chain)
    assert core.ready(plan) == []
    assert plan.subtasks and all(task.state == "merged" for task in plan.subtasks)


@pytest.mark.parametrize("count", range(1, 7))
def test_rejected_prerequisite_cancels_transitively_even_out_of_order(count: int) -> None:
    plan = proposed(count)
    assert plan.subtasks is not None
    # Reverse the chain: the last index is the root and cancellation needs multiple passes.
    drafts = [
        SubtaskDraft(task=t.task, zone=t.zone, depends_on=[i + 1] if i < count - 1 else [])
        for i, t in enumerate(plan.subtasks)
    ]
    plan = core.update(
        plan, UpdatePlan(revision=plan.revision, graph_version=plan.graph_version, subtasks=drafts)
    )
    plan = core.launched(plan, count - 1, "agent-1")
    plan = core.resolved(plan, "agent-1", "rejected")
    assert plan.state == "completed"
    assert plan.subtasks is not None
    assert [t.state for t in plan.subtasks] == ["cancelled"] * (count - 1) + ["rejected"]
    with pytest.raises(WrongState):
        core.resolved(plan, "agent-1", "merged")


def test_join_waits_for_all_prerequisites_and_request_changes_has_no_resolution() -> None:
    plan = proposed(3)
    assert plan.subtasks is not None
    plan = core.update(
        plan,
        UpdatePlan(
            revision=2,
            graph_version=plan.graph_version,
            subtasks=[
                SubtaskDraft(task=t.task, zone=t.zone, depends_on=[0, 1] if i == 2 else [])
                for i, t in enumerate(plan.subtasks)
            ],
        ),
    )
    with pytest.raises(WrongState):
        core.launched(plan, 2, "agent-3")
    plan = core.launched(plan, 0, "agent-1", more_initial=True)
    plan = core.launched(plan, 1, "agent-2")
    plan = core.resolved(plan, "agent-1", "merged")
    assert core.ready(plan) == []
    plan = core.resolved(plan, "agent-2", "merged")
    assert core.ready(plan) == [2]


def test_freeze_unlink_revision_checks_and_started_by_preserved() -> None:
    plan = proposed(1).model_copy(update={"started_by": "agent-99"})
    request = UpdatePlan(
        revision=2,
        graph_version=plan.graph_version,
        subtasks=[SubtaskDraft(task="Edited", zone=ZONE)],
    )
    edited = core.update(plan, request)
    assert edited.started_by == "agent-99" and edited.revision == 3
    with pytest.raises(WrongState):
        core.update(edited, request)
    with pytest.raises(WrongState):
        core.update(plan, request.model_copy(update={"graph_version": "wrong"}))
    linked = core.link(edited, 3, "queued-task-1")
    with pytest.raises(WrongState):
        core.update(linked, request.model_copy(update={"revision": 4}))
    unlinked = core.unlink(linked)
    assert unlinked.revision == 5 and unlinked.state == "proposed"
    assert unlinked.queued_task_id is None and core.content_matches(unlinked, edited)
    rejected = core.reject(unlinked, 5, "No")
    assert rejected.state == "rejected" and rejected.detail == "No"
    assert rejected.subtasks and rejected.subtasks[0].state == "cancelled"
    with pytest.raises(WrongState):
        core.fail(rejected, "too late")


@pytest.mark.parametrize(
    "field,value",
    [
        ("task", "Changed"),
        ("zone", PlanZone(paths=[], node_ids=["m:f"])),
        ("depends_on", [0]),
    ],
)
def test_dispatch_content_guard_checks_all_three_fields(field: str, value: Any) -> None:
    plan = proposed(2)
    changed = plan.model_copy(deep=True)
    assert changed.subtasks is not None
    changed.subtasks[1] = changed.subtasks[1].model_copy(update={field: value})
    assert not core.content_matches(changed, plan)


def test_whole_graph_includes_parse_errors_and_deduplicates_files() -> None:
    graph = parse_version({"m.py": b"def f(): pass\n"}, "head").graph
    graph = graph.model_copy(
        update={"parse_errors": [ParseError(path="bad.py", line=1, message="syntax")]}
    )
    assert core.whole_graph(graph) == PlanZone(paths=["bad.py", "m.py"], node_ids=[])


def test_partial_initial_launch_refusals_and_ready_dependents_keep_priority() -> None:
    plan = core.link(proposed(2), 2, "queued-task-2")
    item = queued(EnqueueTask(plan_id=plan.plan_id, plan_revision=2), 2)
    plan = core.launched(plan, 0, "agent-1", more_initial=True)
    item = queue_transition(item, "starting", agent_id="agent-1")
    for code in ("slot_busy", "spend_brake", "wrong_state"):
        assert refusal_state(item, code) == "starting"
        assert core.ready(plan) == [1]
    running = queue_transition(item, "running")
    assert refusal_state(running, "slot_busy") == "running"
    assert refusal_state(item, "invalid_request") == "failed"
    assert refusal_state(item, "internal", launch_failed=True) == "failed"
    failed = core.fail(plan, "launch failed")
    assert failed.state == "failed" and failed.detail == "launch failed"
    assert failed.subtasks and [t.state for t in failed.subtasks] == ["running", "cancelled"]
    earlier = queued(EnqueueTask(plan_id="plan-99", plan_revision=1), 1)
    assert queue_candidates([earlier, item], {plan.plan_id: plan}) == [item, earlier]
    plan = core.resolved(plan, "agent-1", "merged")
    plan = core.launched(plan, 1, "agent-2")
    assert plan.state == "running"
