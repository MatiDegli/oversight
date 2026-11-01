"""The sole typed confirmation command is bounded and rejects unknown input."""

import pytest
from pydantic import ValidationError

from oversight_contracts.commands import ConfirmExamples

BODY = {
    "run_id": "run-20261031-120000",
    "evidence_seq": 7,
    "base_commit": "abc",
    "action": "confirm",
    "examples": [{"example_id": "one", "inputs": "{}", "expected_output": "1"}],
}


def test_confirm_examples_schema_and_round_trip() -> None:
    command = ConfirmExamples.model_validate(BODY)
    assert ConfirmExamples.model_validate_json(command.model_dump_json()) == command
    schema = ConfirmExamples.model_json_schema()
    assert set(schema["properties"]) == set(schema["required"]) == set(BODY)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["examples"]["minItems"] == 1
    assert schema["properties"]["examples"]["maxItems"] == 5
    assert ConfirmExamples.model_validate({**BODY, "action": "edit"}).action == "edit"
    assert (
        len(ConfirmExamples.model_validate({**BODY, "examples": BODY["examples"] * 5}).examples)
        == 5
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"run_id": "other"},
        {"evidence_seq": 0},
        {"evidence_seq": -1},
        {"base_commit": ""},
        {"action": "reject"},
        {"examples": []},
        {"examples": BODY["examples"] * 6},
        {"text": "yes"},
    ],
)
def test_confirm_examples_invalid_fields(patch: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ConfirmExamples.model_validate({**BODY, **patch})
