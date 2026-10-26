"""The model-client interface, the provider profiles, and the OpenAI-SDK client.

The tool loop sees only `ModelClient`: a list of Chat Completions messages and tool schemas in, one
`ModelReply` out. The SDK client points at the agent's own endpoint and model, sends the reasoning
setting on every call, and retries 429 and 5xx with exponential backoff and full jitter. Usage the
API does not report stays `None`, never 0.
"""

import asyncio
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

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
    model="muse-spark-1.3",
    # docs/api-probe.md: reasoning is on by default and `none` is rejected; low was accepted.
    reasoning="low",
)
MUSE_SPARK_OPEN = ModelProfile(
    id="muse-spark-open",
    label="Muse Spark Open",
    provider="baseten",
    model="meta-models/muse-spark-open-1026",
    # Not probed yet (docs/api-probe.md): no reasoning parameter is known for Baseten.
    reasoning=None,
)
FAKE_MODEL = ModelProfile(
    id="fake", label="Scripted fake model", provider="fake", model="fake-scripted", reasoning=None
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


class ModelClient(Protocol):
    @property
    def profile(self) -> ModelProfile: ...

    async def complete(
        self, messages: Sequence[Message], tools: Sequence[ToolSchema]
    ) -> ModelReply: ...


class ModelAPIError(Exception):
    """The model call failed for good: a refused request, or retries used up."""


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
            delay = max(delay, min(retry_after_s, self.cap_s))
        return delay


def _retryable(error: openai.APIError) -> bool:
    if isinstance(error, openai.APIStatusError):
        return error.status_code == 429 or error.status_code >= 500
    return False


def _retry_after(error: openai.APIError) -> float | None:
    if not isinstance(error, openai.APIStatusError):
        return None
    value = error.response.headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except ValueError:
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
        raise ModelAPIError("the API returned no choices")
    message = completion.choices[0].message
    calls = [
        ToolCall(id=call.id, name=call.function.name, arguments=call.function.arguments or "{}")
        for call in message.tool_calls or []
        if call.type == "function"
    ]
    return ModelReply(
        text=message.content or None,
        tool_calls=calls,
        usage=usage_of(completion),
        model=completion.model or None,
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
        sdk = openai.AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            max_retries=0,
            timeout=timeout_s,
            http_client=http_client,
        )
        # The SDK's typed overloads cannot take parameters built as a dict, so the call is untyped
        # and its result is cast back to the SDK's own model.
        self._create: Callable[..., Awaitable[Any]] = sdk.chat.completions.create

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
                attempt += 1
                if not _retryable(error) or attempt >= self._retry.max_attempts:
                    raise ModelAPIError(_describe(error, attempt)) from error
                await self._sleep(
                    self._retry.delay(attempt - 1, self._jitter(), _retry_after(error))
                )
                continue
            return reply_of(completion)


def _describe(error: openai.APIError, attempts: int) -> str:
    status = error.status_code if isinstance(error, openai.APIStatusError) else None
    kind = f"HTTP {status}" if status is not None else type(error).__name__
    tries = f" after {attempts} attempts" if attempts > 1 else ""
    return f"model API error{tries}: {kind}"
