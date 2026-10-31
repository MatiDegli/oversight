"""Approved supplied-call evidence remains separate from behavior comparison counts."""

import pytest
from pydantic import ValidationError

from oversight_contracts.review import BehaviorDiffRow, ParameterExtensionDiff


def test_parameter_extension_defaults():
    result = ParameterExtensionDiff(node_id="sample:f", parameters=["extra"])
    assert result.model_dump() == {
        "node_id": "sample:f",
        "parameters": ["extra"],
        "run_count": 0,
        "rejected_before": 0,
        "rows": [],
        "not_probed": None,
    }


def test_binding_rejections_cannot_exceed_run_calls():
    with pytest.raises(ValidationError, match="rejected calls cannot exceed run calls"):
        ParameterExtensionDiff(
            node_id="sample:f", parameters=["extra"], run_count=1, rejected_before=2
        )
    assert (
        ParameterExtensionDiff(
            node_id="sample:f", parameters=["extra"], run_count=2, rejected_before=2
        ).rejected_before
        == 2
    )


@pytest.mark.parametrize(
    "updates",
    [{"parameters": []}, {"parameters": [""]}, {"run_count": -1}, {"rejected_before": -1}],
)
def test_extension_rejects_invalid_parameters_and_counts(updates):
    with pytest.raises(ValidationError):
        ParameterExtensionDiff.model_validate(
            {"node_id": "sample:f", "parameters": ["extra"], **updates}
        )


def test_extension_rows_are_limited_to_eight():
    row = BehaviorDiffRow(inputs="f(extra=1)", before="rejected", after="value")
    assert (
        len(ParameterExtensionDiff(node_id="sample:f", parameters=["extra"], rows=[row] * 8).rows)
        == 8
    )
    with pytest.raises(ValidationError, match="at most 8"):
        ParameterExtensionDiff(node_id="sample:f", parameters=["extra"], rows=[row] * 9)
