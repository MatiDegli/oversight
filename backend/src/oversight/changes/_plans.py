"""Pure, immutable plan transitions for one to six subtasks."""

from typing import Any, Literal

from oversight.changes._state import WrongState
from oversight_contracts.commands import UpdatePlan
from oversight_contracts.graph import GraphSnapshot
from oversight_contracts.plans import Plan, PlanSubtask, PlanZone, SubtaskDraft

PLAN_TERMINAL = frozenset({"completed", "rejected", "failed"})
SUBTASK_TERMINAL = frozenset({"merged", "rejected", "cancelled"})


def _next(plan: Plan, **updates: Any) -> Plan:
    return Plan.model_validate(
        plan.model_dump() | updates | {"revision": plan.revision + 1}
    ).model_copy(deep=True)


def planning(task: str, snapshot: GraphSnapshot, number: int) -> Plan:
    return Plan(plan_id=f"plan-{number}", revision=1, task=task, graph_version=snapshot.version)


def propose(plan: Plan, drafts: list[SubtaskDraft]) -> Plan:
    if plan.state != "planning":
        raise WrongState("only a planning plan can be proposed")
    return _next(plan, state="proposed", subtasks=_waiting(drafts))


def whole_graph(snapshot: GraphSnapshot) -> PlanZone:
    paths = {node.path for node in snapshot.nodes} | {error.path for error in snapshot.parse_errors}
    return PlanZone(node_ids=[], paths=sorted(paths))


def _waiting(drafts: list[SubtaskDraft]) -> list[PlanSubtask]:
    if any(not draft.task.strip() for draft in drafts):
        raise ValueError("subtask tasks must not be empty")
    return [PlanSubtask(**draft.model_dump(), state="waiting") for draft in drafts]


def editable(plan: Plan, revision: int) -> None:
    if plan.state != "proposed" or plan.revision != revision or plan.queued_task_id is not None:
        raise WrongState("requires an unqueued proposed plan at the observed revision")


def update(plan: Plan, request: UpdatePlan) -> Plan:
    editable(plan, request.revision)
    if request.graph_version != plan.graph_version:
        raise WrongState("the graph version differs from the plan's")
    return _next(plan, subtasks=_waiting(request.subtasks), detail=None)


def link(plan: Plan, revision: int, queued_task_id: str) -> Plan:
    editable(plan, revision)
    return _next(plan, queued_task_id=queued_task_id)


def unlink(plan: Plan) -> Plan:
    if plan.state != "proposed" or plan.queued_task_id is None:
        raise WrongState("only an undispatched plan can leave the queue")
    return _next(plan, queued_task_id=None, detail=None)


def reject(plan: Plan, revision: int, comment: str | None) -> Plan:
    if plan.state != "proposed" or plan.revision != revision:
        raise WrongState("reject requires a proposed plan at the observed revision")
    assert plan.subtasks is not None
    return _next(
        plan,
        state="rejected",
        detail=comment,
        subtasks=[task.model_copy(update={"state": "cancelled"}) for task in plan.subtasks],
    )


def fail(plan: Plan, detail: str) -> Plan:
    if plan.state in PLAN_TERMINAL:
        raise WrongState("a terminal plan cannot fail")
    return _next(
        plan,
        state="failed",
        detail=detail,
        subtasks=[
            task.model_copy(update={"state": "cancelled"}) if task.state == "waiting" else task
            for task in plan.subtasks
        ]
        if plan.subtasks is not None
        else None,
    )


def ready(plan: Plan) -> list[int]:
    if plan.state not in {"proposed", "starting", "running"} or plan.subtasks is None:
        return []
    return [
        index
        for index, task in enumerate(plan.subtasks)
        if task.state == "waiting"
        and all(plan.subtasks[dependency].state == "merged" for dependency in task.depends_on)
    ]


def content_matches(plan: Plan, revision: Plan) -> bool:
    def content(value: Plan) -> list[SubtaskDraft]:
        return [
            SubtaskDraft(task=task.task, zone=task.zone, depends_on=task.depends_on)
            for task in value.subtasks or []
        ]

    return content(plan) == content(revision)


def launched(plan: Plan, index: int, agent_id: str, *, more_initial: bool = False) -> Plan:
    if index not in ready(plan):
        raise WrongState("only a ready waiting subtask can launch")
    assert plan.subtasks is not None
    subtasks = [task.model_copy(deep=True) for task in plan.subtasks]
    subtasks[index] = subtasks[index].model_copy(update={"state": "running", "agent_id": agent_id})
    return _next(
        plan, state="starting" if more_initial else "running", subtasks=subtasks, detail=None
    )


def resolved(plan: Plan, agent_id: str, outcome: Literal["merged", "rejected"]) -> Plan:
    assert plan.subtasks is not None
    subtasks = [task.model_copy(deep=True) for task in plan.subtasks]
    index = next((i for i, task in enumerate(subtasks) if task.agent_id == agent_id), None)
    if index is None or subtasks[index].state != "running":
        raise WrongState("resolution requires a running subtask owned by that agent")
    subtasks[index] = subtasks[index].model_copy(update={"state": outcome})
    # Iterate to a fixed point: dependencies need not be listed in topological order.
    while True:
        cancelled = [
            i
            for i, task in enumerate(subtasks)
            if task.state == "waiting"
            and any(subtasks[d].state in {"rejected", "cancelled"} for d in task.depends_on)
        ]
        if not cancelled:
            break
        for i in cancelled:
            subtasks[i] = subtasks[i].model_copy(update={"state": "cancelled"})
    completed = all(task.state in SUBTASK_TERMINAL for task in subtasks)
    state = "completed" if completed and plan.state != "failed" else plan.state
    return _next(plan, state=state, subtasks=subtasks)
