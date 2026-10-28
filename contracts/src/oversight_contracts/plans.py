"""Reserved two-agent planning states; no generation, assignment or workspace effects."""

from typing import Literal, Self

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
    depends_on: list[Literal[0, 1]] = Field(default_factory=list[Literal[0, 1]])
    """Stable zero-based subtask ids within the two-slot plan; slots cannot be reordered."""


class PlanSubtask(SubtaskDraft):
    """A subtask and the agent assigned when the plan starts."""

    agent_id: AgentId | None = None


class Plan(ContractModel):
    """The complete versioned state of a two-agent plan."""

    plan_id: PlanId
    revision: PositiveInt
    task: NonEmptyText
    graph_version: NonEmptyText
    state: Literal[
        "planning", "proposed", "starting", "running", "completed", "rejected", "failed"
    ] = "planning"
    subtasks: tuple[PlanSubtask, PlanSubtask] | None = None
    detail: str | None = None

    started_by: AgentId | None = None
    """The lead that requested this plan."""
    queued_task_id: QueuedTaskId | None = None
    """The queue item dispatching this plan."""

    @model_validator(mode="after")
    def _state_has_subtasks(self) -> Self:
        if self.state == "proposed" and self.subtasks is None:
            raise ValueError("a proposed plan requires both subtasks")
        if self.subtasks is not None:
            for index, task in enumerate(self.subtasks):
                if index in task.depends_on or len(task.depends_on) != len(set(task.depends_on)):
                    raise ValueError("dependencies must be unique and exclude self")
            if all(task.depends_on for task in self.subtasks):
                raise ValueError("subtask dependencies must be acyclic")
        if self.state == "running":
            if self.subtasks is None:
                raise ValueError("a running plan requires both subtasks")
            assigned = [task.agent_id for task in self.subtasks if task.agent_id is not None]
            if (
                not assigned
                or len(assigned) != len(set(assigned))
                or any(task.agent_id is None and not task.depends_on for task in self.subtasks)
            ):
                raise ValueError("running requires distinct agents for all independent subtasks")
        return self
