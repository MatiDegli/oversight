"""Examples E1 is inert: scripted replies only, no network or runtime wiring."""

import asyncio
import json
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from oversight.agents import (
    EXAMPLE_CALL_TIMEOUT_S,
    EXAMPLE_CONTEXT_CHARS,
    EXAMPLE_MAX_OUTPUT_TOKENS,
    MUSE_SPARK,
    ExamplesNotConfigured,
    ExampleTarget,
    Script,
    ScriptedModel,
    Usage,
    canonical_json,
    example_context,
    examples_client,
    parse_script,
    prompt_version,
    run_examples_call,
)
from oversight.agents import examples as phase
from oversight.agents.client import CALL_SCOPE, ModelAPIError
from oversight.settings import Settings
from oversight_contracts.examples import TaskExample

ALLOWED = [ExampleTarget("pkg::f", "function", False, True)]


def payload() -> dict[str, Any]:
    return {
        "meanings": [{"id": "exact", "description": "Preserve numeric types"}],
        "cases": [
            {
                "example_id": f"e{i}",
                "meaning": "exact",
                "inputs": '{"kwargs":{},"node_id":"pkg::f","args":[' + str(i) + "]}",
                "expected_output": {"z": 1.0, "a": i},
                "note": "Separate integer/float",
            }
            for i in range(3)
        ],
    }


def model(raw: object = None, *, arguments: str | None = None) -> ScriptedModel:
    if raw is None:
        raw = payload()
    return ScriptedModel(
        parse_script(
            json.dumps(
                {
                    "task": "examples",
                    "turns": [
                        {
                            "tool_calls": [
                                {
                                    "name": "finish",
                                    "arguments": arguments
                                    if arguments is not None
                                    else {"summary": json.dumps(raw)},
                                }
                            ],
                            "usage": {"input_tokens": 11, "output_tokens": 7},
                        }
                    ],
                }
            ),
            MUSE_SPARK,
        ),
        MUSE_SPARK,
    )


@pytest.mark.anyio
async def test_examples_single_call(monkeypatch: pytest.MonkeyPatch) -> None:
    client = model()
    captured: dict[str, Any] = {}

    def constructor(profile: object, **kwargs: Any) -> ScriptedModel:
        captured.update(kwargs)
        assert profile == MUSE_SPARK and MUSE_SPARK.reasoning == "low"
        return client

    monkeypatch.setattr(phase, "OpenAIClient", constructor)
    result = await run_examples_call(
        lambda: examples_client(Settings(meta_api_key="test")), "captured source", ALLOWED
    )
    assert result.kind == "examples"
    assert len(client.requests) == 1 and len(client.tool_requests) == 1
    assert client.tool_requests[0] == [phase.EXAMPLE_FINISH_SCHEMA]
    assert captured["timeout_s"] == EXAMPLE_CALL_TIMEOUT_S == 90
    assert captured["retry"].max_attempts == 1
    assert captured["max_output_tokens"] == EXAMPLE_MAX_OUTPUT_TOKENS == 4096
    assert result.attempt_usage == (Usage(11, 7),)
    assert result.call_names == ("finish",)
    for i, example in enumerate(result.examples):
        assert isinstance(example, TaskExample) and example.pin is None
        assert example.note == "exact: Preserve numeric types — Separate integer/float"
        assert example.expected_output == f'{{"a":{i},"z":1.0}}'
        assert json.loads(example.inputs) == {"node_id": "pkg::f", "args": [i], "kwargs": {}}


@pytest.mark.anyio
async def test_clarification_needed() -> None:
    client = model({"clarification_needed": "Which rounding rule?"})
    result = await run_examples_call(lambda: client, "context", ALLOWED)
    assert result.kind == "clarification" and result.clarification == "Which rounding rule?"
    assert not result.examples and len(client.requests) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("ending", ["examples", "invalid", "call_failed", "timeout", "cancelled"])
async def test_factory_client_is_closed(ending: str, monkeypatch: pytest.MonkeyPatch) -> None:
    client = model(arguments="invalid" if ending == "invalid" else None)
    close = AsyncMock()
    monkeypatch.setattr(client, "aclose", close, raising=False)
    failures = {
        "call_failed": ModelAPIError("transport failed"),
        "timeout": TimeoutError(),
        "cancelled": asyncio.CancelledError(),
    }
    if ending in failures:
        monkeypatch.setattr(client, "complete", AsyncMock(side_effect=failures[ending]))
    if ending == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            await run_examples_call(lambda: client, "context", ALLOWED)
    else:
        result = await run_examples_call(lambda: client, "context", ALLOWED)
        assert (result.kind if ending == "examples" else result.reason) == {
            "examples": "examples",
            "invalid": "malformed_json",
            "call_failed": "call_failed",
            "timeout": "timeout",
        }[ending]
    close.assert_awaited_once_with()
    assert CALL_SCOPE.get() is None


def invalid_payload(issue: str) -> dict[str, Any]:
    raw = payload()
    case = raw["cases"][0]
    if issue in {"no_meanings", "four_meanings"}:
        raw["meanings"] *= 0 if issue == "no_meanings" else 4
    elif issue in {"two_cases", "six_cases"}:
        raw["cases"] = raw["cases"][:2] if issue == "two_cases" else raw["cases"] * 2
    elif issue == "unknown_key":
        raw["extra"] = True
    elif issue == "wrong_type":
        case["note"] = 7
    elif issue == "duplicate_ids":
        raw["cases"][1]["example_id"] = "e0"
    elif issue == "unknown_meaning":
        case["meaning"] = "unknown"
    elif issue == "pin":
        case["pin"] = None
    elif issue == "bad_input_json":
        case["inputs"] = "not JSON"
    elif issue in {"args_object", "kwargs_list", "input_extra", "input_missing", "outside_target"}:
        inputs = json.loads(case["inputs"])
        if issue == "args_object":
            inputs["args"] = {}
        if issue == "kwargs_list":
            inputs["kwargs"] = []
        if issue == "input_extra":
            inputs["code"] = "pass"
        if issue == "input_missing":
            inputs.pop("kwargs")
        if issue == "outside_target":
            inputs["node_id"] = "other::f"
        case["inputs"] = json.dumps(inputs)
    elif issue == "input_not_string":
        case["inputs"] = {}
    elif issue in {"missing_output", "behavior"}:
        case.pop("expected_output")
        if issue == "behavior":
            case["expected_behavior"] = "does something"
    elif issue == "nan":
        case["expected_output"] = [float("nan")]
    elif issue == "infinity":
        case["expected_output"] = {"nested": float("inf")}
    elif issue == "input_nan":
        case["inputs"] = case["inputs"].replace("[0]", "[NaN]")
    elif issue == "empty_meaning":
        raw["meanings"][0]["id"] = " "
    elif issue == "duplicate_meaning":
        raw["meanings"] *= 2
    elif issue == "empty_example_id":
        case["example_id"] = ""
    return raw


@pytest.mark.anyio
@pytest.mark.parametrize(
    "issue,code",
    [
        ("no_call", "no_tool_call"),
        ("many_calls", "multiple_tool_calls"),
        ("other_tool", "unexpected_tool"),
        ("length", "truncated"),
        ("model", "model_mismatch"),
        ("empty_arguments", "empty_output"),
        ("empty_summary", "empty_output"),
        ("malformed", "malformed_json"),
        ("integer_limit", "malformed_json"),
        ("duplicate_keys", "non_strict_json"),
        ("outer_duplicate", "non_strict_json"),
        ("unknown_key", "invalid_schema"),
        ("wrong_type", "invalid_schema"),
        ("no_meanings", "meaning_count"),
        ("four_meanings", "meaning_count"),
        ("two_cases", "case_count"),
        ("six_cases", "case_count"),
        ("duplicate_ids", "duplicate_example_ids"),
        ("unknown_meaning", "unknown_meaning"),
        ("pin", "model_supplied_pin"),
        ("bad_input_json", "invalid_inputs"),
        ("args_object", "invalid_inputs"),
        ("kwargs_list", "invalid_inputs"),
        ("input_extra", "invalid_inputs"),
        ("input_missing", "invalid_inputs"),
        ("input_not_string", "invalid_schema"),
        ("outside_target", "target_not_allowed"),
        ("missing_output", "missing_expected_output"),
        ("behavior", "behavior_only"),
        ("nan", "non_finite_numbers"),
        ("infinity", "non_finite_numbers"),
        ("overflow", "non_finite_numbers"),
        ("input_nan", "non_finite_numbers"),
        ("empty_meaning", "invalid_meaning"),
        ("duplicate_meaning", "invalid_meaning"),
        ("empty_example_id", "invalid_schema"),
    ],
)
async def test_failure_codes(issue: str, code: str) -> None:
    raw = invalid_payload(issue)
    arguments = None
    summary = json.dumps(raw)
    if issue == "malformed":
        summary = "```json\n{}\n```"
    if issue == "integer_limit":
        summary = '{"clarification_needed":' + "9" * 4301 + "}"
    if issue == "duplicate_keys":
        summary = '{"cases":[],"cases":[]}'
    if issue == "overflow":
        summary = summary.replace('"a": 0', '"a": 1e400')
    if issue == "empty_summary":
        summary = " "
    if issue == "empty_arguments":
        arguments = ""
    if issue == "outer_duplicate":
        arguments = '{"summary":"a","summary":"b"}'
    client = model(arguments=arguments or json.dumps({"summary": summary}))
    reply = client._turns[0]
    call = reply.tool_calls[0]
    if issue == "empty_arguments":
        reply = replace(reply, tool_calls=[replace(call, arguments="")])
    if issue == "no_call":
        reply = replace(reply, tool_calls=[])
    if issue == "many_calls":
        reply = replace(reply, tool_calls=[call, replace(call, name="edit_file")])
    if issue == "other_tool":
        reply = replace(reply, tool_calls=[replace(call, name="read_file")])
    if issue == "length":
        reply = replace(reply, finish_reason="length")
    if issue == "model":
        reply = replace(reply, model="different")
    client = ScriptedModel(Script("examples", [reply]), MUSE_SPARK)
    result = await run_examples_call(lambda: client, "context", ALLOWED)
    assert result.kind == "failed" and result.reason == code, result
    assert result.attempt_usage == (Usage(11, 7),) and len(client.requests) == 1
    assert result.call_names == tuple(call.name for call in reply.tool_calls)
    assert CALL_SCOPE.get() is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "field,limit",
    [
        ("example_id", 128),
        ("meaning", 128),
        ("description", 1000),
        ("inputs", 4000),
        ("expected_output", 8000),
        ("note", 2000),
        ("clarification_needed", 2000),
        ("summary", 65536),
    ],
)
async def test_string_bounds(field: str, limit: int) -> None:
    raw = payload()
    if field == "description":
        raw["meanings"][0][field] = "x" * (limit + 1)
    elif field == "clarification_needed":
        raw = {field: "x" * (limit + 1)}
    elif field != "summary":
        raw["cases"][0][field] = "x" * (limit + 1)
    client = model(
        raw, arguments=json.dumps({"summary": "x" * (limit + 1)}) if field == "summary" else None
    )
    result = await run_examples_call(lambda: client, "context", ALLOWED)
    assert result.reason == "strings_too_long"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "target",
    [
        ExampleTarget("pkg::f", "method", False, False),
        ExampleTarget("pkg::f", "function", True, True),
        ExampleTarget("pkg::f", "function", False, False),
        ExampleTarget("pkg::f", "class", False, True),
    ],
)
async def test_only_sync_top_level_functions(target: ExampleTarget) -> None:
    result = await run_examples_call(lambda: model(), "context", [target])
    assert result.reason == "target_not_sync_top_level"


def test_canonical_json() -> None:
    assert canonical_json({"z": "ñ", "a": [1, 1.0]}) == '{"a":[1,1.0],"z":"\\u00f1"}'
    for value in (float("nan"), float("inf"), {"nested": [float("-inf")]}):
        with pytest.raises(ValueError):
            canonical_json(value)


@pytest.mark.anyio
@pytest.mark.parametrize("setup", ["key", "url"])
async def test_not_configured(setup: str, monkeypatch: pytest.MonkeyPatch) -> None:
    if setup == "url":
        monkeypatch.setattr(phase, "META_BASE_URL", "")
    settings = Settings(meta_api_key=None if setup == "key" else "test")
    with pytest.raises(ExamplesNotConfigured):
        examples_client(settings)
    result = await run_examples_call(lambda: examples_client(settings), "context", ALLOWED)
    assert result.kind == result.reason == "not_configured" and not result.attempt_usage


def test_bounded_context_and_prompt_version(monkeypatch: pytest.MonkeyPatch) -> None:
    example = TaskExample(example_id="e", inputs="data", expected_output="null")
    short = example_context(
        "task", target_excerpts=["def f():"], test_excerpts=["assert f()"], supplied=[example]
    )
    assert all(text in short for text in ("task", "def f():", "assert f()", "unconfirmed"))
    long = example_context("x" * 13000, target_excerpts=[], test_excerpts=[], supplied=[])
    assert len(long) == EXAMPLE_CONTEXT_CHARS and long.endswith("text omitted.]")
    versions = [prompt_version(pack) for pack in (False, True)]
    assert versions == ["agent-v1-3a6d2512c603", "agent-v1-0e614be3b125"]
    monkeypatch.setattr(phase, "EXAMPLES_PROMPT", "different prompt")
    assert versions == [prompt_version(pack) for pack in (False, True)]


@pytest.mark.anyio
@pytest.mark.parametrize("succeeds", [False, True])
async def test_observed_failed_attempt_and_no_second_send(
    succeeds: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = model()
    original = client.complete

    async def fail(messages: Any, tools: Any) -> Any:
        reply = await original(messages, tools)
        scope = CALL_SCOPE.get()
        assert scope and scope.attempt_failed
        await scope.attempt_failed(
            {
                "model": "muse-spark",
                "provider": "meta",
                "attempt": 1,
                "max_attempts": 1,
                "cause": "server_error",
                "status": 503,
                "retry_after_s": None,
                "wait_s": None,
                "final": "attempts_used",
            }
        )
        if succeeds:
            return reply
        raise ModelAPIError("failed once")

    monkeypatch.setattr(client, "complete", fail)
    result = await run_examples_call(lambda: client, "context", ALLOWED)
    assert result.kind == ("examples" if succeeds else "failed")
    assert result.reason == (None if succeeds else "call_failed")
    assert result.attempt_usage == ((Usage(), Usage(11, 7)) if succeeds else (Usage(),))
    assert len(client.requests) == 1


@pytest.mark.anyio
async def test_timeout_and_context_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    client = model()
    monkeypatch.setattr(phase, "EXAMPLE_CALL_TIMEOUT_S", -1)
    client._turn_delay_s = 1
    result = await run_examples_call(lambda: client, "context", ALLOWED)
    assert result.reason == "timeout" and len(client.requests) == 1
    assert CALL_SCOPE.get() is None
    result = await run_examples_call(lambda: client, "x" * 12001, ALLOWED)
    assert result.reason == "context_too_long" and len(client.requests) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("expected", [None, "", 1, 1.0, True, [1, 1.0]])
async def test_exact_json_expected_values(expected: Any) -> None:
    raw = payload()
    raw["cases"][0]["expected_output"] = expected
    result = await run_examples_call(lambda: model(raw), "context", ALLOWED)
    assert result.kind == "examples"
    assert result.examples[0].expected_output == canonical_json(expected)


def test_context_strips_supplied_pin() -> None:
    from oversight_contracts.examples import ExamplePin

    example = TaskExample(
        example_id="e",
        inputs="data",
        expected_output="null",
        pin=ExamplePin(source_task="previous private task"),
    )
    text = example_context("task", target_excerpts=[], test_excerpts=[], supplied=[example])
    assert '"pin":null' in text and "previous private task" not in text
    assert example.pin is not None
