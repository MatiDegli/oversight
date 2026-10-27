"""Reserved two-agent planning states; no generation, assignment or workspace effects."""

from typing import Literal, Self

from pydantic import PositiveInt, model_validator

from oversight_contracts.common import (
    AgentId,
    ContractModel,
    NodeId,
    NonEmptyText,
    PlanId,
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

    @model_validator(mode="after")
    def _state_has_subtasks(self) -> Self:
        if self.state == "proposed" and self.subtasks is None:
            raise ValueError("a proposed plan requires both subtasks")
        if self.state == "running" and (
            self.subtasks is None
            or any(task.agent_id is None for task in self.subtasks)
            or self.subtasks[0].agent_id == self.subtasks[1].agent_id
        ):
            raise ValueError("a running plan requires two distinct assigned agents")
        return self
