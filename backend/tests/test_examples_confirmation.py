"""Pure confirmation guards: no model, repository, source or graph access."""

from dataclasses import replace

import pytest

from oversight.agents import (
    ExamplesInvalid,
    ExampleTarget,
    canonical_json,
    validate_person_examples,
)
from oversight.changes import WrongState
from oversight.examples_confirmation import InvalidExamples, PendingExamples, check_confirmation
from oversight_contracts.commands import ConfirmExamples
from oversight_contracts.examples import ExamplePin, TaskExample

TARGET = ExampleTarget("shop.mod.value", "function", False, True)
EXAMPLE = TaskExample(
    example_id="one",
    inputs='{"node_id":"shop.mod.value","args":[],"kwargs":{}}',
    expected_output='{"b":2,"a":1}',
    note="one meaning",
)


def pending() -> PendingExamples:
    return PendingExamples(
        "run-20261031-120000",
        "agent-1",
        "change-1",
        "abc",
        7,
        "Do it.",
        (EXAMPLE,),
        {"one": "meaning"},
        (TARGET,),
        3,
    )


def command(**updates: object) -> ConfirmExamples:
    return ConfirmExamples.model_validate(
        {
            "run_id": "run-20261031-120000",
            "evidence_seq": 7,
            "base_commit": "abc",
            "action": "confirm",
            "examples": [EXAMPLE.model_dump()],
            **updates,
        }
    )


def test_examples_confirm_builds_server_pins() -> None:
    p, c = pending(), command()
    result = check_confirmation(p, c, "agent-1")
    [pinned] = result.examples
    assert pinned.pin == ExamplePin(source_task="Do it.")
    assert pinned.model_copy(update={"pin": None}) == EXAMPLE
    assert c.examples[0].pin is p.offered[0].pin is None
    c.examples[0] = c.examples[0].model_copy(update={"note": "mutated"})
    assert pinned.note == "one meaning"
    other = EXAMPLE.model_copy(update={"example_id": "two"})
    subset = replace(
        p, offered=(EXAMPLE, other), meaning_by_example={"one": "meaning", "two": "meaning"}
    )
    assert len(check_confirmation(subset, command(), "agent-1").examples) == 1
    with pytest.raises(ValueError, match="unpinned"):
        replace(p, offered=result.examples)


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "run-20261030-120000"),
        ("evidence_seq", 8),
        ("base_commit", "other"),
        ("agent_id", "agent-2"),
        ("unknown", None),
        ("altered", None),
        ("meanings", None),
        ("duplicate", None),
        ("pin", None),
        ("empty", None),
        ("missing_meaning", None),
    ],
)
def test_examples_confirm_refusals(field: str, value: object) -> None:
    p, c, agent = pending(), command(), "agent-1"
    if field == "agent_id":
        agent = str(value)
    elif field in {"run_id", "evidence_seq", "base_commit"}:
        c = c.model_copy(update={field: value})
    elif field == "unknown":
        c.examples[0] = c.examples[0].model_copy(update={"example_id": "unknown"})
    elif field == "altered":
        c.examples[0] = c.examples[0].model_copy(update={"note": "changed"})
    elif field == "meanings":
        other = EXAMPLE.model_copy(update={"example_id": "two"})
        p = replace(p, offered=(*p.offered, other), meaning_by_example={"one": "a", "two": "b"})
        c.examples.append(other)
    elif field == "duplicate":
        c.examples.extend(c.examples)
    elif field == "pin":
        c.examples[0] = c.examples[0].model_copy(update={"pin": ExamplePin(source_task="Do it.")})
    elif field == "missing_meaning":
        p = replace(p, meaning_by_example={})
    else:
        c.examples.clear()  # Defend direct callers as well as wire validation.
    expected = (
        WrongState
        if field in {"run_id", "evidence_seq", "base_commit", "agent_id"}
        else InvalidExamples
    )
    with pytest.raises(expected) as error:
        check_confirmation(p, c, agent)
    if isinstance(error.value, InvalidExamples):
        assert (
            error.value.code
            == {
                "unknown": "not_offered",
                "altered": "not_offered",
                "meanings": "mixed_meanings",
                "duplicate": "duplicate_example_id",
                "pin": "client_pin",
                "empty": "case_count",
                "missing_meaning": "unknown_meaning",
            }[field]
        )


@pytest.mark.parametrize(
    "patch,target,code",
    [
        ({"inputs": "{"}, TARGET, "invalid_inputs"),
        (
            {"inputs": '{"node_id":"shop.mod.value","args":[NaN],"kwargs":{}}'},
            TARGET,
            "non_finite_numbers",
        ),
        ({"expected_output": "1e999"}, TARGET, "non_finite_numbers"),
        ({"expected_output": '{"x":1,"x":2}'}, TARGET, "non_strict_json"),
        ({"expected_output": "no JSON"}, TARGET, "malformed_json"),
        ({"expected_output": None, "expected_behavior": "works"}, TARGET, "behavior_only"),
        ({"expected_behavior": "also works"}, TARGET, "behavior_only"),
        ({}, replace(TARGET, node_id="other"), "target_not_allowed"),
        ({}, replace(TARGET, is_async=True), "target_not_sync_top_level"),
        ({}, replace(TARGET, is_top_level=False), "target_not_sync_top_level"),
        ({}, replace(TARGET, kind="method"), "target_not_sync_top_level"),
        ({"inputs": " " * 4001}, TARGET, "strings_too_long"),
        ({"expected_output": '"' + "x" * 8000 + '"'}, TARGET, "strings_too_long"),
        ({"example_id": "x" * 129}, TARGET, "strings_too_long"),
        ({"note": "x" * 2001}, TARGET, "strings_too_long"),
        (
            {"inputs": '{"node_id":"shop.mod.value","args":[],"kwargs":{},"extra":0}'},
            TARGET,
            "invalid_inputs",
        ),
    ],
)
def test_examples_edit_uses_the_oracle_rules(
    patch: dict[str, object], target: ExampleTarget, code: str
) -> None:
    c = command(action="edit")
    c.examples[0] = c.examples[0].model_copy(update=patch)
    with pytest.raises(InvalidExamples) as error:
        check_confirmation(replace(pending(), targets=(target,)), c, "agent-1")
    assert error.value.code == code


def test_examples_digest_is_stable() -> None:
    p, c = pending(), command(action="edit")
    first = check_confirmation(p, c, "agent-1")
    c.examples[0] = c.examples[0].model_copy(
        update={
            "expected_output": '{ "a": 1, "b": 2 }',
            "inputs": canonical_json({"kwargs": {}, "args": [], "node_id": TARGET.node_id}),
        }
    )
    assert check_confirmation(p, c, "agent-1").digest == first.digest
    assert first.examples[0].expected_output == '{"a":1,"b":2}'
    c.examples[0] = c.examples[0].model_copy(update={"note": "different"})
    assert check_confirmation(p, c, "agent-1").digest != first.digest
    c = command()
    assert check_confirmation(p, c, "agent-1").digest != first.digest
    other = c.examples[0].model_copy(update={"example_id": "two"})
    p = replace(p, offered=(*p.offered, other), meaning_by_example={"one": "a", "two": "a"})
    c.examples.append(other)
    forward = check_confirmation(p, c, "agent-1").digest
    c.examples.reverse()
    assert check_confirmation(p, c, "agent-1").digest != forward


@pytest.mark.parametrize(
    "kind,code",
    [
        ("empty", "case_count"),
        ("six", "case_count"),
        ("duplicate", "duplicate_example_id"),
        ("pin", "person_supplied_pin"),
    ],
)
def test_person_examples_bounds_and_ids(kind: str, code: str) -> None:
    examples = (
        []
        if kind == "empty"
        else [EXAMPLE] * (6 if kind == "six" else 2 if kind == "duplicate" else 1)
    )
    if kind == "pin":
        examples = [EXAMPLE.model_copy(update={"pin": ExamplePin(source_task="Do it.")})]
    with pytest.raises(ExamplesInvalid) as error:
        validate_person_examples(examples, (TARGET,))
    assert error.value.code == code
