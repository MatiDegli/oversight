"""Review scopes isolate deadlines and per-attempt accounting without a paid transport."""

import asyncio
from dataclasses import replace
from typing import Any

import openai
import pytest
from openai.types.chat import ChatCompletion

from oversight import agents
from oversight.agents import (
    FAKE_MODEL,
    MUSE_SPARK,
    ModelReply,
    OpenAIClient,
    Usage,
    run_review_call,
)
from oversight.agents.client import CALL_SCOPE, CallScope

from .test_attempt_events import ValidatingRecorder
from .test_client import COMPLETION
from .test_reviewer import INPUTS, TARGET

pytestmark = pytest.mark.anyio


def client() -> OpenAIClient:
    return OpenAIClient(
        MUSE_SPARK, base_url="https://review.test/v1", api_key="test", max_output_tokens=100
    )


def completion(*, empty: bool = False) -> ChatCompletion:
    return ChatCompletion.model_validate(
        COMPLETION
        | {
            "choices": []
            if empty
            else [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "VERDICT: accept"},
                }
            ]
        }
    )


async def call(model: OpenAIClient, record: Any, **kwargs: Any) -> Any:
    return await run_review_call(
        model,
        TARGET,
        replace(INPUTS, reviewer_model=model.profile.model),
        author=FAKE_MODEL,
        ready_time=kwargs.pop("ready_time", asyncio.get_running_loop().time()),
        record=record,
        **kwargs,
    )


async def test_own_scope_under_author_scope_and_concurrent_calls() -> None:
    assert not hasattr(agents, "CALL_SCOPE") and not hasattr(agents, "CallScope")
    author_ticks: list[ModelReply] = []
    scopes: list[CallScope] = []
    ticks: list[list[ModelReply]] = [[], []]
    both = asyncio.Event()

    async def author_record(reply: ModelReply) -> None:
        author_ticks.append(reply)

    author = CallScope(asyncio.get_running_loop().time() + 600, author_record)

    async def run(index: int) -> None:
        ready = asyncio.get_running_loop().time() - index

        async def record(reply: ModelReply) -> None:
            ticks[index].append(reply)

        async def create(**params: Any) -> ChatCompletion:
            scope = CALL_SCOPE.get()
            assert scope is not None and scope is not author
            assert (
                scope.deadline == ready + 90 and scope.record is record and scope.role == "review"
            )
            scopes.append(scope)
            if len(scopes) == 2:
                both.set()
            await both.wait()
            assert CALL_SCOPE.get() is scope
            assert params["reasoning_effort"] == "low" and "tools" not in params
            return completion()

        model = client()
        model._create = create
        result = await call(model, record, ready_time=ready)
        assert result.review.verdict == "clean" and CALL_SCOPE.get() is author

    token = CALL_SCOPE.set(author)
    try:
        await asyncio.gather(run(0), run(1))
    finally:
        CALL_SCOPE.reset(token)
    assert not author_ticks and [len(t) for t in ticks] == [1, 1]
    assert scopes[0].record is not scopes[1].record


@pytest.mark.parametrize("empty", [False, True])
async def test_response_records_once_including_empty_choices(empty: bool) -> None:
    ticks: list[ModelReply] = []

    async def record(reply: ModelReply) -> None:
        ticks.append(reply)

    async def create(**params: Any) -> ChatCompletion:
        return completion(empty=empty)

    model = client()
    model._create = create
    result = await call(model, record)
    assert len(ticks) == 1 and ticks[0].usage == Usage(700, 100, 600)
    assert result.review.verdict == ("unknown" if empty else "clean")
    assert CALL_SCOPE.get() is None


@pytest.mark.parametrize("stage", ["before_task", "expired", "backoff", "inside"])
async def test_cancellation_boundary_only_inflight_adds_unknown(stage: str) -> None:
    ticks: list[ModelReply] = []
    entered = asyncio.Event()
    requests = 0

    async def record(reply: ModelReply) -> None:
        ticks.append(reply)

    async def create(**params: Any) -> ChatCompletion:
        nonlocal requests
        requests += 1
        if stage == "backoff":
            import httpx2 as httpx

            response = httpx.Response(429, request=httpx.Request("POST", "https://review.test"))
            raise openai.RateLimitError("retry", response=response, body=None)
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def sleep(delay: float) -> None:
        entered.set()
        await asyncio.Event().wait()

    model = client()
    model._create, model._sleep = create, sleep
    task = asyncio.create_task(
        call(
            model,
            record,
            ready_time=asyncio.get_running_loop().time() - (91 if stage == "expired" else 0),
        )
    )
    if stage == "expired":
        with pytest.raises(TimeoutError):
            await task
    else:
        if stage != "before_task":
            await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert requests == (1 if stage in {"backoff", "inside"} else 0)
    assert len(ticks) == (1 if stage == "inside" else 0)
    if ticks:
        assert ticks[0].usage == Usage() and ticks[0].model == MUSE_SPARK.model
    assert CALL_SCOPE.get() is None


async def test_long_retry_after_uses_review_deadline_not_author_deadline() -> None:
    import httpx2 as httpx

    ticks: list[ModelReply] = []
    requests = 0

    async def record(reply: ModelReply) -> None:
        ticks.append(reply)

    async def create(**params: Any) -> ChatCompletion:
        nonlocal requests
        requests += 1
        response = httpx.Response(
            429,
            headers={"retry-after": "100"},
            request=httpx.Request("POST", "https://review.test"),
        )
        raise openai.RateLimitError("retry", response=response, body=None)

    async def no_sleep(delay: float) -> None:
        raise AssertionError("retry delay exceeds review deadline")

    model = client()
    model._create, model._sleep = create, no_sleep
    token = CALL_SCOPE.set(CallScope(asyncio.get_running_loop().time() + 600, record))
    try:
        result = await call(model, record)
        assert "100 s exceeds the task deadline" in result.review.problems[0]
    finally:
        CALL_SCOPE.reset(token)
    assert requests == 1 and not ticks


async def test_connection_failure_records_unknown_then_response_once() -> None:
    import httpx2 as httpx

    ticks: list[ModelReply] = []
    requests = 0

    async def record(reply: ModelReply) -> None:
        ticks.append(reply)

    async def create(**params: Any) -> ChatCompletion:
        nonlocal requests
        requests += 1
        if requests == 1:
            raise openai.APIConnectionError(request=httpx.Request("POST", "https://review.test"))
        return completion()

    async def no_wait(delay: float) -> None:
        pass

    model = client()
    model._create, model._sleep = create, no_wait
    await call(model, record)
    assert len(ticks) == 2 and ticks[0].usage == Usage()
    assert ticks[1].usage == Usage(700, 100, 600)


@pytest.mark.parametrize("terminal", [False, True])
async def test_failed_review_attempts_use_author_identity_and_own_profile(terminal: bool) -> None:
    from .test_client import FakeApi, ok, status
    from .test_client import client as http_client

    recorder = ValidatingRecorder()
    ticks: list[ModelReply] = []

    async def record(reply: ModelReply) -> None:
        ticks.append(reply)

    api = FakeApi(
        status(400) if terminal else status(429, {"retry-after": "2"}),
        ok(completion().model_dump()),
    )
    sleeps: list[float] = []
    model = http_client(api, sleeps)
    try:
        result = await call(model, record, agent_id="agent-7", publish=recorder.publish)
    finally:
        await model.aclose()
    [failure] = recorder.failures()
    assert failure.agent_id == "agent-7" and failure.change_id == TARGET.change_id
    assert failure.role == "review" and failure.model == MUSE_SPARK.id
    assert failure.provider == "meta" and failure.attempt == 1 and failure.max_attempts == 5
    assert failure.wait_s == (None if terminal else 2)
    assert failure.final == ("not_retryable" if terminal else None)
    assert result.review.verdict == ("unknown" if terminal else "clean")
    assert len(ticks) == (0 if terminal else 1)
    assert sleeps == ([] if terminal else [2])
    assert CALL_SCOPE.get() is None
