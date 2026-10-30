"""A review owns its timeout and accounting, independently of the author's context."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence

from oversight.agents._attempts import attempt_observer
from oversight.agents._crossreview import Inputs
from oversight.agents.client import (
    CALL_SCOPE,
    CallScope,
    Message,
    ModelClient,
    ModelReply,
    ToolSchema,
)
from oversight.agents.reviewer import ReviewOutcome, review_candidate
from oversight.events import EventPayload
from oversight_contracts.events import ModelProfile
from oversight_contracts.review import ChangeTarget


class _AccountedClient:
    def __init__(
        self, client: ModelClient, record: Callable[[ModelReply], Awaitable[None]]
    ) -> None:
        self.client = client
        self.record = record

    @property
    def profile(self) -> ModelProfile:
        return self.client.profile

    async def complete(
        self, messages: Sequence[Message], tools: Sequence[ToolSchema]
    ) -> ModelReply:
        # Empty choices and unanswered attempts already pass through the client's callback.
        reply = await self.client.complete(messages, tools)
        await self.record(reply)
        return reply


async def run_review_call(
    client: ModelClient,
    target: ChangeTarget,
    inputs: Inputs,
    *,
    author: ModelProfile,
    ready_time: float,
    record: Callable[[ModelReply], Awaitable[None]],
    author_reply_models: Sequence[str | None] = (),
    agent_id: str | None = None,
    publish: Callable[[EventPayload], Awaitable[object]] | None = None,
) -> ReviewOutcome:
    """One logical call, bounded by ready + 90 seconds, including all retry waits.

    The caller drains its durable accounting callback even after invalidation or cancellation.
    No author scope, callback or history is reused, including when called under an author scope.
    """
    if (publish is None) != (agent_id is None):
        raise ValueError("review attempt events require both publish and the author's agent_id")
    deadline = ready_time + 90.0
    observer = (
        attempt_observer(publish, agent_id=agent_id, change_id=target.change_id, role="review")
        if publish is not None and agent_id is not None
        else None
    )
    token = CALL_SCOPE.set(CallScope(deadline, record, role="review", attempt_failed=observer))
    try:
        async with asyncio.timeout_at(deadline):
            # An already expired deadline must not enter the provider's request.
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError
            return await review_candidate(
                _AccountedClient(client, record),
                target,
                inputs,
                author=author,
                author_reply_models=author_reply_models,
            )
    finally:
        CALL_SCOPE.reset(token)
