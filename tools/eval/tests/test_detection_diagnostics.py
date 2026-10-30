"""Every rejected envelope has bounded, text-free evidence, using simulated HTTP only."""

import asyncio
from copy import deepcopy

import httpx2 as httpx
import openai
import pytest
from decision_client_helpers import (  # pyright: ignore[reportMissingImports]
    INPUT,
    FakeApi,
    body,
    ok,
    status,
)
from tools.eval.detection.adapters import Adapter
from tools.eval.detection.config import ARMS
from tools.eval.detection.diagnostics import DetectionClient
from tools.eval.tests.test_detection_adapters import budget as budget

from oversight.agents import MUSE_SPARK, MUSE_SPARK_OPEN, RetryPolicy


def response(stage, model):
    result = body(model=model)
    choice = result["choices"][0]
    message = choice["message"]
    if stage == "model":
        result["model"] = "wrong-model"
    elif stage == "finish_reason":
        choice["finish_reason"] = "length"
        message["content"] = ""
    elif stage == "tool_calls":
        message["tool_calls"] = [
            {"id": "t", "type": "function", "function": {"name": "x", "arguments": "{}"}}
        ]
    elif stage == "empty_choices":
        result["choices"] = []
    elif stage == "choice_count":
        result["choices"].append(deepcopy(choice))
    elif stage == "message":
        choice["message"] = None
    elif stage == "refusal":
        message["refusal"] = "private refusal text"
    elif stage == "legacy_tool_call":
        message["function_call"] = {"name": "x", "arguments": "{}"}
    else:
        message["content"] = {
            "none": '{"choice":"asked_for"}',
            "empty_text": "",
            "reply_length": "x" * 513,
            "json": "private reply, not JSON",
            "duplicate_key": '{"choice":"asked_for","choice":"cannot_tell"}',
            "object": "[]",
            "fields": '{"choice":"asked_for","extra":1}',
            "choice": '{"choice":"approve"}',
        }[stage]
    return result


@pytest.mark.anyio
@pytest.mark.parametrize("arm", ARMS[1:], ids=lambda a: a.id)
@pytest.mark.parametrize(
    "stage",
    [
        "none",
        "model",
        "finish_reason",
        "tool_calls",
        "empty_text",
        "reply_length",
        "json",
        "duplicate_key",
        "object",
        "fields",
        "choice",
        "empty_choices",
        "choice_count",
        "message",
        "refusal",
        "legacy_tool_call",
    ],
)
async def test_each_validation_stage_is_retained(budget, arm, stage):
    profile = MUSE_SPARK_OPEN if arm.profile == "baseten" else MUSE_SPARK
    raw = response(stage, profile.model)
    api = FakeApi(ok(raw))
    sdk = DetectionClient(
        profile,
        base_url="https://decision.test/v1",
        api_key="test",
        max_output_tokens=512,
        http_client=openai.DefaultAsyncHttpxClient(transport=httpx.MockTransport(api.handle)),
    )
    try:
        adapter = Adapter(arm, budget, client=sdk)
        if arm.behavior:
            await adapter.classify_difference(
                INPUT, deadline=asyncio.get_running_loop().time() + 10
            )
        else:
            await adapter.task_diff(INPUT.task, "diff", INPUT.target)
        evidence = adapter.evidence[0]
        d = evidence.diagnostics
        assert d is not None and d.validation_stage == stage
        assert d.reported_model == raw["model"]
        assert d.completion_tokens == 20 and d.reasoning_tokens == 15
        assert d.latency_s > 0
        if raw["choices"] and raw["choices"][0]["message"]:
            assert d.reply_characters == len(raw["choices"][0]["message"]["content"])
            assert d.finish_reason == raw["choices"][0]["finish_reason"]
        assert set(d.model_dump()) == {
            "reported_model",
            "finish_reason",
            "reply_characters",
            "completion_tokens",
            "reasoning_tokens",
            "latency_s",
            "validation_stage",
        }
        assert evidence.unavailable == (None if stage == "none" else "invalid_response")
    finally:
        await sdk.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["transport", "deadline", "unknown_usage"])
async def test_transport_deadline_and_unknown_counts(budget, failure):
    async def slow(request):
        await asyncio.Event().wait()
        raise AssertionError("timed out transport resumed")

    raw = body(usage=None)
    api = FakeApi(status(400) if failure == "transport" else ok(raw))
    sdk = DetectionClient(
        MUSE_SPARK_OPEN,
        base_url="https://decision.test/v1",
        api_key="test",
        max_output_tokens=512,
        retry=RetryPolicy(max_attempts=1),
        http_client=openai.DefaultAsyncHttpxClient(
            transport=httpx.MockTransport(slow if failure == "deadline" else api.handle)
        ),
    )
    try:
        adapter = Adapter(ARMS[3], budget, client=sdk)
        result = await adapter.classify_difference(
            INPUT,
            deadline=asyncio.get_running_loop().time() + (0.1 if failure == "deadline" else 10),
        )
        d = adapter.evidence[0].diagnostics
        assert d is not None
        assert d.validation_stage == ("none" if failure == "unknown_usage" else failure)
        assert d.completion_tokens is None and d.reasoning_tokens is None
        if failure == "deadline":
            assert result.unavailable == "deadline" and d.latency_s >= 0.04
        if failure != "unknown_usage":
            assert d.reply_characters is None and d.reported_model is None
    finally:
        await sdk.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "code",
    [
        "profile",
        "credentials",
        "closed",
        "budget_unconfigured",
        "pricing_unconfigured",
        "budget_blocked",
        "ledger_unknown",
        "ledger_unreliable",
    ],
)
async def test_product_unavailability_stage_is_retained(budget, code):
    from unittest.mock import AsyncMock

    from oversight.agents import DecisionUnavailable

    sdk = DetectionClient(
        MUSE_SPARK_OPEN,
        base_url="https://decision.test/v1",
        api_key="test",
        max_output_tokens=512,
        http_client=openai.DefaultAsyncHttpxClient(transport=httpx.MockTransport(FakeApi().handle)),
    )
    sdk.complete = AsyncMock(side_effect=DecisionUnavailable(code))
    try:
        adapter = Adapter(ARMS[3], budget, client=sdk)
        result = await adapter.classify_difference(
            INPUT, deadline=asyncio.get_running_loop().time() + 10
        )
        d = adapter.evidence[0].diagnostics
        assert result.unavailable == code
        assert d is not None and d.validation_stage == code
        assert d.reported_model is None and d.reply_characters is None
        assert d.completion_tokens is None and d.reasoning_tokens is None
    finally:
        await sdk.aclose()
