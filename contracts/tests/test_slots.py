"""Contracts 1.21 seats: strict identifiers, shared wire validation and historical replay."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from oversight.workspace import run_process
from oversight_contracts import CONTRACT_VERSION
from oversight_contracts.commands import AgentCreated, MissingSetting, StartAgent
from oversight_contracts.common import Slot
from oversight_contracts.events import AgentStatus, RunStarted, ServerEvent, SlotInfo
from oversight_contracts.queue import QueuedAgentTask

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "fixtures/contracts/slots-extended-1.21.json"
RUN_EVENT = json.loads(FIXTURE.read_text())[0]
SLOT_MODELS = [
    (StartAgent, dict(task="work"), "slot"),
    (AgentCreated, dict(agent_id="agent-1", change_id="change-1"), "slot"),
    (MissingSetting, dict(name="META_API_KEY", problem="missing"), "slots"),
    (SlotInfo, dict(default_model="fake"), "slot"),
    (RunStarted, RUN_EVENT["payload"], "slots"),
    (
        AgentStatus,
        dict(
            agent_id="agent-1",
            slot="A",
            change_id="change-1",
            task="work",
            model="fake",
            state="working",
            step=1,
            max_steps=30,
            detail=None,
        ),
        "slot",
    ),
    (QueuedAgentTask, dict(task="work", model=None, zone=None), "slot"),
]
VALID_SLOTS = [*"ABCDEF", "G", "Z", "AA", "AB", "ZZ"]
INVALID_SLOTS = [
    "",
    *"abcdef",
    "aa",
    "1",
    "123",
    " ",
    " A",
    "A ",
    "A B",
    "A1",
    "Á",
    "\uff21",  # Fullwidth Latin A.
    "\u0410",  # Cyrillic A.
    1,
    1.0,
    True,
    False,
    {},
    [],
]
SLOT_CASES = [(slot, True) for slot in VALID_SLOTS] + [(slot, False) for slot in INVALID_SLOTS]


def slot_payload(model: Any, base: Any, field: str, slot: Any) -> dict[str, Any]:
    value = slot
    if model is MissingSetting:
        value = [slot]
    elif model is RunStarted:
        value = [dict(slot=slot, default_model="fake")]
    return {**base, field: value}


@pytest.mark.parametrize("model,base,field", SLOT_MODELS, ids=lambda v: getattr(v, "__name__", ""))
@pytest.mark.parametrize("slot,valid", SLOT_CASES)
def test_each_slot_bearing_model_accepts_only_uppercase_ascii_strings(
    model: Any, base: Any, field: str, slot: Any, valid: bool
) -> None:
    payload = slot_payload(model, base, field, slot)
    if valid:
        parsed = model.model_validate(payload)
        assert model.model_validate_json(parsed.model_dump_json()) == parsed
        assert parsed.model_dump(mode="json")[field] == payload[field]
    else:
        with pytest.raises(ValidationError):
            model.model_validate(payload)
        with pytest.raises(ValidationError):
            model.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("slot", [None, b"A", bytearray(b"A")])
def test_slot_itself_rejects_null_and_bytes(slot: Any) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(Slot).validate_python(slot)


@pytest.mark.anyio
async def test_slot_cases_agree_with_generated_schema(tmp_path: Path) -> None:
    records = []
    for model, base, field in SLOT_MODELS:
        normalized = model.model_validate(slot_payload(model, base, field, "A")).model_dump(
            mode="json"
        )
        for slot, valid in SLOT_CASES:
            records.append(
                dict(
                    source=f"{model.__name__}: {slot!r}",
                    model=model.__name__,
                    value=slot_payload(model, normalized, field, slot),
                    valid=valid,
                )
            )
    for slot, valid in [*SLOT_CASES, (None, False)]:
        records.append(dict(source=f"Slot: {slot!r}", model="Slot", value=slot, valid=valid))
    serialized = tmp_path / "slot-cases.json"
    serialized.write_text(json.dumps(records))
    result = await run_process(
        ["node", "contracts/tests/validate_serialized.cjs", str(serialized), "--cases"],
        cwd=ROOT,
        timeout_s=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.anyio
async def test_extended_run_fixture_round_trips_and_matches_generated_schema(
    tmp_path: Path,
) -> None:
    adapter = TypeAdapter(ServerEvent)
    event = adapter.validate_python(RUN_EVENT)
    assert event.type == "run.started"
    assert event.payload.contract_version == CONTRACT_VERSION == "1.21.0"
    assert [seat.slot for seat in event.payload.slots] == [*"ABCDEFGH", "AA"]
    assert adapter.validate_json(adapter.dump_json(event)) == event
    serialized = tmp_path / "extended-run.json"
    serialized.write_text(
        json.dumps([dict(source=FIXTURE.name, event=event.model_dump(mode="json"))])
    )
    result = await run_process(
        ["node", "contracts/tests/validate_serialized.cjs", str(serialized)], cwd=ROOT, timeout_s=60
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_generated_typescript_uses_an_open_string_slot() -> None:
    typescript = (ROOT / "contracts/generated/contracts.ts").read_text()
    assert "export type Slot = string;" in typescript
