"""Constrained D6 decisions over simulated HTTP, with one receipt per sent attempt."""

import asyncio
import json
from dataclasses import replace
from typing import Any, cast

import httpx2 as httpx
import openai
import pytest
from decision_client_helpers import INPUT, FakeApi, body, client, ok, status

from oversight.agents import (
    MUSE_SPARK_OPEN,
    DecisionClient,
    DecisionUnavailable,
    ModelReply,
    RetryPolicy,
    Usage,
    classify_difference,
)
from oversight.agents.client import CALL_SCOPE, CallScope

from .test_attempt_events import ValidatingRecorder

pytestmark = pytest.mark.anyio


async def allow() -> None:
    pass


async def call(model: DecisionClient, ticks: list[ModelReply], **kwargs: Any) -> Any:
    async def record(reply: ModelReply) -> None:
        ticks.append(reply)

    return await classify_difference(
        model,
        kwargs.pop("input", INPUT),
        deadline=kwargs.pop("deadline", asyncio.get_running_loop().time() + 30),
        admission=kwargs.pop("admission", allow),
        accounting=record,
        publish=kwargs.pop("publish", None),
    )


@pytest.mark.parametrize("terminal", [False, True])
async def test_failed_decision_attempts_use_review_role_and_author_identity(terminal: bool) -> None:
    recorder = ValidatingRecorder()
    api = FakeApi(status(400) if terminal else status(503), ok(body()))
    ticks: list[ModelReply] = []
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        [failure] = recorder.failures()
        assert failure.wait_s == delay
        sleeps.append(delay)

    model = client(api, sleep=sleep, jitter=lambda: 1.0)
    try:
        outcome = await call(model, ticks, publish=recorder.publish)
    finally:
        await model.aclose()
    [failure] = recorder.failures()
    assert failure.agent_id == INPUT.agent_id and failure.change_id == INPUT.target.change_id
    assert failure.role == "review" and failure.model == MUSE_SPARK_OPEN.id
    assert failure.provider == "baseten" and failure.attempt == 1 and failure.max_attempts == 5
    assert failure.wait_s == (None if terminal else 1)
    assert failure.final == ("not_retryable" if terminal else None)
    assert outcome.unavailable == ("transport" if terminal else None)
    assert sleeps == ([] if terminal else [1])
    assert len(ticks) == (1 if terminal else 2) and ticks[0].usage == Usage()
    assert CALL_SCOPE.get() is None


@pytest.mark.parametrize("choice", ["asked_for", "not_asked_for", "cannot_tell"])
async def test_choices_have_exact_schema_reasoning_and_identity(choice: str) -> None:
    api = FakeApi(ok(body(json.dumps({"choice": choice}))))
    ticks: list[ModelReply] = []
    model = client(api)
    try:
        outcome = await call(model, ticks)
    finally:
        await model.aclose()
    assert outcome.choice == choice and outcome.unavailable is None
    assert outcome.target == INPUT.target and outcome.node_id == INPUT.difference.node_id
    [request] = api.bodies
    assert request["reasoning_effort"] == "low"
    assert not {"tools", "tool_choice", "logprobs", "top_logprobs"} & request.keys()
    schema = cast(dict[str, Any], request["response_format"])["json_schema"]
    assert schema["strict"] is True
    assert schema["schema"] == {
        "type": "object",
        "properties": {
            "choice": {"type": "string", "enum": ["asked_for", "not_asked_for", "cannot_tell"]}
        },
        "required": ["choice"],
        "additionalProperties": False,
    }
    assert len(ticks) == 1 and ticks[0].usage == Usage(40, 20, None, 15)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json",
        "{}",
        "[]",
        "null",
        '{"choice":"asked"}',
        '{"choice":"not_asked"}',
        '{"choice":null}',
        '{"choice":"asked_for",',
        '{"choice":"asked_for","extra":1}',
        '{"choice":"asked_for","choice":"cannot_tell"}',
        '{"choice":"ASKED_FOR"}',
        '{"choice":"asked_for"} trailing',
    ],
)
async def test_invalid_content_is_unavailable_without_repair(text: str) -> None:
    api = FakeApi(ok(body(text)))
    ticks: list[ModelReply] = []
    model = client(api)
    try:
        outcome = await call(model, ticks)
    finally:
        await model.aclose()
    assert outcome.choice is None and outcome.unavailable == "invalid_response"
    assert len(api.bodies) == len(ticks) == 1


@pytest.mark.parametrize("mutation", ["empty", "length", "tool_calls", "model", "missing_model"])
async def test_unusable_envelopes_are_accounted_once(mutation: str) -> None:
    completion = body()
    if mutation == "empty":
        completion["choices"] = []
    elif mutation in {"model", "missing_model"}:
        completion["model"] = "muse-spark" if mutation == "model" else ""
    else:
        completion["choices"][0]["finish_reason"] = mutation
        if mutation == "tool_calls":
            completion["choices"][0]["message"]["tool_calls"] = [
                {"id": "x", "type": "function", "function": {"name": "finish", "arguments": "{}"}}
            ]
    api = FakeApi(ok(completion))
    ticks: list[ModelReply] = []
    model = client(api)
    try:
        outcome = await call(model, ticks)
    finally:
        await model.aclose()
    assert outcome.choice is None and len(ticks) == 1 and len(api.bodies) == 1
    assert ticks[0].usage.output_tokens == 20


@pytest.mark.parametrize("first", [429, 503, "connection"])
async def test_retry_admits_and_records_every_attempt(first: int | str) -> None:
    api = FakeApi(
        httpx.ConnectError("offline") if first == "connection" else status(cast(int, first)),
        ok(body()),
    )
    ticks: list[ModelReply] = []
    waits: list[float] = []
    admissions = 0

    async def admit() -> None:
        nonlocal admissions
        assert len(ticks) == admissions
        admissions += 1

    async def sleep(delay: float) -> None:
        waits.append(delay)

    model = client(api, jitter=lambda: 0.25, sleep=sleep)
    try:
        outcome = await call(model, ticks, admission=admit)
    finally:
        await model.aclose()
    assert outcome.choice == "asked_for" and admissions == len(ticks) == 2
    assert ticks[0].usage == Usage() and ticks[1].usage.output_tokens == 20
    assert waits == [0.25]
    assert all(b["reasoning_effort"] == "low" and "response_format" in b for b in api.bodies)


async def test_denied_retry_checks_again_after_wait() -> None:
    api = FakeApi(status(429, {"retry-after": "7"}), ok(body()))
    ticks: list[ModelReply] = []
    denied = False

    async def admit() -> None:
        if denied:
            raise DecisionUnavailable("budget_blocked")

    async def sleep(delay: float) -> None:
        nonlocal denied
        assert delay == 7
        denied = True

    model = client(api, sleep=sleep)
    try:
        outcome = await call(model, ticks, admission=admit)
    finally:
        await model.aclose()
    assert outcome.unavailable == "budget_blocked" and len(api.bodies) == len(ticks) == 1


@pytest.mark.parametrize("stage", ["denied", "expired", "inside", "backoff", "admission"])
async def test_pre_send_boundaries_never_add_a_receipt(stage: str) -> None:
    entered = asyncio.Event()
    ticks: list[ModelReply] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        entered.set()
        if stage == "backoff":
            return status(429)
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def admit() -> None:
        if stage == "denied":
            raise DecisionUnavailable("budget_blocked")
        if stage == "admission":
            entered.set()
            await asyncio.Event().wait()

    async def sleep(delay: float) -> None:
        entered.set()
        await asyncio.Event().wait()

    model = DecisionClient(
        MUSE_SPARK_OPEN,
        base_url="https://decision.test/v1",
        api_key="test",
        max_output_tokens=512,
        sleep=sleep,
        http_client=openai.DefaultAsyncHttpxClient(transport=httpx.MockTransport(transport)),
    )
    task = asyncio.create_task(
        call(
            model,
            ticks,
            admission=admit,
            deadline=asyncio.get_running_loop().time() + (-1 if stage == "expired" else 30),
        )
    )
    try:
        if stage in {"denied", "expired"}:
            result = await task
            assert result.choice is None
        else:
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    finally:
        await model.aclose()
    assert len(ticks) == (1 if stage in {"inside", "backoff"} else 0)


async def test_concurrent_candidates_replace_the_author_scope() -> None:
    outer: list[ModelReply] = []
    scopes: list[CallScope] = []
    both = asyncio.Event()

    async def author_record(reply: ModelReply) -> None:
        outer.append(reply)

    async def transport(request: httpx.Request) -> httpx.Response:
        scope = CALL_SCOPE.get()
        assert scope is not None and scope.role == "review"
        scopes.append(scope)
        if len(scopes) == 2:
            both.set()
        await both.wait()
        assert CALL_SCOPE.get() is scope
        return ok(body())

    model = DecisionClient(
        MUSE_SPARK_OPEN,
        base_url="https://decision.test/v1",
        api_key="test",
        max_output_tokens=512,
        http_client=openai.DefaultAsyncHttpxClient(transport=httpx.MockTransport(transport)),
    )
    ticks: list[list[ModelReply]] = [[], []]
    token = CALL_SCOPE.set(CallScope(None, author_record))
    try:
        results = await asyncio.gather(
            *(
                call(
                    model,
                    ticks[i],
                    input=replace(
                        INPUT,
                        target=INPUT.target.model_copy(update={"change_id": f"change-{i + 1}"}),
                    ),
                )
                for i in range(2)
            )
        )
        scope = CALL_SCOPE.get()
        assert scope is not None and scope.record is author_record
    finally:
        CALL_SCOPE.reset(token)
        await model.aclose()
    assert not outer and [len(t) for t in ticks] == [1, 1]
    assert scopes[0] is not scopes[1] and results[0].target != results[1].target


@pytest.mark.parametrize("status_code,attempts", [(400, 1), (401, 1), (502, 3)])
async def test_exhaustion_and_other_4xx(status_code: int, attempts: int) -> None:
    api = FakeApi(*(status(status_code) for _ in range(attempts)))
    ticks: list[ModelReply] = []

    async def sleep(delay: float) -> None:
        pass

    model = client(api, sleep=sleep, retry=RetryPolicy(max_attempts=3))
    try:
        outcome = await call(model, ticks)
    finally:
        await model.aclose()
    assert outcome.unavailable == "transport" and len(api.bodies) == len(ticks) == attempts


@pytest.mark.parametrize("mutation", ["multiple", "refusal", "legacy_tool", "missing_message"])
async def test_malformed_and_ambiguous_envelopes_are_not_choices(mutation: str) -> None:
    completion = body()
    message = completion["choices"][0]["message"]
    if mutation == "multiple":
        completion["choices"] *= 2
    elif mutation == "refusal":
        message["refusal"] = "Cannot comply."
    elif mutation == "legacy_tool":
        message["function_call"] = {"name": "finish", "arguments": "{}"}
    else:
        del completion["choices"][0]["message"]
    api = FakeApi(ok(completion))
    ticks: list[ModelReply] = []
    model = client(api)
    try:
        outcome = await call(model, ticks)
    finally:
        await model.aclose()
    assert outcome.unavailable == "invalid_response" and len(ticks) == len(api.bodies) == 1
    assert ticks[0].usage == Usage(40, 20, None, 15)


@pytest.mark.parametrize("usage", [7, {"prompt_tokens": -1, "completion_tokens": 20}])
async def test_malformed_usage_still_has_one_unknown_receipt(usage: Any) -> None:
    api = FakeApi(ok(body(usage=usage)))
    ticks: list[ModelReply] = []
    model = client(api)
    try:
        await call(model, ticks)
    finally:
        await model.aclose()
    assert len(api.bodies) == len(ticks) == 1
    assert ticks[0].usage.input_tokens is None


async def test_invalid_http_json_is_accounted_without_retry() -> None:
    api = FakeApi(
        httpx.Response(200, content=b"not-json", headers={"content-type": "application/json"})
    )
    ticks: list[ModelReply] = []
    model = client(api)
    try:
        outcome = await call(model, ticks)
    finally:
        await model.aclose()
    assert outcome.choice is None and len(ticks) == len(api.bodies) == 1
    assert ticks[0].usage == Usage()


async def test_deadline_after_admission_sends_nothing() -> None:
    api = FakeApi(ok(body()))
    ticks: list[ModelReply] = []
    model = client(api)

    async def admission() -> None:
        await asyncio.Event().wait()

    try:
        outcome = await call(
            model, ticks, admission=admission, deadline=asyncio.get_running_loop().time() + 0.02
        )
    finally:
        await model.aclose()
    assert outcome.unavailable == "deadline" and not ticks and not api.bodies


async def test_retry_wait_past_deadline_keeps_only_the_sent_receipt() -> None:
    api = FakeApi(status(429, {"retry-after": "75"}), ok(body()))
    ticks: list[ModelReply] = []

    async def sleep(delay: float) -> None:
        raise AssertionError("the deadline rules out this retry wait")

    model = client(api, sleep=sleep)
    try:
        outcome = await call(model, ticks)
    finally:
        await model.aclose()
    assert outcome.choice is None and len(ticks) == len(api.bodies) == 1


async def test_ordinary_client_keeps_its_unconstrained_parameters() -> None:
    from oversight.agents import OpenAIClient

    api = FakeApi(ok(body()))
    model = OpenAIClient(
        MUSE_SPARK_OPEN,
        base_url="https://ordinary.test/v1",
        api_key="test",
        max_output_tokens=512,
        http_client=openai.DefaultAsyncHttpxClient(transport=httpx.MockTransport(api.handle)),
    )
    try:
        await model.complete([], [])
    finally:
        await model.aclose()
    assert "response_format" not in api.bodies[0] and "logprobs" not in api.bodies[0]


async def test_capture_copies_evidence_and_requires_confirmed_examples() -> None:
    from oversight.agents import DecisionInput
    from oversight_contracts.examples import ExamplePin, TaskExample

    example = TaskExample(example_id="example", inputs="price=100", expected_output="91.5")
    with pytest.raises(ValueError, match="confirmation"):
        replace(INPUT, confirmed_examples=(example,))
    example = example.model_copy(update={"pin": ExamplePin(source_task=INPUT.task)})
    source = INPUT.difference.model_copy(deep=True)
    target = INPUT.target.model_copy(deep=True)
    captured = DecisionInput(target, INPUT.agent_id, INPUT.task, source, (example,))
    source.rows.clear()
    # Contract instances are frozen; their nested row list still needs a detached capture.
    api = FakeApi(ok(body()))
    model = client(api)
    try:
        outcome = await call(model, [], input=captured)
    finally:
        await model.aclose()
    payload = json.loads(cast(list[dict[str, Any]], api.bodies[0]["messages"])[1]["content"])
    assert payload["confirmed_examples"][0]["expected_output"] == "91.5"
    assert payload["difference"]["rows"] and outcome.target.revision == 1


async def test_failed_accounting_is_explicit_unavailability_without_double_recording() -> None:
    api = FakeApi(ok(body()))
    model = client(api)
    receipts: list[ModelReply] = []

    async def record(reply: ModelReply) -> None:
        receipts.append(reply)
        raise OSError("private storage error")

    try:
        outcome = await classify_difference(
            model,
            INPUT,
            deadline=asyncio.get_running_loop().time() + 30,
            admission=allow,
            accounting=record,
        )
    finally:
        await model.aclose()
    assert outcome.unavailable == "ledger_unreliable" and len(receipts) == len(api.bodies) == 1
