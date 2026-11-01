"""Pure, deterministic pytest source for confirmed synchronous JSON examples."""

import hashlib
import json
import keyword
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from oversight_contracts.examples import TaskExample

MAX_CRITERIA_SOURCE_BYTES = 131_072
MAX_INPUT_CHARS = 4000
MAX_OUTPUT_CHARS = 8000
CANONICAL_ARGUMENTS = 'sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False'


@dataclass(frozen=True)
class CriteriaCase:
    example_id: str
    node_id: str
    args: str
    kwargs: str
    expected: str


@dataclass(frozen=True)
class RenderedCriteria:
    path: str
    source: str
    sha256: str
    cases: tuple[tuple[str, str], ...]


def _canonical(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _constant(value: str) -> object:
    raise ValueError("non-finite JSON number")


def _load(text: str) -> object:
    try:
        value: object = json.loads(text, object_pairs_hook=_object, parse_constant=_constant)
        _canonical(value)  # Reject overflowing exponents at every depth too.
        return value
    except (RecursionError, OverflowError) as error:
        raise ValueError("JSON exceeds parser limits") from error


def _target(node_id: object) -> str:
    if not isinstance(node_id, str) or node_id.count(":") != 1:
        raise ValueError("invalid target")
    module, name = node_id.split(":")
    if not all(part.isidentifier() and not keyword.iskeyword(part) for part in module.split(".")):
        raise ValueError("invalid module")
    if not name.isidentifier() or keyword.iskeyword(name):
        raise ValueError("target must be a top-level function")
    return node_id


def parse_cases(examples: Sequence[TaskExample]) -> tuple[CriteriaCase, ...]:
    """Validate without losing duplicate keys or accepting behavior-only examples."""
    if not 1 <= len(examples) <= 5:
        raise ValueError("criteria require one to five cases")
    seen: set[str] = set()
    cases: list[CriteriaCase] = []
    for example in examples:
        if example.example_id in seen:
            raise ValueError("duplicate example id")
        seen.add(example.example_id)
        if len(example.inputs) > MAX_INPUT_CHARS:
            raise ValueError("inputs exceed character limit")
        if example.expected_output is None:
            raise ValueError("exact JSON output required")
        if len(example.expected_output) > MAX_OUTPUT_CHARS:
            raise ValueError("output exceeds character limit")
        inputs = _load(example.inputs)
        if not isinstance(inputs, dict):
            raise ValueError("inputs must be an object")
        fields = cast(dict[str, object], inputs)
        if set(fields) != {"node_id", "args", "kwargs"}:
            raise ValueError("inputs require exactly node_id, args and kwargs")
        if not isinstance(fields["args"], list) or not isinstance(fields["kwargs"], dict):
            raise ValueError("args must be a list and kwargs an object")
        cases.append(
            CriteriaCase(
                example.example_id,
                _target(fields["node_id"]),
                _canonical(cast(list[object], fields["args"])),
                _canonical(cast(dict[str, object], fields["kwargs"])),
                _canonical(_load(example.expected_output)),
            )
        )
    return tuple(cases)


def render_examples(examples: Sequence[TaskExample], change_id: str) -> RenderedCriteria:
    """Return ASCII source and the complete ordered ids pytest will report."""
    match = re.fullmatch(r"change-([1-9][0-9]*)", change_id)
    if match is None:
        raise ValueError("invalid change id")
    path = f"tests/test_oversight_examples_change_{match[1]}.py"
    source = "# Oversight confirmed JSON examples.\nimport importlib\nimport json\n"
    ids: list[tuple[str, str]] = []
    for index, case in enumerate(parse_cases(examples), 1):
        slug = re.sub(r"[^a-z0-9_]", "_", case.example_id.lower())[:40]
        slug = slug if slug.strip("_") else "case"
        function = f"test_example_{index:02d}_{slug}"
        module, name = case.node_id.split(":")
        # Canonical JSON uses repr of ASCII text; identifiers use escaped repr literals.
        literal_module = ascii(module)
        literal_name = ascii(name)
        source += (
            f"\n\ndef {function}():\n"
            f"    module = importlib.import_module({literal_module})\n"
            f"    function = getattr(module, {literal_name})\n"
            f"    result = function(*json.loads({case.args!r}), **json.loads({case.kwargs!r}))\n"
            f"    assert json.dumps(result, {CANONICAL_ARGUMENTS}) == {case.expected!r}\n"
        )
        ids.append((case.example_id, f"{path}::{function}"))
    data = source.encode("ascii")
    if len(data) > MAX_CRITERIA_SOURCE_BYTES:
        raise ValueError("criteria source exceeds byte limit")
    return RenderedCriteria(path, source, hashlib.sha256(data).hexdigest(), tuple(ids))
