"""Reserved one-to-six-subtask planning states; no generation, assignment or workspace effects."""

from collections.abc import Sequence
from typing import Annotated, Literal, Self

from pydantic import Field, PositiveInt, model_validator

from oversight_contracts.common import (
    AgentId,
    ContractModel,
    NodeId,
    NonEmptyText,
    PlanId,
    QueuedTaskId,
    WorkspacePath,
)


class PlanZone(ContractModel):
    """Explicit graph nodes and workspace files selected for a subtask."""

    node_ids: list[NodeId]
    paths: list[WorkspacePath]


class SubtaskDraft(ContractModel):
    """An editable subtask before the server assigns its agent."""

    task: NonEmptyText
    zone: PlanZone
    depends_on: list[Annotated[int, Field(ge=0, le=5)]] = Field(default_factory=list[int])
    """Stable zero-based subtask ids; subtasks are not reordered within a revision."""


class PlanSubtask(SubtaskDraft):
    """A subtask and the agent assigned when the plan starts."""

    agent_id: AgentId | None = None
    state: Literal["waiting", "running", "merged", "rejected", "cancelled"] | None = None
    """Null only for payloads written before 1.8.0. Waiting has no agent; running and merged
    have one. Prerequisites must merge before a subtask runs. Reject, discard and stop mean
    rejected; dependents of rejected or cancelled subtasks are cancelled. Request changes keeps
    the subtask running. Agent ids are per start, so later subtasks may reuse a released slot.
    """


def validate_dependencies(subtasks: Sequence[SubtaskDraft]) -> None:
    """Validate stable indices and the entire graph, including disconnected cycles."""
    for index, task in enumerate(subtasks):
        if index in task.depends_on or len(task.depends_on) != len(set(task.depends_on)):
            raise ValueError("dependencies must be unique and exclude self")
        if any(dependency >= len(subtasks) for dependency in task.depends_on):
            raise ValueError("dependency index must be below the subtask count")
    visited: set[int] = set()
    active: set[int] = set()

    def visit(index: int) -> None:
        if index in active:
            raise ValueError("subtask dependencies must be acyclic")
        if index in visited:
            return
        active.add(index)
        for dependency in subtasks[index].depends_on:
            visit(dependency)
        active.remove(index)
        visited.add(index)

    for index in range(len(subtasks)):
        visit(index)


class Plan(ContractModel):
    """The complete versioned state of a plan with one to six subtasks."""

    plan_id: PlanId
    revision: PositiveInt
    task: NonEmptyText
    graph_version: NonEmptyText
    state: Literal[
        "planning", "proposed", "starting", "running", "completed", "rejected", "failed"
    ] = "planning"
    subtasks: list[PlanSubtask] | None = Field(default=None, min_length=1, max_length=6)
    detail: str | None = None

    started_by: AgentId | None = None
    """The lead that requested this plan."""
    queued_task_id: QueuedTaskId | None = None
    """The queue item dispatching this plan."""

    @model_validator(mode="after")
    def _state_has_subtasks(self) -> Self:
        if self.state == "proposed" and self.subtasks is None:
            raise ValueError("a proposed plan requires subtasks")
        if self.subtasks is not None:
            validate_dependencies(self.subtasks)
            states = [task.state for task in self.subtasks]
            if any(state is not None for state in states):
                if any(state is None for state in states):
                    raise ValueError("subtask states must be all null or all set")
                self._stateful_subtasks()
                return self
            if len(self.subtasks) != 2:
                raise ValueError("subtask states are required for plans written from 1.8.0")
        if self.state == "running":
            if self.subtasks is None:
                raise ValueError("a running plan requires subtasks")
            assigned = [task.agent_id for task in self.subtasks if task.agent_id is not None]
            if (
                not assigned
                or len(assigned) != len(set(assigned))
                or any(task.agent_id is None and not task.depends_on for task in self.subtasks)
            ):
                raise ValueError("running requires distinct agents for all independent subtasks")
        return self

    def _stateful_subtasks(self) -> None:
        """Apply lifecycle rules only to plans whose subtasks all carry 1.8 states."""
        assert self.subtasks is not None
        assigned = [task.agent_id for task in self.subtasks if task.agent_id is not None]
        if len(assigned) != len(set(assigned)):
            raise ValueError("subtask agents must be distinct")
        if self.state == "running" and not assigned:
            raise ValueError("a running plan requires an assigned agent")
        for task in self.subtasks:
            if task.state == "waiting" and task.agent_id is not None:
                raise ValueError("waiting subtasks have no agent")
            if task.state in {"running", "merged"} and task.agent_id is None:
                raise ValueError("running and merged subtasks require an agent")
            prerequisites = [self.subtasks[index] for index in task.depends_on]
            if task.state in {"running", "merged", "rejected"} and any(
                prerequisite.state != "merged" for prerequisite in prerequisites
            ):
                raise ValueError("prerequisites must merge before a subtask runs")
            if task.state != "cancelled" and any(
                prerequisite.state in {"rejected", "cancelled"} for prerequisite in prerequisites
            ):
                raise ValueError("a rejected or cancelled prerequisite cancels its dependents")
            if self.state == "rejected" and task.state != "cancelled":
                raise ValueError("a rejected plan cancels every subtask")
            if self.state == "completed" and task.state not in {"merged", "rejected", "cancelled"}:
                raise ValueError("a completed plan requires terminal subtask states")
