"""Tool-free plan calls, strict output and deadlines, with in-memory responses only."""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from openai.types.chat import ChatCompletion

from oversight.agents import MUSE_SPARK, ModelReply, OpenAIClient, Usage, _plan_call, _prompts
from oversight.agents._plan_call import graph_context, run_plan_call
from oversight.agents.client import CALL_SCOPE, CallScope
from oversight_contracts.graph import GraphNode, GraphSnapshot

from .test_client import COMPLETION

pytestmark = pytest.mark.anyio
TASK = "Keep the exact task.\n  Including whitespace. "


def snapshot() -> GraphSnapshot:
    def node(name: str, kind: Any, parent: str | None) -> GraphNode:
        return GraphNode(
            id=name,
            kind=kind,
            name=name,
            qualname=name,
            path="shop.py",
            start_line=1,
            end_line=2,
            parent_id=parent,
            is_test=False,
        )

    return GraphSnapshot(
        version="captured-main",
        commit="base",
        edges=[],
        parse_errors=[],
        positions={},
        nodes=[
            node("shop", "module", None),
            node("top", "function", "shop"),
            node("Class", "class", "shop"),
            node("method", "method", "Class"),
        ],
    )


def model() -> OpenAIClient:
    return OpenAIClient(
        MUSE_SPARK, base_url="https://plan.test/v1", api_key="test", max_output_tokens=100
    )


def completion(text: str, *, empty: bool = False) -> ChatCompletion:
    return ChatCompletion.model_validate(
        COMPLETION
        | {
            "model": MUSE_SPARK.model,
            "choices": []
            if empty
            else [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": text,
                    },
                }
            ],
        }
    )


@pytest.mark.parametrize(
    "text,empty,valid",
    [
        ('{"task":"Model subtask"}', False, True),
        ('{"task":"' + "x" * 4000 + '"}', False, True),
        ("malformed", False, False),
        ('{"task":"Model subtask","zone":[]}', False, False),
        (json.dumps({"task": "x" * 4001}), False, False),
        ('{"task":4}', False, False),
        ('{"task":" "}', False, False),
        ('{"task":"first","task":"last"}', False, False),
        ('```json\n{"task":"fenced"}\n```', False, False),
        ('{"task":"one"}\n{"task":"two"}', False, False),
        ("[]", False, False),
        ('{"task":NaN}', False, False),
        ("", True, False),
    ],
)
async def test_strict_reply_and_one_receipt(text: str, empty: bool, valid: bool) -> None:
    replies: list[ModelReply] = []
    calls = 0
    ready = asyncio.get_running_loop().time()

    async def record(reply: ModelReply) -> None:
        replies.append(reply)

    async def create(**params: Any) -> ChatCompletion:
        nonlocal calls
        calls += 1
        scope = CALL_SCOPE.get()
        assert scope is not None and scope.role == "plan" and scope.deadline == ready + 90
        assert params["reasoning_effort"] == "low" and "tools" not in params
        assert "tool_choice" not in params
        assert params["messages"][1]["content"] == TASK
        assert "captured-main" in params["messages"][2]["content"]
        return completion(text, empty=empty)

    client = model()
    client._create = create
    try:
        outcome = await run_plan_call(
            lambda: client, TASK, snapshot(), ready_time=ready, record=record
        )
        assert calls == 1 and len(replies) == 1
        assert outcome.task == (json.loads(text)["task"] if valid else TASK)
        assert ("model wrote" if valid else "fallback") in outcome.detail
        assert CALL_SCOPE.get() is None
    finally:
        await client.aclose()


async def test_transport_retries_share_the_deadline_and_retry_after_floor() -> None:
    import httpx2 as httpx
    import openai

    attempts, waits, replies = [], [], []

    async def create(**params: Any) -> ChatCompletion:
        attempts.append(CALL_SCOPE.get())
        response = httpx.Response(
            503, headers={"Retry-After": "2"}, request=httpx.Request("POST", "https://plan.test/v1")
        )
        raise openai.APIStatusError("unavailable", response=response, body={})

    async def sleep(delay: float) -> None:
        waits.append(delay)

    async def record(reply: ModelReply) -> None:
        replies.append(reply)

    client = model()
    client._create, client._sleep, client._jitter = create, sleep, lambda: 0
    try:
        outcome = await run_plan_call(
            lambda: client,
            TASK,
            snapshot(),
            ready_time=asyncio.get_running_loop().time(),
            record=record,
        )
        assert outcome.task == TASK and "ModelAPIError" in outcome.detail
        assert len(attempts) == 5 and all(scope is attempts[0] for scope in attempts)
        assert waits == [2, 2, 2, 2] and not replies
    finally:
        await client.aclose()


async def test_held_call_expires_at_90_seconds_and_restores_its_parent_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    limits: list[asyncio.Timeout] = []
    replies: list[ModelReply] = []
    ready = asyncio.get_running_loop().time()

    def timeout_at(deadline: float) -> asyncio.Timeout:
        assert deadline == ready + 90
        limit = asyncio.timeout_at(deadline)
        limits.append(limit)
        return limit

    monkeypatch.setattr(
        _plan_call,
        "asyncio",
        SimpleNamespace(
            **vars(asyncio)
            | {
                "timeout_at": timeout_at,
            }
        ),
    )

    async def record(reply: ModelReply) -> None:
        replies.append(reply)

    async def create(**params: Any) -> ChatCompletion:
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    parent = CallScope(ready + 600, record)
    token = CALL_SCOPE.set(parent)
    client = model()
    client._create = create
    try:
        pending = asyncio.create_task(
            run_plan_call(lambda: client, TASK, snapshot(), ready_time=ready, record=record)
        )
        await entered.wait()
        limits[0].reschedule(asyncio.get_running_loop().time())
        outcome = await pending
        assert outcome.task == TASK and "90-second" in outcome.detail
        assert CALL_SCOPE.get() is parent
        assert len(replies) == 1 and replies[0].usage == Usage()
    finally:
        CALL_SCOPE.reset(token)
        await client.aclose()


async def test_invalid_tools_truncation_and_model_identity() -> None:
    from oversight.agents import ToolCall

    class Fake:
        profile = MUSE_SPARK
        reply = ModelReply('{"task":"model"}', [])

        async def complete(self, messages: Any, tools: Any) -> ModelReply:
            return self.reply

    client = Fake()
    replies: list[ModelReply] = []

    async def record(reply: ModelReply) -> None:
        replies.append(reply)

    for updates in (
        {"tool_calls": [ToolCall("id", "finish", "{}")]},
        {"finish_reason": "length"},
        {"model": "other-model"},
    ):
        client.reply = replace(ModelReply('{"task":"model"}', []), **updates)
        outcome = await run_plan_call(
            lambda: client,
            TASK,
            snapshot(),
            ready_time=asyncio.get_running_loop().time(),
            record=record,
        )
        assert outcome.task == TASK and "invalid" in outcome.detail
    assert len(replies) == 3


@pytest.mark.parametrize("failure", ["factory", "record"])
async def test_non_reply_value_error_reports_a_call_failure(failure: str) -> None:
    calls = 0

    class Fake:
        profile = MUSE_SPARK

        async def complete(self, messages: Any, tools: Any) -> ModelReply:
            nonlocal calls
            calls += 1
            return ModelReply('{"task":"Valid model subtask"}', [])

    def factory() -> Fake:
        if failure == "factory":
            raise ValueError("invalid client setup")
        return Fake()

    async def record(reply: ModelReply) -> None:
        raise ValueError("invalid usage tick")

    outcome = await run_plan_call(
        factory,
        TASK,
        snapshot(),
        ready_time=asyncio.get_running_loop().time(),
        record=record,
    )
    assert outcome.task == TASK
    assert outcome.detail == "Verbatim fallback: the model call failed (ValueError)."
    assert calls == (0 if failure == "factory" else 1)


def test_context_contains_only_top_level_names_and_states_truncation() -> None:
    graph = snapshot()
    context = graph_context(graph)
    assert "module shop" in context and "function top" in context and "class Class" in context
    assert "method" not in context
    graph = graph.model_copy(update={"nodes": graph.nodes * 1000})
    context = graph_context(graph)
    assert len(context) == 20_000 and context.endswith(
        "[Graph context truncated at 20,000 characters.]"
    )


def test_plan_prompt_participates_in_both_prompt_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    before = [_prompts.prompt_version(pack) for pack in (False, True)]
    monkeypatch.setattr(_prompts, "PLAN_PROMPT", _prompts.PLAN_PROMPT + " Changed.")
    assert all(_prompts.prompt_version(pack) != before[i] for i, pack in enumerate((False, True)))
