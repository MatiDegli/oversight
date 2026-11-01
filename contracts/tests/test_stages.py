"""The approved optional stage view stays strict, additive and expressed in raw milliseconds."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from oversight_contracts import CONTRACT_VERSION
from oversight_contracts.commands import ChangeView
from oversight_contracts.stages import ChangeStages, StageTime, SuitePhaseTime

ROOT = Path(__file__).resolve().parents[1]
STAGE = dict(ms=0, known_ms=0, intervals=0, open=False)
PHASE = dict(phase="agent", ms=0, runs=1)
STAGES = dict(agent_work=STAGE, checks=STAGE, waiting_for_person=STAGE, test_runs=STAGE)
FIELDS = [
    (StageTime, STAGE, ["ms", "known_ms", "intervals", "open"]),
    (SuitePhaseTime, PHASE, ["phase", "ms", "runs"]),
    (
        ChangeStages,
        STAGES,
        ["agent_work", "checks", "waiting_for_person", "test_runs", "test_phases", "problems"],
    ),
]


@pytest.mark.parametrize("model,data,fields", FIELDS)
def test_exact_fields_order_and_extra_keys_rejected(model, data, fields):
    assert list(model.model_fields) == fields
    assert set(fields).isdisjoint({"total", "score", "rank", "ratio", "percent", "review_time"})
    assert list(model.model_validate(data).model_dump()) == fields
    with pytest.raises(ValidationError):
        model.model_validate(dict(data, extra=True))


@pytest.mark.parametrize(
    "model,data",
    [
        (StageTime, dict(STAGE, ms=-1)),
        (StageTime, dict(STAGE, known_ms=-1)),
        (StageTime, dict(STAGE, intervals=-1)),
        (SuitePhaseTime, dict(PHASE, ms=-1)),
        (SuitePhaseTime, dict(PHASE, runs=0)),
        (ChangeStages, dict(STAGES, test_phases=[PHASE] * 6)),
        (ChangeStages, dict(STAGES, problems=["Unknown at seq 1."] * 21)),
        (ChangeStages, dict(STAGES, problems=[""])),
    ],
)
def test_invalid_stage_values_rejected(model, data):
    with pytest.raises(ValidationError):
        model.model_validate(data)


def test_known_lower_bound_and_older_change_view_are_valid():
    stage = StageTime(ms=None, known_ms=5, intervals=1, open=False)
    assert stage.ms is None and stage.known_ms == 5
    old = dict(
        change_id="change-1",
        seq=0,
        proposal=None,
        files=[],
        facts=None,
        review=None,
        assessment=None,
        resolution=None,
    )
    assert ChangeView.model_validate(old).stages is None
    assert ChangeStages.model_validate(STAGES).test_phases == []
    assert ChangeStages.model_validate(STAGES).problems == []


def test_generated_schema_and_types_include_strict_stage_models():
    schema = json.loads((ROOT / "generated/oversight.schema.json").read_text())
    assert CONTRACT_VERSION in schema["$comment"]
    definitions = schema["$defs"]
    assert "stages" in definitions["ChangeView"]["required"]
    for model, _, fields in FIELDS:
        definition = definitions[model.__name__]
        assert definition["additionalProperties"] is False
        assert list(definition["properties"]) == fields
        assert definition["required"] == fields
    assert "stages: ChangeStages | null;" in (ROOT / "generated/contracts.ts").read_text()
