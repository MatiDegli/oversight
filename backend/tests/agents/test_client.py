"""The OpenAI-SDK client on a fake HTTP transport: retries with backoff, the reasoning setting on
every call, and usage that stays unknown when the API does not report it. No network, no key."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx2 as httpx  # the SDK's HTTP library: its MockTransport stands in for the network
import openai
import pytest

from oversight.agents import (
    MUSE_SPARK,
    MUSE_SPARK_OPEN,
    Message,
    ModelAPIError,
    OpenAIClient,
    Pricing,
    RetryPolicy,
    ToolSchema,
    Usage,
    UsageLedger,
)
from oversight.agents.client import CALL_SCOPE, CallScope, ModelReply

pytestmark = pytest.mark.anyio

COMPLETION: dict[str, object] = {
    "id": "cmpl-test",
    "object": "chat.completion",
    "created": 0,
    "model": "muse-spark-1.3-contributor",
    "choices": [
        {
            "index": 0,
            "finish_reason": "tool_calls",
            "message": {
                "role": "assistant",
                "content": "Reading it first.",
                "tool_calls": [
                    {
                        "id": "call-a",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path": "a.py"}'},
                    }
                ],
            },
        }
    ],
    "usage": {
        "prompt_tokens": 700,
        "completion_tokens": 100,
        "total_tokens": 800,
        "prompt_tokens_details": {"cached_tokens": 600},
    },
}


class FakeApi:
    """Answers each request with the next queued response and keeps the request bodies."""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self._responses = list(responses)
        self.bodies: list[dict[str, object]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def ok(body: dict[str, object] = COMPLETION) -> httpx.Response:
    return httpx.Response(200, json=body)


def status(code: int, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(code, json={"error": {"message": "test", "type": "x"}}, headers=headers)


def client(api: FakeApi, sleeps: list[float], **kwargs: object) -> OpenAIClient:
    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    transport = httpx.MockTransport(api.handle)
    options: dict[str, object] = {"profile": MUSE_SPARK, "jitter": lambda: 1.0} | kwargs
    return OpenAIClient(
        base_url="https://api.test/v1",
        api_key="test-key",
        max_output_tokens=2048,
        http_client=openai.DefaultAsyncHttpxClient(transport=transport),
        sleep=sleep,
        **options,  # type: ignore[arg-type]
    )


USER: list[Message] = [{"role": "user", "content": "hi"}]
TOOLS: list[ToolSchema] = [
    {"type": "function", "function": {"name": "finish", "parameters": {"type": "object"}}}
]


async def test_retries_429_and_5xx_with_exponential_backoff() -> None:
    api = FakeApi(status(429), status(503), ok())
    sleeps: list[float] = []
    reply = await client(api, sleeps).complete(USER, TOOLS)
    assert sleeps == [1.0, 2.0]
    assert len(api.bodies) == 3
    assert reply.tool_calls[0].name == "read_file"
    assert reply.tool_calls[0].arguments == '{"path": "a.py"}'
    assert reply.text == "Reading it first." and reply.model == "muse-spark-1.3-contributor"


async def test_jitter_scales_the_delay_and_retry_after_is_a_floor() -> None:
    api = FakeApi(status(500), status(429, {"retry-after": "7"}), ok())
    sleeps: list[float] = []
    await client(api, sleeps, jitter=lambda: 0.25).complete(USER, TOOLS)
    assert sleeps == [0.25, 7.0]


async def test_gives_up_after_the_last_attempt() -> None:
    api = FakeApi(status(502), status(502), status(502))
    sleeps: list[float] = []
    model = client(api, sleeps, retry=RetryPolicy(max_attempts=3, base_s=1.0, cap_s=30.0))
    with pytest.raises(ModelAPIError, match="after 3 attempts: HTTP 502"):
        await model.complete(USER, TOOLS)
    assert sleeps == [1.0, 2.0] and len(api.bodies) == 3


async def test_client_errors_are_not_retried() -> None:
    api = FakeApi(status(400), ok())
    sleeps: list[float] = []
    with pytest.raises(ModelAPIError, match="HTTP 400"):
        await client(api, sleeps).complete(USER, TOOLS)
    assert sleeps == [] and len(api.bodies) == 1


async def test_every_call_sets_the_model_reasoning_and_limits() -> None:
    api = FakeApi(ok(), ok())
    sleeps: list[float] = []
    await client(api, sleeps).complete(USER, TOOLS)
    body = api.bodies[0]
    assert body["model"] == "muse-spark-1.3-contributor" and body["reasoning_effort"] == "low"
    assert body["max_completion_tokens"] == 2048 and body["tool_choice"] == "auto"
    assert body["tools"] == TOOLS
    # A profile with no reasoning setting sends none.
    no_reasoning = MUSE_SPARK_OPEN.model_copy(update={"reasoning": None})
    await client(api, sleeps, profile=no_reasoning).complete(USER, TOOLS)
    assert "reasoning_effort" not in api.bodies[1]


async def test_muse_spark_open_sends_reasoning_effort_low_at_the_top_level() -> None:
    # docs/api-probe.md, "Baseten: Muse Spark Open": Chat Completions takes the top-level
    # `reasoning_effort`; the profile sends it on every call, with the model id the API reports.
    api = FakeApi(ok(), ok())
    await client(api, [], profile=MUSE_SPARK_OPEN).complete(USER, TOOLS)
    await client(api, [], profile=MUSE_SPARK_OPEN).complete(USER, [])
    for body in api.bodies:
        assert body["model"] == "meta-models/muse-spark-open-1026"
        assert body["reasoning_effort"] == "low"
        assert "reasoning" not in body and "extra_body" not in body


async def test_usage_is_read_or_left_unknown() -> None:
    no_usage = {k: v for k, v in COMPLETION.items() if k != "usage"}
    no_details = COMPLETION | {"usage": {"prompt_tokens": 5, "completion_tokens": 2}}
    api = FakeApi(ok(), ok(no_usage), ok(no_details))
    model = client(api, [])
    assert (await model.complete(USER, TOOLS)).usage == Usage(700, 100, 600)
    assert (await model.complete(USER, TOOLS)).usage == Usage(None, None, None)
    assert (await model.complete(USER, TOOLS)).usage == Usage(5, 2, None)


def test_cost_is_unknown_without_counts_and_the_brake_follows_spend() -> None:
    price = Pricing(input_usd=1.0, output_usd=4.0, cached_input_usd=0.5)
    assert price.cost(Usage(None, 10, None)) is None
    assert price.cost(Usage(1_000_000, 0, 500_000)) == pytest.approx(0.75)
    ledger = UsageLedger(budget_usd=10.0)
    assert ledger.record(None) == 0.0 and ledger.brake == "ok"
    assert ledger.record(5.0) == 5.0 and ledger.brake == "warn_50"
    ledger.record(3.0)
    assert ledger.brake == "warn_80"
    ledger.record(1.0)
    assert ledger.brake == "warn_80"  # 90% exactly: above it, not at it, blocks
    ledger.record(0.5)
    assert ledger.brake == "blocked"


@pytest.mark.parametrize("header", ["75", "75.5"])
async def test_retry_after_seconds_is_not_capped(header: str) -> None:
    api = FakeApi(status(429, {"retry-after": header}), ok())
    sleeps: list[float] = []
    await client(api, sleeps).complete(USER, TOOLS)
    assert sleeps == [float(header)]


async def test_retry_after_http_date_is_not_capped() -> None:
    later = datetime.now(UTC) + timedelta(seconds=76)
    api = FakeApi(status(503, {"retry-after": format_datetime(later, usegmt=True)}), ok())
    sleeps: list[float] = []
    await client(api, sleeps).complete(USER, TOOLS)
    assert 74 < sleeps[0] <= 76


@pytest.mark.parametrize("header", ["invalid", "NaN", "inf", "-1"])
async def test_invalid_or_past_retry_after_uses_backoff(header: str) -> None:
    api = FakeApi(status(429, {"retry-after": header}), ok())
    sleeps: list[float] = []
    await client(api, sleeps).complete(USER, TOOLS)
    assert sleeps == [1.0]


async def test_wait_past_deadline_halts_without_sleeping_or_retrying() -> None:
    api = FakeApi(status(429, {"retry-after": "75"}), ok())
    sleeps: list[float] = []
    recorded: list[ModelReply] = []

    async def record(reply: ModelReply) -> None:
        recorded.append(reply)

    token = CALL_SCOPE.set(CallScope(asyncio.get_running_loop().time() + 10, record))
    try:
        with pytest.raises(ModelAPIError, match="75 s exceeds the task deadline"):
            await client(api, sleeps).complete(USER, TOOLS)
    finally:
        CALL_SCOPE.reset(token)
    assert sleeps == [] and len(api.bodies) == 1 and recorded == []


@pytest.mark.parametrize("error", [httpx.ReadTimeout("timeout"), httpx.ConnectError("offline")])
async def test_transport_errors_retry_and_record_uncertain_usage(error: Exception) -> None:
    api = FakeApi(error, ok())
    recorded: list[ModelReply] = []
    sleeps: list[float] = []

    async def record(reply: ModelReply) -> None:
        recorded.append(reply)

    token = CALL_SCOPE.set(CallScope(None, record))
    try:
        reply = await client(api, sleeps).complete(USER, TOOLS)
    finally:
        CALL_SCOPE.reset(token)
    assert len(api.bodies) == 2 and sleeps == [1.0]
    assert len(recorded) == 1 and recorded[0].usage == Usage()
    assert reply.usage == Usage(700, 100, 600)
    assert all(body["reasoning_effort"] == "low" for body in api.bodies)


async def test_transport_attempts_are_bounded_independently_of_model_turns() -> None:
    api = FakeApi(*(httpx.ReadTimeout("timeout") for _ in range(3)), ok())
    sleeps: list[float] = []
    with pytest.raises(ModelAPIError, match="after 3 attempts: APITimeoutError"):
        await client(api, sleeps, retry=RetryPolicy(max_attempts=3)).complete(USER, TOOLS)
    assert len(api.bodies) == 3 and sleeps == [1.0, 2.0]


async def test_finish_reason_survives_http_parsing() -> None:
    body = COMPLETION | {
        "choices": [
            {
                "finish_reason": "length",
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "cut",
                            "type": "function",
                            "function": {"name": "edit_file", "arguments": '{"path":'},
                        }
                    ],
                },
            }
        ]
    }
    reply = await client(FakeApi(ok(body)), []).complete(USER, TOOLS)
    assert reply.finish_reason == "length"
    assert reply.tool_calls[0].arguments == '{"path":'


@pytest.mark.parametrize("first", ["length", "stop", "no_choices", "timeout"])
async def test_http_attempts_feed_run_accounting_once(small_tree: object, first: str) -> None:
    from typing import cast

    from agent_helpers import Recorder, make_run

    from oversight.workspace import Tree, Workspace
    from oversight_contracts.events import UsageTick

    finish = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "done",
                "type": "function",
                "function": {"name": "finish", "arguments": '{"summary": "Done"}'},
            }
        ],
    }
    final = COMPLETION | {
        "choices": [{"index": 0, "finish_reason": "tool_calls", "message": finish}]
    }
    if first == "timeout":
        initial = httpx.ReadTimeout("timeout")
    else:
        message = finish if first == "length" else {"role": "assistant", "content": None}
        choices = (
            []
            if first == "no_choices"
            else [{"index": 0, "finish_reason": first, "message": message}]
        )
        initial = ok(COMPLETION | {"choices": choices})
    api = FakeApi(initial, ok(final))
    recorder = Recorder()
    run = make_run(cast(tuple[Workspace, Tree], small_tree), client(api, []), recorder)
    end = await run.run()
    ticks = cast(list[UsageTick], recorder.of("UsageTick"))
    if first == "no_choices":
        assert end.state == "halted" and len(ticks) == 1 and len(api.bodies) == 1
    else:
        assert end.state == "ready_for_review" and end.steps == 1
        assert len(ticks) == 2 and len(api.bodies) == 2
    assert ticks[0].input_tokens == (None if first == "timeout" else 700)
    if first == "timeout":
        assert ticks[0].cost_usd is None and ticks[0].output_tokens is None
    else:
        assert ticks[0].output_tokens == 100


async def test_cancellation_during_http_records_unknown_usage() -> None:
    entered = asyncio.Event()
    recorded: list[ModelReply] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def record(reply: ModelReply) -> None:
        recorded.append(reply)

    sdk = openai.DefaultAsyncHttpxClient(transport=httpx.MockTransport(transport))
    model = OpenAIClient(
        MUSE_SPARK,
        base_url="https://api.test/v1",
        api_key="fake",
        max_output_tokens=100,
        http_client=sdk,
    )
    token = CALL_SCOPE.set(CallScope(None, record))
    try:
        request = asyncio.create_task(model.complete(USER, TOOLS))
        await entered.wait()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
    finally:
        CALL_SCOPE.reset(token)
        await sdk.aclose()
    assert len(recorded) == 1 and recorded[0].usage == Usage()
