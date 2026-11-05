"""Approved editable configuration, separate from startup readiness names."""

import json
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from oversight_contracts.commands import (
    EditableSetting,
    EditableSettingName,
    SettingsView,
    UpdateSettings,
)

NAMES = {
    "META_API_KEY",
    "BASETEN_API_KEY",
    "OVERSIGHT_BUDGET_USD",
    "MUSE_SPARK_INPUT_USD_PER_M",
    "MUSE_SPARK_OUTPUT_USD_PER_M",
    "OVERSIGHT_BASETEN_BUDGET_USD",
    "BASETEN_INPUT_USD_PER_M",
    "BASETEN_OUTPUT_USD_PER_M",
    "OVERSIGHT_CROSS_REVIEW",
    "OVERSIGHT_MAX_AGENTS",
    "OVERSIGHT_MAX_STEPS",
    "OVERSIGHT_SPEND_BRAKE_PERCENT",
}


def test_editable_allowlist_and_string_limit() -> None:
    assert set(get_args(EditableSettingName.__value__)) == NAMES
    assert (
        UpdateSettings.model_validate({"values": {name: "" for name in NAMES}}).values.keys()
        == NAMES
    )
    UpdateSettings(values={"META_API_KEY": "x" * 512})
    for values in ({"OTHER": "value"}, {"META_API_KEY": "x" * 513}, {"META_API_KEY": 42}):
        with pytest.raises(ValidationError):
            UpdateSettings.model_validate({"values": values})


def test_secret_shape_and_generated_settings_contract() -> None:
    entry = EditableSetting(
        name="META_API_KEY", kind="secret", state="set", value=None, overridden=False
    )
    view = SettingsView(settings=[entry], restart_required=False, apply="server_restart")
    assert view.model_dump()["settings"][0]["value"] is None
    schema = json.loads(
        (Path(__file__).parents[1] / "generated/oversight.schema.json").read_text()
    )["$defs"]
    assert {"EditableSetting", "SettingsView", "UpdateSettings"} <= schema.keys()
    assert (
        schema["UpdateSettings"]["properties"]["values"]["additionalProperties"]["maxLength"] == 512
    )


@pytest.mark.parametrize("name", ["META_API_KEY", "BASETEN_API_KEY"])
@pytest.mark.parametrize("kind", ["secret", "number", "boolean"])
def test_key_names_cannot_return_a_value_regardless_of_kind(name: str, kind: str) -> None:
    with pytest.raises(ValidationError, match="secret values must be withheld"):
        EditableSetting.model_validate(
            {
                "name": name,
                "kind": kind,
                "state": "set",
                "value": "synthetic-key",
                "overridden": False,
            }
        )
