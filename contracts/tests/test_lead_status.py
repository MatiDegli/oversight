"""The approved lead plan attachment is optional and round-trips."""

from oversight_contracts import CONTRACT_VERSION
from oversight_contracts.events import LeadStatus


def test_lead_status_version_and_default_null() -> None:
    assert CONTRACT_VERSION == "1.13.0"
    status = LeadStatus(
        agent_id="agent-1",
        revision=1,
        task="Coordinate",
        model="fake",
        state="starting",
        step=0,
        detail=None,
    )
    assert status.requested_plan_id is None
    assert status.model_dump()["requested_plan_id"] is None


def test_lead_status_requested_plan_round_trip() -> None:
    status = LeadStatus(
        agent_id="agent-1",
        revision=3,
        task="Coordinate",
        model="fake",
        state="working",
        step=1,
        detail="Requested plan-1.",
        requested_plan_id="plan-1",
    )
    assert LeadStatus.model_validate_json(status.model_dump_json()) == status
    assert status.requested_plan_id == "plan-1"
