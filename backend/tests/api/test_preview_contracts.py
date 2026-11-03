"""Absent preview profile uses the existing setup error, with no new setting or handler."""

import json

import pytest
from pydantic import ValidationError

from oversight.api.errors import error_response
from oversight_contracts.commands import ERROR_STATUS, ErrorDetail, ErrorResponse

MESSAGE = "Preview profile is not configured."


def test_absent_profile_is_valid_not_configured_with_empty_missing() -> None:
    detail = ErrorDetail(code="not_configured", message=MESSAGE, missing=[])
    expected = {"error": {"code": "not_configured", "message": MESSAGE, "missing": []}}
    assert ErrorResponse(error=detail).model_dump() == expected
    assert ErrorResponse.model_validate(expected).error == detail
    response = error_response("not_configured", MESSAGE, missing=[])
    assert response.status_code == ERROR_STATUS["not_configured"] == 503
    assert json.loads(bytes(response.body)) == expected


def test_bare_not_configured_is_invalid_in_models_and_response_builder() -> None:
    with pytest.raises(ValidationError, match="missing is set exactly"):
        ErrorDetail(code="not_configured", message=MESSAGE)
    with pytest.raises(ValidationError, match="missing is set exactly"):
        ErrorResponse.model_validate({"error": {"code": "not_configured", "message": MESSAGE}})
    with pytest.raises(ValidationError, match="missing is set exactly"):
        error_response("not_configured", MESSAGE)
