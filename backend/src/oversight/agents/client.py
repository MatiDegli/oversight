"""The model-client interface, the provider profiles, and the OpenAI-SDK client.

The tool loop sees only `ModelClient`: a list of Chat Completions messages and tool schemas in, one
`ModelReply` out. The SDK client points at the agent's own endpoint and model, sends the reasoning
setting on every call, and retries transport errors, 429 and 5xx with backoff and full jitter.
Usage the API does not report stays `None`, never 0.
"""

import asyncio
import math
import random
from collections.abc import Awaitable, Callable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol, cast
from urllib.parse import urlparse
from uuid import uuid4

import httpx2 as httpx
import openai
from openai.types.chat import ChatCompletion

from oversight_contracts.events import ModelProfile

type Message = dict[str, object]
"""One Chat Completions message, as JSON."""
type ToolSchema = dict[str, object]
"""One entry of `tools`: `{"type": "function", "function": {...}}`."""

META_BASE_URL = "https://api.meta.ai/v1"
BASETEN_BASE_URL = "https://inference.baseten.co/v1"

MUSE_SPARK = ModelProfile(
    id="muse-spark",
    label="Muse Spark",
    provider="meta",
    model="muse-spark-1.3-contributor",
    # Contributor mode (DECISIONS.md, "Model API"): the maintainer chose it; Meta may train on the
    # prompts and replies, and the code the agents see is public. docs/api-probe.md: reasoning is
    # on by default and `none` is rejected; low was accepted, also on this id.
    reasoning="low",
)
MUSE_SPARK_OPEN = ModelProfile(
    id="muse-spark-open",
    label="Muse Spark Open",
    provider="baseten",
    model="meta-models/muse-spark-open-1026",
    # docs/api-probe.md, "Baseten: Muse Spark Open": the API reports this exact id, and native tool
    # calls, tool results and parallel calls work. Reasoning is on by default, and the top-level
    # `reasoning_effort` is accepted with `low` and with `none`; `none` still produced reasoning
    # tokens, so it does not turn reasoning off. `low` is sent on every call, as on agent A's
    # profile; the probe's one sample each cannot show that it changes the cost.
    reasoning="low",
)
FAKE_MODEL = ModelProfile(
    id="fake", label="Scripted fake model", provider="fake", model="fake-scripted", reasoning=None
)

FAKE_MODEL_B = ModelProfile(
    id="fake-b",
    label="Scripted fake model B",
    provider="fake",
    model="fake-scripted-b",
    reasoning=None,
)

UNVERIFIED_PROFILES: frozenset[str] = frozenset()
"""Profiles whose reasoning setting no probe has confirmed. A run on one is refused, since the agent
would ride the provider's default. Empty: docs/api-probe.md confirmed both Muse profiles'. A new
profile's id goes here until tools/probe_api.py has confirmed its setting. The client itself still
accepts such a profile, for the probe."""


class UnverifiedProfile(Exception):
    """A run was asked for on a profile in `UNVERIFIED_PROFILES`."""


def require_verified(profile: ModelProfile) -> None:
    """`UnverifiedProfile`, with a message for the person, if no run may use `profile` yet."""
    if profile.id in UNVERIFIED_PROFILES:
        raise UnverifiedProfile(
            f"{profile.label} ({profile.model}) cannot run agents yet: its reasoning setting is "
            "unverified. Probe its provider (tools/probe_api.py) and set the profile's reasoning "
            "mode first."
        )


@dataclass(frozen=True)
class Usage:
    """Token counts for one call; `None` means the API did not report it."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str
    """The raw JSON text the model sent; the tool loop parses it."""


@dataclass(frozen=True)
class ModelReply:
    text: str | None
    tool_calls: list[ToolCall]
    usage: Usage = field(default_factory=Usage)
    model: str | None = None
    """The model id the API reported."""
    finish_reason: str | None = None


@dataclass(frozen=True)
class CallScope:
    """Run-local retry deadline and accounting for attempts with no usable reply.

    Context-local so clients retain the small ModelClient interface, and simultaneous agents
    cannot share a deadline or charge uncertain usage to each other.
    """

    deadline: float | None
    record: Callable[[ModelReply], Awaitable[None]]


CALL_SCOPE: ContextVar[CallScope | None] = ContextVar("agent_model_call", default=None)


class ModelClient(Protocol):
    @property
    def profile(self) -> ModelProfile: ...

    async def complete(
        self, messages: Sequence[Message], tools: Sequence[ToolSchema]
    ) -> ModelReply: ...


class ModelAPIError(Exception):
    """The model call failed for good: a refused request, or retries used up."""


class EmptyChoicesError(ModelAPIError):
    """No verdict, but retain the response identity and usage for the review adapter."""

    def __init__(self, reply: ModelReply) -> None:
        super().__init__("the API returned no choices")
        self.reply = reply


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    base_s: float = 1.0
    cap_s: float = 30.0

    def delay(self, attempt: int, jitter: float, retry_after_s: float | None) -> float:
        """Full jitter over an exponential ceiling; a server's Retry-After is a floor."""
        ceiling = min(self.cap_s, self.base_s * 2**attempt)
        delay = ceiling * jitter
        if retry_after_s is not None:
            delay = max(delay, retry_after_s)
        return delay


def _retryable(error: openai.APIError) -> bool:
    if isinstance(error, openai.APIConnectionError):
        return True  # includes APITimeoutError
    if isinstance(error, openai.APIStatusError):
        return error.status_code == 429 or error.status_code >= 500
    return False


def _retry_after(error: openai.APIError) -> float | None:
    if not isinstance(error, openai.APIStatusError):
        return None
    value = error.response.headers.get("retry-after")
    if value is None:
        return None
    try:
        seconds = float(value)
        return max(0.0, seconds) if math.isfinite(seconds) else None
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=UTC)
            return max(0.0, (date - datetime.now(UTC)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return None


def usage_of(completion: ChatCompletion) -> Usage:
    usage = completion.usage
    if usage is None:
        return Usage()
    details = usage.prompt_tokens_details
    return Usage(
        input_tokens=usage.prompt_tokens,
        output_tokens=usage.completion_tokens,
        cached_tokens=details.cached_tokens if details is not None else None,
    )


def reply_of(completion: ChatCompletion) -> ModelReply:
    if not completion.choices:
        raise EmptyChoicesError(ModelReply(None, [], usage_of(completion), completion.model))
    message = completion.choices[0].message
    calls = [
        ToolCall(
            id=call.id,
            name=call.function.name if call.type == "function" else call.custom.name,
            arguments=(call.function.arguments or "{}")
            if call.type == "function"
            else call.custom.input,
        )
        for call in message.tool_calls or []
    ]
    return ModelReply(
        text=message.content or None,
        tool_calls=calls,
        usage=usage_of(completion),
        model=completion.model or None,
        finish_reason=completion.choices[0].finish_reason,
    )


class OpenAIClient:
    """Chat Completions through the official SDK, for Meta or Baseten. The SDK's own retries are
    off: this class retries, so every attempt and delay is under its control and tested."""

    def __init__(
        self,
        profile: ModelProfile,
        *,
        base_url: str,
        api_key: str,
        max_output_tokens: int,
        retry: RetryPolicy | None = None,
        http_client: openai.DefaultAsyncHttpxClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
        timeout_s: float = 120.0,
    ) -> None:
        self._profile = profile
        self._max_output_tokens = max_output_tokens
        self._retry = retry or RetryPolicy()
        self._sleep = sleep
        self._jitter = jitter
        # Keep the SDK's existing pool sizes; only its idle expiry changes.
        http_client = http_client or openai.DefaultAsyncHttpxClient(
            limits=httpx.Limits(
                max_connections=1000, max_keepalive_connections=100, keepalive_expiry=30
            )
        )
        self._affinity = (
            uuid4().hex
            if profile.provider == "baseten"
            and urlparse(base_url).hostname == "inference.baseten.co"
            else None
        )
        sdk = openai.AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            max_retries=0,
            timeout=timeout_s,
            http_client=http_client,
        )
        # The SDK's typed overloads cannot take parameters built as a dict, so the call is untyped
        # and its result is cast back to the SDK's own model.
        self._sdk = sdk
        self._create: Callable[..., Awaitable[Any]] = sdk.chat.completions.create

    async def aclose(self) -> None:
        if not self._sdk.is_closed():
            await self._sdk.close()

    @property
    def profile(self) -> ModelProfile:
        return self._profile

    def params(self, messages: Sequence[Message], tools: Sequence[ToolSchema]) -> dict[str, object]:
        params: dict[str, object] = {
            "model": self._profile.model,
            "messages": list(messages),
            "max_completion_tokens": self._max_output_tokens,
        }
        if tools:
            params["tools"] = list(tools)
            params["tool_choice"] = "auto"
        if self._profile.reasoning is not None:
            params["reasoning_effort"] = self._profile.reasoning
        if self._affinity is not None:
            params["extra_headers"] = {"x-session-affinity": self._affinity}
        return params

    async def complete(
        self, messages: Sequence[Message], tools: Sequence[ToolSchema]
    ) -> ModelReply:
        params = self.params(messages, tools)
        attempt = 0
        while True:
            try:
                completion = cast(ChatCompletion, await self._create(**params))
            except openai.APIError as error:
                if isinstance(error, openai.APIConnectionError):
                    await self._unanswered()
                attempt += 1
                if not _retryable(error) or attempt >= self._retry.max_attempts:
                    raise ModelAPIError(_describe(error, attempt)) from error
                delay = self._retry.delay(attempt - 1, self._jitter(), _retry_after(error))
                scope = CALL_SCOPE.get()
                if scope is not None and scope.deadline is not None:
                    remaining = scope.deadline - asyncio.get_running_loop().time()
                    if delay > remaining:
                        raise ModelAPIError(
                            f"model API retry wait of {delay:g} s exceeds the task deadline"
                        ) from error
                await self._sleep(delay)
                continue
            except asyncio.CancelledError:
                # A sent request may have been billed even when Stop or the task deadline wins.
                await self._unanswered()
                raise
            if not completion.choices:
                scope = CALL_SCOPE.get()
                if scope is not None:
                    await scope.record(ModelReply(None, [], usage_of(completion), completion.model))
            return reply_of(completion)

    async def _unanswered(self) -> None:
        scope = CALL_SCOPE.get()
        if scope is not None:
            await scope.record(ModelReply(None, [], model=self._profile.model))


def _describe(error: openai.APIError, attempts: int) -> str:
    status = error.status_code if isinstance(error, openai.APIStatusError) else None
    kind = f"HTTP {status}" if status is not None else type(error).__name__
    tries = f" after {attempts} attempts" if attempts > 1 else ""
    return f"model API error{tries}: {kind}"
