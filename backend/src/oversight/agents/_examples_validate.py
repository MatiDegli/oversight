"""Strict wire validation for the inert examples phase."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

# E1 deliberately reuses the package's private strict-JSON helpers.
from oversight.agents._plan_call import (
    _invalid_constant,  # pyright: ignore[reportPrivateUsage]
    _unique_object,  # pyright: ignore[reportPrivateUsage]
)
from oversight_contracts.examples import TaskExample

EXAMPLE_ID_CHARS = 128
EXAMPLE_DESCRIPTION_CHARS = 1000
EXAMPLE_INPUT_CHARS = 4000
EXAMPLE_OUTPUT_CHARS = 8000
EXAMPLE_NOTE_CHARS = 2000
EXAMPLE_CLARIFICATION_CHARS = 2000
EXAMPLE_SUMMARY_CHARS = 65536


class ExamplesInvalid(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(detail)


def canonical_json(value: object) -> str:
    """Shared exact JSON rendering for E1, the E3 preview and the E4 check."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def _constant(value: str) -> object:
    try:
        return _invalid_constant(value)
    except ValueError as error:
        raise ExamplesInvalid("non_finite_numbers", str(error)) from error


def _load(text: str) -> object:
    try:
        value: object = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_constant)
        canonical_json(value)  # Includes overflowed exponents, at every nesting level.
        return value
    except ExamplesInvalid:
        raise
    except json.JSONDecodeError as error:
        raise ExamplesInvalid("malformed_json", str(error)) from error
    except ValueError as error:
        code = "non_strict_json" if "duplicate JSON key" in str(error) else "non_finite_numbers"
        raise ExamplesInvalid(code, str(error)) from error
    except RecursionError as error:
        raise ExamplesInvalid("invalid_schema", "JSON nesting exceeds parser limits") from error


def _bounded(value: str, limit: int, label: str) -> None:
    if len(value) > limit:
        raise ExamplesInvalid("strings_too_long", f"{label} exceeds {limit} characters")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Finish(_Strict):
    summary: str


class _Meaning(_Strict):
    id: str
    description: str


class _Case(_Strict):
    example_id: str
    meaning: str
    inputs: str
    expected_output: JsonValue
    note: str


class _Reply(_Strict):
    meanings: list[_Meaning]
    cases: list[_Case]


class _Clarification(_Strict):
    clarification_needed: str


class _Inputs(_Strict):
    node_id: str
    args: list[JsonValue]
    kwargs: dict[str, JsonValue]


@dataclass(frozen=True)
class ExampleTarget:
    node_id: str
    kind: Literal["module", "class", "function", "method"]
    is_async: bool
    is_top_level: bool


def _case(raw: object) -> _Case:
    if isinstance(raw, dict):
        if "pin" in raw:
            raise ExamplesInvalid("model_supplied_pin", "The model cannot confirm examples")
        if "expected_output" not in raw:
            code = "behavior_only" if "expected_behavior" in raw else "missing_expected_output"
            raise ExamplesInvalid(code, "An exact JSON expected_output is required")
    return _Case.model_validate(raw)


def _flatten(
    case: _Case, meanings: dict[str, str], targets: dict[str, ExampleTarget]
) -> TaskExample:
    for label, value, limit in (
        ("example_id", case.example_id, EXAMPLE_ID_CHARS),
        ("meaning", case.meaning, EXAMPLE_ID_CHARS),
        ("inputs", case.inputs, EXAMPLE_INPUT_CHARS),
        ("note", case.note, EXAMPLE_NOTE_CHARS),
    ):
        _bounded(value, limit, label)
    if case.meaning not in meanings:
        raise ExamplesInvalid("unknown_meaning", f"Unknown meaning: {case.meaning}")
    rendered = canonical_json(case.expected_output)
    _bounded(rendered, EXAMPLE_OUTPUT_CHARS, "expected_output")
    try:
        inputs = _load(case.inputs)
        parsed = _Inputs.model_validate(inputs)
    except (ValidationError, ExamplesInvalid) as error:
        if isinstance(error, ExamplesInvalid) and error.code == "non_finite_numbers":
            raise
        raise ExamplesInvalid("invalid_inputs", str(error)) from error
    target = targets.get(parsed.node_id)
    if target is None:
        raise ExamplesInvalid("target_not_allowed", f"Target not allowed: {parsed.node_id}")
    if target.kind != "function" or target.is_async or not target.is_top_level:
        raise ExamplesInvalid("target_not_sync_top_level", f"Ineligible target: {parsed.node_id}")
    return TaskExample(
        example_id=case.example_id,
        inputs=canonical_json(inputs),
        expected_output=rendered,
        note=f"{case.meaning}: {meanings[case.meaning]} — {case.note}",
        pin=None,
    )


def validate_examples(arguments: str, allowed: Sequence[ExampleTarget]) -> list[TaskExample] | str:
    """Raises a precise failure; successful examples remain unconfirmed."""
    if not arguments.strip():
        raise ExamplesInvalid("empty_output", "Empty finish arguments")
    _bounded(arguments, EXAMPLE_SUMMARY_CHARS, "finish arguments")
    try:
        summary = _Finish.model_validate(_load(arguments)).summary
        if not summary.strip():
            raise ExamplesInvalid("empty_output", "Empty finish summary")
        _bounded(summary, EXAMPLE_SUMMARY_CHARS, "summary")
        raw = _load(summary)
        if isinstance(raw, dict) and "clarification_needed" in raw:
            clarification = _Clarification.model_validate(raw).clarification_needed
            _bounded(clarification, EXAMPLE_CLARIFICATION_CHARS, "clarification_needed")
            if not clarification.strip():
                raise ExamplesInvalid("empty_output", "Empty clarification")
            return clarification
        # Check dedicated error categories before pydantic's structural validation.
        if isinstance(raw, dict):
            raw_cases = cast(dict[str, object], raw).get("cases")
            if isinstance(raw_cases, list):
                for case in cast(list[object], raw_cases):
                    _case(case)
        reply = _Reply.model_validate(raw)
        if not 1 <= len(reply.meanings) <= 3:
            raise ExamplesInvalid("meaning_count", "Expected one to three meanings")
        if not 3 <= len(reply.cases) <= 5:
            raise ExamplesInvalid("case_count", "Expected three to five cases")
        meanings: dict[str, str] = {}
        for meaning in reply.meanings:
            _bounded(meaning.id, EXAMPLE_ID_CHARS, "meaning id")
            _bounded(meaning.description, EXAMPLE_DESCRIPTION_CHARS, "meaning description")
            if not meaning.id.strip() or not meaning.description.strip() or meaning.id in meanings:
                raise ExamplesInvalid("invalid_meaning", "Meaning ids must be nonempty and unique")
            meanings[meaning.id] = meaning.description
        if len({case.example_id for case in reply.cases}) != len(reply.cases):
            raise ExamplesInvalid("duplicate_example_ids", "Example ids must be unique")
        return [_flatten(case, meanings, {t.node_id: t for t in allowed}) for case in reply.cases]
    except ValidationError as error:
        raise ExamplesInvalid("invalid_schema", str(error)) from error
