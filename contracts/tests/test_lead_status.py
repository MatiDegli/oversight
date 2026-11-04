"""The approved lead plan attachment is optional and round-trips."""

from oversight_contracts import CONTRACT_VERSION
from oversight_contracts.events import LeadStatus


def test_lead_status_version_and_default_null() -> None:
    assert CONTRACT_VERSION == "1.22.0"
    status = LeadStatus(
        agent_id="agent-1",
        revision=1,
        task="Coordinate",
        model="fake",
        state="starting",
        step=0,
        detail=None,
    )
    assert status.limits is None
    assert status.model_dump()["limits"] is None
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


def test_lead_limits_round_trip_and_optional_attachment() -> None:
    from oversight_contracts.events import LeadLimits

    limits = LeadLimits(
        turns=50,
        tool_calls=12,
        model_calls=18,
        wall_clock_s=300,
        plans=3,
        subtasks=6,
        task_chars=2000,
    )
    status = LeadStatus(
        agent_id="agent-1",
        revision=1,
        task="Coordinate",
        model="fake",
        state="idle",
        step=0,
        detail=None,
        limits=limits,
    )
    assert LeadStatus.model_validate_json(status.model_dump_json()) == status
    assert LeadLimits.model_validate({name: 10**9 for name in LeadLimits.model_fields})


def test_lead_limits_reject_nonpositive_values_and_remain_frozen() -> None:
    import pytest
    from pydantic import ValidationError

    from oversight_contracts.events import LeadLimits

    values = dict.fromkeys(LeadLimits.model_fields, 1)
    for name in values:
        for value in (0, -1):
            with pytest.raises(ValidationError):
                LeadLimits.model_validate(values | {name: value})
    limits = LeadLimits.model_validate(values)
    with pytest.raises(ValidationError, match="frozen"):
        limits.turns = 2


def test_historical_events_default_new_limits_and_capacity_to_null() -> None:
    import json
    from pathlib import Path

    from pydantic import TypeAdapter

    from oversight_contracts.events import ServerEvent

    root = Path(__file__).resolve().parents[2]
    adapter = TypeAdapter(ServerEvent)
    seen = set()
    for path in sorted((root / "fixtures/contracts").glob("*.json")):
        original = path.read_bytes()
        for raw in json.loads(original):
            event = adapter.validate_python(raw)
            if event.type == "run.started":
                assert event.payload.max_leads is None
                assert event.payload.model_dump()["max_leads"] is None
                seen.add(event.type)
            elif event.type == "lead.status":
                assert event.payload.limits is None
                assert event.payload.model_dump()["limits"] is None
                seen.add(event.type)
        assert path.read_bytes() == original
    assert seen == {"run.started", "lead.status"}
