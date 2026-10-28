"""Task transport configuration, affinity, revision retention and shutdown; no network."""

from typing import cast

import httpx2 as httpx
import openai
import pytest
from agent_helpers import Recorder, make_run
from test_client import COMPLETION, USER

from oversight.agents import MUSE_SPARK, MUSE_SPARK_OPEN, OpenAIClient, TaskClients
from oversight.workspace import Tree, Workspace
from oversight_contracts.events import ModelProfile

pytestmark = pytest.mark.anyio


def model(profile: object, url: str, http: openai.DefaultAsyncHttpxClient) -> OpenAIClient:
    return OpenAIClient(
        cast(ModelProfile, profile),
        base_url=url,
        api_key="fake",
        max_output_tokens=100,
        http_client=http,
    )


async def test_affinity_is_random_per_task_and_stable_across_turns_and_retries() -> None:
    headers: list[str | None] = []

    def respond(request: httpx.Request) -> httpx.Response:
        headers.append(request.headers.get("x-session-affinity"))
        return httpx.Response(200, json=COMPLETION)

    clients = [
        model(
            MUSE_SPARK_OPEN,
            "https://inference.baseten.co/v1",
            openai.DefaultAsyncHttpxClient(transport=httpx.MockTransport(respond)),
        )
        for _ in range(2)
    ]
    try:
        for client in clients:
            await client.complete(USER, [])
            await client.complete(USER, [])
        assert headers[0] == headers[1] and headers[2] == headers[3]
        assert headers[0] != headers[2]
        assert all(h is not None and len(h) == 32 for h in headers)
    finally:
        for client in clients:
            await client.aclose()


@pytest.mark.parametrize("profile", [MUSE_SPARK, MUSE_SPARK_OPEN])
async def test_meta_endpoint_never_gets_affinity(profile: object) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert "x-session-affinity" not in request.headers
        return httpx.Response(200, json=COMPLETION)

    client = model(
        profile,
        "https://api.meta.ai/v1",
        openai.DefaultAsyncHttpxClient(transport=httpx.MockTransport(respond)),
    )
    try:
        await client.complete(USER, [])
    finally:
        await client.aclose()


async def test_idle_expiry_changes_without_enlarging_sdk_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = openai.DefaultAsyncHttpxClient
    captured: list[httpx.Limits] = []

    def http_client(**kwargs: object) -> openai.DefaultAsyncHttpxClient:
        captured.append(cast(httpx.Limits, kwargs["limits"]))
        return original(transport=httpx.MockTransport(lambda _: httpx.Response(200)))

    monkeypatch.setattr(openai, "DefaultAsyncHttpxClient", http_client)
    client = OpenAIClient(
        MUSE_SPARK, base_url="https://api.test", api_key="fake", max_output_tokens=1
    )
    try:
        assert captured == [
            httpx.Limits(max_connections=1000, max_keepalive_connections=100, keepalive_expiry=30)
        ]
    finally:
        await client.aclose()
        await client.aclose()


async def test_same_client_and_affinity_survive_revision_until_resolution(
    small_tree: tuple[Workspace, Tree],
) -> None:
    headers: list[str] = []

    def finish(request: httpx.Request) -> httpx.Response:
        headers.append(request.headers["x-session-affinity"])
        return httpx.Response(
            200,
            json=COMPLETION
            | {
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": f"finish-{len(headers)}",
                                    "type": "function",
                                    "function": {
                                        "name": "finish",
                                        "arguments": '{"summary":"Done"}',
                                    },
                                }
                            ],
                        },
                    }
                ]
            },
        )

    http = openai.DefaultAsyncHttpxClient(transport=httpx.MockTransport(finish))
    client = model(MUSE_SPARK_OPEN, "https://inference.baseten.co/v1", http)
    pool = TaskClients(lambda _profile, _slot: client)
    run = make_run(small_tree, pool(MUSE_SPARK_OPEN, "B"), Recorder())
    assert (await run.run()).state == "ready_for_review"
    assert not http.is_closed
    assert (await run.revise("Check again", None)).state == "ready_for_review"
    assert headers[0] == headers[1]
    await run.aclose()
    assert http.is_closed
    await pool.aclose()


async def test_shutdown_closes_clients_even_from_failed_starts() -> None:
    http = openai.DefaultAsyncHttpxClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200))
    )
    client = model(MUSE_SPARK, "https://api.meta.ai/v1", http)
    pool = TaskClients(lambda _profile, _slot: client)
    pool(MUSE_SPARK, "A")  # start fails before a retained AgentRun exists
    await pool.aclose()
    assert http.is_closed
