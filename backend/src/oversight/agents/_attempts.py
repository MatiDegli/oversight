"""Best-effort failed-attempt diagnostics, independent of retry and accounting policy."""

import logging
from collections.abc import Awaitable, Callable
from typing import Literal, TypedDict

import openai

from oversight.events import EventPayload
from oversight_contracts.events import ModelAttemptFailed, ModelProfile

logger = logging.getLogger(__name__)


class FailedAttempt(TypedDict):
    model: str
    provider: Literal["meta", "baseten", "fake"]
    attempt: int
    max_attempts: int
    cause: Literal["timeout", "connection", "rate_limited", "server_error", "refused"]
    status: int | None
    retry_after_s: float | None
    wait_s: float | None
    final: Literal["attempts_used", "not_retryable", "deadline"] | None


def attempt_observer(
    publish: Callable[[EventPayload], Awaitable[object]],
    *,
    agent_id: str,
    change_id: str | None = None,
    role: Literal["agent", "review", "plan"] = "agent",
) -> Callable[[FailedAttempt], Awaitable[None]]:
    """Bind caller identity without exposing publication through ModelClient."""

    async def observe(failure: FailedAttempt) -> None:
        await publish(
            ModelAttemptFailed(agent_id=agent_id, change_id=change_id, role=role, **failure)
        )

    return observe


async def notify_failed_attempt(
    observer: Callable[[FailedAttempt], Awaitable[None]],
    error: openai.APIError,
    *,
    profile: ModelProfile,
    attempt: int,
    max_attempts: int,
    retry_after_s: float | None,
    wait_s: float | None,
    final: Literal["attempts_used", "not_retryable", "deadline"] | None,
) -> None:
    cause: Literal["timeout", "connection", "rate_limited", "server_error", "refused"]
    status: int | None = None
    if isinstance(error, openai.APITimeoutError):
        cause = "timeout"
    elif isinstance(error, openai.APIConnectionError):
        cause = "connection"
    elif isinstance(error, openai.APIStatusError):
        status = error.status_code
        cause = (
            "rate_limited"
            if status == 429
            else "server_error"
            if 500 <= status <= 599
            else "refused"
        )
    else:
        logger.warning("Model attempt has no transport or HTTP failure classification")
        return
    try:
        await observer(
            FailedAttempt(
                model=profile.id,
                provider=profile.provider,
                attempt=attempt,
                max_attempts=max_attempts,
                cause=cause,
                status=status,
                retry_after_s=retry_after_s,
                wait_s=wait_s,
                final=final,
            )
        )
    except Exception:
        logger.exception("Could not publish failed model attempt")
