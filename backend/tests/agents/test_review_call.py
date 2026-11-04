"""Review scopes isolate deadlines and per-attempt accounting without a paid transport."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
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
    reviewer,
    run_review_call,
)
from oversight.agents._crossreview import build_correction_message
from oversight.agents.client import CALL_SCOPE, CallScope
from oversight.agents.reviewer import MIN_REASK_SECONDS

from .test_attempt_events import ValidatingRecorder
from .test_client import COMPLETION
from .test_reviewer import INPUTS, TARGET

pytestmark = pytest.mark.anyio


def client() -> OpenAIClient:
    return OpenAIClient(
        MUSE_SPARK, base_url="https://review.test/v1", api_key="test", max_output_tokens=100
    )


def completion(*, empty: bool = False, text: str = "VERDICT: accept") -> ChatCompletion:
    return ChatCompletion.model_validate(
        COMPLETION
        | {
            "choices": []
            if empty
            else [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": text},
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
        check_spending=kwargs.pop("check_spending", lambda: None),
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


@pytest.mark.parametrize("remaining", [19.999, 20.0, 20.001])
async def test_reask_minimum_uses_original_deadline_after_accounting(
    monkeypatch: pytest.MonkeyPatch, remaining: float
) -> None:
    ready = asyncio.get_running_loop().time()
    ticks: list[ModelReply] = []
    checks: list[int] = []
    scopes: list[CallScope | None] = []

    async def record(reply: ModelReply) -> None:
        ticks.append(reply)
        monkeypatch.setattr(
            reviewer,
            "asyncio",
            SimpleNamespace(
                get_running_loop=lambda: SimpleNamespace(time=lambda: ready + 90 - remaining)
            ),
        )

    async def create(**params: Any) -> ChatCompletion:
        scopes.append(CALL_SCOPE.get())
        return completion(text="bad" if len(scopes) == 1 else "VERDICT: accept")

    model = client()
    model._create = create
    result = await call(
        model, record, ready_time=ready, check_spending=lambda: checks.append(len(ticks))
    )
    assert MIN_REASK_SECONDS == 20
    enough = remaining >= MIN_REASK_SECONDS
    assert len(scopes) == len(ticks) == (2 if enough else 1)
    assert checks == ([1] if enough else [])
    assert result.review.verdict == ("clean" if enough else "unknown")
    if enough:
        assert scopes[0] is scopes[1]
    else:
        assert result.review.problems == [
            "review_invalid: invalid reviewer output",
            "last non-empty line must be an exact verdict",
            "line 1: expected an uppercase severity and path:positive-line: defect and fix",
            "There was not enough time left to ask the reviewer again.",
        ]
        assert result.decision.reasons == ("Invalid model review.", *result.review.problems)
    assert CALL_SCOPE.get() is None


async def test_spending_refusal_after_recording_first_reply_propagates() -> None:
    ticks: list[ModelReply] = []
    requests = 0

    async def record(reply: ModelReply) -> None:
        ticks.append(reply)

    async def create(**params: Any) -> ChatCompletion:
        nonlocal requests
        requests += 1
        return completion(text="bad")

    def refuse() -> None:
        assert len(ticks) == requests == 1
        raise RuntimeError("scripted admission refusal")

    model = client()
    model._create = create
    with pytest.raises(RuntimeError, match="scripted admission refusal"):
        await call(model, record, check_spending=refuse)
    assert len(ticks) == requests == 1 and ticks[0].usage == Usage(700, 100, 600)
    assert CALL_SCOPE.get() is None


async def test_correction_preparation_counts_toward_minimum_remaining_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = asyncio.get_running_loop().time()
    ticks: list[ModelReply] = []
    checks: list[bool] = []

    def correction(text: str, problems: Any) -> str:
        message = build_correction_message(text, problems)
        monkeypatch.setattr(
            reviewer,
            "asyncio",
            SimpleNamespace(get_running_loop=lambda: SimpleNamespace(time=lambda: ready + 71)),
        )
        return message

    async def record(reply: ModelReply) -> None:
        ticks.append(reply)

    async def create(**params: Any) -> ChatCompletion:
        return completion(text="bad")

    monkeypatch.setattr(reviewer, "build_correction_message", correction)
    model = client()
    model._create = create
    result = await call(model, record, ready_time=ready, check_spending=lambda: checks.append(True))
    assert len(ticks) == 1 and not checks
    assert result.review.problems[-1] == "There was not enough time left to ask the reviewer again."
    assert result.decision.reasons == ("Invalid model review.", *result.review.problems)


@pytest.mark.parametrize("kind", ["terminal", "deadline", "connection", "empty"])
async def test_reask_transport_failure_retains_accounting_and_attempt_diagnostics(
    kind: str,
) -> None:
    import httpx2 as httpx

    from .test_client import FakeApi, ok, status
    from .test_client import client as http_client

    failure = {
        "terminal": status(400),
        "deadline": status(429, {"retry-after": "100"}),
        "connection": openai.APIConnectionError(
            request=httpx.Request("POST", "https://review.test")
        ),
        "empty": ok(completion(empty=True).model_dump()),
    }[kind]
    api = FakeApi(ok(completion(text="bad").model_dump()), failure, status(400))
    ticks: list[ModelReply] = []
    recorder = ValidatingRecorder()

    async def record(reply: ModelReply) -> None:
        ticks.append(reply)

    model = http_client(api, [])
    try:
        result = await call(model, record, agent_id="agent-7", publish=recorder.publish)
    finally:
        await model.aclose()
    assert result.review.verdict == "unknown" and result.decision.action == "needs_you"
    if kind == "empty":
        assert result.reply is not None
        assert "review_empty: the API returned no choices" in result.review.problems
    else:
        assert "last non-empty line" not in " ".join(result.review.problems)
        assert result.reply is None
        assert result.review.problems[0].startswith("review_api_failed:")
    assert len(ticks) == (2 if kind in {"connection", "empty"} else 1)
    if kind == "connection":
        assert ticks[-1].usage == Usage() and ticks[-1].model == MUSE_SPARK.model
    failures = recorder.failures()
    assert len(failures) == (0 if kind == "empty" else 2 if kind == "connection" else 1)
    assert len(api.bodies) == (3 if kind == "connection" else 2)
    assert all(f.role == "review" and f.agent_id == "agent-7" for f in failures)
    if kind == "deadline":
        assert failures[0].final == "deadline"
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
