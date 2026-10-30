"""Coding-agent status must match every server-owned provenance field."""

from typing import Any

import pytest

from oversight.agents import validate_agent_status
from oversight_contracts.events import AgentStatus
from oversight_contracts.plans import PlanZone

ZONE = PlanZone(paths=["m.py"], node_ids=[])
STATUS = AgentStatus(
    agent_id="agent-1",
    change_id="change-1",
    slot="A",
    task="Task",
    model="fake",
    state="working",
    step=0,
    max_steps=30,
    detail=None,
    base_commit="base",
    plan_id="plan-1",
    zone=ZONE,
    queued_task_id="queued-task-1",
    started_by=None,
)


@pytest.mark.parametrize(
    "updates",
    [
        {"plan_id": None},
        {"plan_id": "plan-2"},
        {"zone": None},
        {"started_by": "agent-99"},
        {"queued_task_id": None},
        {"base_commit": "other"},
    ],
)
def test_status_provenance_refuses_mismatch(updates: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="provenance"):
        validate_agent_status(
            STATUS.model_copy(update=updates),
            base_commit="base",
            plan_id="plan-1",
            zone=ZONE,
            queued_task_id="queued-task-1",
        )


def test_status_accepts_matching_plan_and_standalone_provenance() -> None:
    validate_agent_status(
        STATUS,
        base_commit="base",
        plan_id="plan-1",
        zone=ZONE,
        queued_task_id="queued-task-1",
    )
    validate_agent_status(
        STATUS.model_copy(
            update={
                "plan_id": None,
                "zone": None,
                "queued_task_id": None,
            }
        ),
        base_commit="base",
    )
