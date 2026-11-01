"""Pure server-owned coding-agent provenance checks."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING

from oversight.agents._examples_validate import canonical_json
from oversight_contracts.decisions import DecisionRef
from oversight_contracts.events import AgentStatus
from oversight_contracts.examples import TaskExample
from oversight_contracts.plans import PlanZone

if TYPE_CHECKING:
    from oversight.agents.loop import AgentTask


def validate_agent_status(
    status: AgentStatus,
    *,
    base_commit: str | None,
    plan_id: str | None = None,
    zone: PlanZone | None = None,
    started_by: str | None = None,
    queued_task_id: str | None = None,
    answering: DecisionRef | None = None,
) -> None:
    """Compare with the server registration, never provenance from request data."""
    if (
        status.base_commit,
        status.plan_id,
        status.zone,
        status.started_by,
        status.queued_task_id,
    ) != (
        base_commit,
        plan_id,
        zone,
        started_by,
        queued_task_id,
    ):
        raise ValueError("agent status differs from server-owned base and provenance")
    if status.answering != answering:
        raise ValueError("agent status answering differs from server registration")
    if not base_commit:
        raise ValueError("live coding-agent status requires its recorded base")


def validate_task_examples(
    examples: Sequence[TaskExample], source_task: str
) -> tuple[TaskExample, ...]:
    """Keep task construction small; only detached human pins for this original task survive."""
    if len(examples) > 5 or any(
        example.pin is None
        or example.pin.confirmed_by != "person"
        or example.pin.source_task != source_task
        for example in examples
    ):
        raise ValueError("AgentTask requires at most five examples confirmed by this task's person")
    for example in examples:
        if example.expected_output is None:
            raise ValueError("AgentTask requires JSON expected output")
        canonical_json(json.loads(example.expected_output))
    return tuple(example.model_copy(deep=True) for example in examples)


def task_examples(examples: Sequence[TaskExample]) -> list[TaskExample] | None:
    """Detach event contents from the retained task; empty tasks keep their original wire form."""
    return [example.model_copy(deep=True) for example in examples] if examples else None


def done_when_text(examples: Sequence[TaskExample]) -> str:
    """One deterministic model instruction block, without executable criteria or test ids."""
    if not examples:
        return ""
    lines = ["Done when: the person confirmed these exact results."]
    for example in examples:
        lines.extend(
            (
                f"Example {example.example_id}",
                f"Inputs: {example.inputs}",
                f"Expected output: {canonical_json(json.loads(example.expected_output or 'null'))}",
                f"Note: {example.note or ''}",
            )
        )
    return "\n".join(lines)


def task_message(task: AgentTask) -> str:
    """Keep the original task text and exact confirmed-results separator."""
    return task.task + ("\n\n" + done_when_text(task.examples) if task.examples else "")
