"""Captured decision evidence and simulated HTTP shared across test directories."""

import json
from typing import Any

import httpx2 as httpx
import openai

from oversight.agents import MUSE_SPARK_OPEN, DecisionClient, DecisionInput
from oversight_contracts.review import BehaviorDiffRow, ChangeTarget, NodeBehaviorDiff


class FakeApi:
    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.responses = list(responses)
        self.bodies: list[dict[str, Any]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def ok(body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=body)


def status(code: int, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(code, json={"error": {"message": "test", "type": "x"}}, headers=headers)


INPUT = DecisionInput(
    target=ChangeTarget(
        change_id="change-1",
        revision=1,
        base_commit="base",
        head_commit="head",
        main_commit="main",
        candidate_tree="tree",
    ),
    agent_id="agent-1",
    task="Use a discount of 0.085.",
    difference=NodeBehaviorDiff(
        node_id="pricing.discount",
        compared_count=1,
        differing_count=1,
        rows=[BehaviorDiffRow(inputs="price=100", before="float:90.0", after="float:91.5")],
    ),
)


def body(text: str = '{"choice":"asked_for"}', **updates: Any) -> dict[str, Any]:
    return {
        "id": "decision",
        "object": "chat.completion",
        "created": 0,
        "model": MUSE_SPARK_OPEN.model,
        "choices": [
            {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}
        ],
        "usage": {
            "prompt_tokens": 40,
            "completion_tokens": 20,
            "completion_tokens_details": {"reasoning_tokens": 15},
        },
    } | updates


def client(api: FakeApi, **kwargs: Any) -> DecisionClient:
    return DecisionClient(
        MUSE_SPARK_OPEN,
        base_url="https://decision.test/v1",
        api_key="test",
        max_output_tokens=512,
        http_client=openai.DefaultAsyncHttpxClient(transport=httpx.MockTransport(api.handle)),
        **kwargs,
    )
