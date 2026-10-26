"""The API probe (docs/proposal.md, front 6).

    uv run --frozen python tools/probe_api.py meta|baseten [--model ID] [--report PATH]
                                                          [--details PATH]

Calls a provider's Chat Completions endpoint through the official OpenAI SDK, at most MAX_CALLS
times per run, and writes what it found to docs/api-probe.md. It spends credits, so make check
never runs it: the tests run the same code over a fake transport.

The key comes from oversight.settings and goes only into the SDK client. It is never printed,
logged or written: every string that leaves the process (the terminal, the report, the details
file) goes through one Redactor, which removes the key, any 8-character fragment of it and bearer
or key-like tokens. An error body is reduced to a code, a type and a short message, all redacted
(nothing at all for 401 and 403), and only the rate-limit headers are kept, redacted too.
A 429 or 5xx is retried up to MAX_RETRIES times with exponential backoff and jitter, honoring
Retry-After; every attempt counts against MAX_CALLS.
Exit codes: 0 the report was written, 1 the run was aborted, 2 the provider's key is not set.
"""

import argparse
import json
import random
import re
import statistics
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field, fields, is_dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol, cast

import openai
from openai import OpenAI

from oversight.settings import Settings

MAX_CALLS = 8
MAX_RETRIES = 3  # per step, on 429 and 5xx; every retry is a call against MAX_CALLS
BACKOFF_BASE = 1.0  # seconds before the first retry; it doubles each time, with jitter
BACKOFF_MAX = 30.0
FRAGMENT = 8  # a run of this many characters of the key is as good as the key
ART = timezone(timedelta(hours=-3))
DEFAULT_REPORT = Path(__file__).resolve().parents[1] / "docs" / "api-probe.md"
ORDER = ("meta", "baseten")
Obj = dict[str, Any]
Redact = Callable[[str], str]


@dataclass(frozen=True)
class Provider:
    name: str
    title: str
    base_url: str
    model: str
    key_name: str  # the name of the variable in .env, never its value
    sources: tuple[str, ...]


PROVIDERS = {
    "meta": Provider(
        name="meta",
        title="Meta Model API: Muse Spark, Standard mode",
        base_url="https://api.meta.ai/v1",
        model="muse-spark-1.3",
        key_name="META_API_KEY",
        sources=(
            "https://dev.meta.ai/docs/ (base URL, bearer auth, model ids; "
            "ai.developer.meta.com/docs redirects here)",
            "https://dev.meta.ai/docs/models.md (muse-spark-1.3 is the latest Standard-mode id)",
            "https://dev.meta.ai/docs/reasoning (reasoning_effort)",
            "https://dev.meta.ai/docs/protocols/chat-completions (parameters, usage)",
            "https://dev.meta.ai/docs/tool-calling (tools, tool_choice, parallel calls)",
            "https://dev.meta.ai/docs/pricing-rate-limits (rate limits and headers)",
        ),
    ),
    "baseten": Provider(
        name="baseten",
        title="Baseten: Muse Spark Open",
        base_url="https://inference.baseten.co/v1",
        model="meta-models/muse-spark-open-1026",
        key_name="BASETEN_API_KEY",
        sources=(
            "https://www.baseten.co/resources/changelog/"
            "muse-spark-open-and-nemotron-35-super-vl-available-on-baseten/ "
            "(base URL; its example slug is meta-models/muse-spark-open, without -1026)",
            "https://docs.baseten.co/reference/inference-api/chat-completions.md (parameters)",
        ),
    ),
}

# Facts taken from Meta's documentation and not probed, so that front 12 has them in one place.
DOCUMENTED_NOT_PROBED = (
    "`tool_choice` accepts only `auto`; `none`, `required` and a named function return HTTP 400.",
    "`stop` is not supported on reasoning models and returns HTTP 400; `top_p: 0` returns 400.",
    "A function name must match `^[a-zA-Z0-9_.-]+$` with at most one dot.",
    "Reasoning tokens count toward `max_completion_tokens` and are billed as output.",
    "`reasoning_content` is redacted to empty for external keys, so there is nothing to send back.",
    "Rate limit for muse-spark-1.3: 3,000 requests and 4,000,000 tokens per minute, per team.",
    "A 429 should be retried with exponential backoff and jitter, honoring `Retry-After`.",
)

TOOLS: list[Obj] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file. Returns numbered lines. Read long files by line range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the repository."},
                    "start_line": {"type": "integer", "description": "First line, from 1."},
                    "end_line": {"type": "integer", "description": "Last line, included."},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace old_text with new_text. old_text must appear exactly once.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the repository."},
                    "old_text": {"type": "string", "description": "The exact text to replace."},
                    "new_text": {"type": "string", "description": "The replacement."},
                },
                "required": ["path", "old_text", "new_text"],
                "additionalProperties": False,
            },
        },
    },
]

CHAT_PROMPT = "What is 1847 * 293? Answer with the number only."
EDIT_TASK = (
    "Change the default discount rate in src/pricing.py to 0.0850. "
    "Read the file first, then edit it."
)
READ_TWO_TASK = "Read src/a.py and src/b.py."
LONG_TASK = "Write a story of about 300 words about a lighthouse keeper."
OLD_VALUE = "0.0725"  # in the file only, so an edit that names it was built on the tool result
FILE_SOURCE = (
    f"DEFAULT_RATE = {OLD_VALUE}\n\n\n"
    "def discount(total: float, rate: float = DEFAULT_RATE) -> float:\n"
    "    return total * (1 - rate)\n"
)
FILE_NUMBERED = "".join(f"{n}\t{line}\n" for n, line in enumerate(FILE_SOURCE.split("\n")[:-1], 1))
JSON_SYSTEM = (
    "You have two tools. read_file(path, start_line?, end_line?) returns numbered lines. "
    "edit_file(path, old_text, new_text) replaces old_text, which must appear exactly once. "
    "To use a tool, reply with only a JSON object of the form "
    '{"tool": "<name>", "arguments": {...}} and nothing else. '
    "The result arrives in the next message."
)


@dataclass(frozen=True)
class Reply:
    status: int  # 0: no HTTP answer (a network error or a timeout)
    body: Obj
    seconds: float
    headers: dict[str, str] = field(default_factory=dict[str, str])  # rate-limit headers only


class Transport(Protocol):
    def complete(self, params: Obj) -> Reply: ...

    def stream(self, params: Obj) -> Reply: ...


# Reading JSON ------------------------------------------------------------------------------------


def as_obj(value: object) -> Obj:
    return cast(Obj, value) if isinstance(value, dict) else {}


def as_list(value: object) -> list[Any]:
    return cast(list[Any], value) if isinstance(value, list) else []


def choice_of(body: Obj) -> Obj:
    choices = as_list(body.get("choices"))
    return as_obj(choices[0]) if choices else {}


def message_of(body: Obj) -> Obj:
    return as_obj(choice_of(body).get("message"))


def finish_of(body: Obj) -> str | None:
    value = choice_of(body).get("finish_reason")
    return value if isinstance(value, str) else None


def tool_calls_of(body: Obj) -> list[Obj]:
    return [as_obj(c) for c in as_list(message_of(body).get("tool_calls"))]


def name_of(call: Obj) -> str | None:
    value = as_obj(call.get("function")).get("name")
    return value if isinstance(value, str) else None


def arguments_of(call: Obj) -> Obj | None:
    """The call's arguments as an object, or None if they are not a JSON object."""
    arguments = as_obj(call.get("function")).get("arguments")
    if not isinstance(arguments, str):
        return None
    try:
        parsed: object = json.loads(arguments)
    except ValueError:
        return None
    return cast(Obj, parsed) if isinstance(parsed, dict) else None


def usage_of(body: Obj) -> Obj | None:
    return as_obj(body.get("usage")) or None


def flatten(value: object, prefix: str = "") -> list[str]:
    """Name=value for every leaf of a usage object: the field names are the evidence."""
    if isinstance(value, dict):
        return [i for k, v in cast(Obj, value).items() for i in flatten(v, f"{prefix}{k}.")]
    return [f"{prefix[:-1]}={value}"]


def count(usage: Obj | None, *path: str) -> int | None:
    """An integer at a path in usage, or None if absent: unknown is never recorded as zero."""
    node: object = usage
    for name in path:
        node = as_obj(node).get(name)
    return node if isinstance(node, int) else None


def reasoning_tokens(body: Obj) -> int | None:
    return count(usage_of(body), "completion_tokens_details", "reasoning_tokens")


def cached_tokens(usage: Obj | None) -> tuple[str, int | None]:
    """Where cached input tokens are reported, as a path, and how many."""
    for path in (
        ("prompt_tokens_details", "cached_tokens"),
        ("input_tokens_details", "cached_tokens"),
        ("cached_tokens",),
    ):
        found = count(usage, *path)
        if found is not None:
            return ".".join(path), found
    return "not reported", None


def took(reply: Reply) -> str:
    return f"HTTP {reply.status}, {reply.seconds:.2f} s"


def error_text(reply: Reply) -> str:
    err = as_obj(reply.body.get("error"))
    return ", ".join(f"{k}={v}" for k, v in err.items()) if err else "no error body"


def parse_json_tool_call(text: str) -> tuple[str, Obj] | None:
    """The first JSON object in a reply, as (tool, arguments); code fences and prose are skipped."""
    start = text.find("{")
    while start != -1:
        try:
            parsed, _ = json.JSONDecoder().raw_decode(text[start:])
        except ValueError:
            parsed = None
        found = as_obj(parsed)
        if isinstance(found.get("tool"), str):
            return found["tool"], as_obj(found.get("arguments"))
        start = text.find("{", start + 1)
    return None


# The redactor ------------------------------------------------------------------------------------

REDACTED = "[redacted]"
# What looks like a credential whoever it belongs to: a bearer token, an sk-style key, or a long
# run of letters and digits mixed (hyphens and underscores split words, so slugs and field names
# are left alone).
KEY_LIKE = re.compile(
    r"Bearer\s+[A-Za-z0-9._~+/=-]{6,}"
    r"|\b(?:sk|pk|rk)[-_][A-Za-z0-9_-]{8,}"
    r"|\b(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{24,}\b",
    re.IGNORECASE,
)


class Redactor:
    """The one sanitizer: nothing leaves the process without passing through it.

    It removes the key, every run of FRAGMENT or more characters that appears in the key (so a
    reflected piece of it goes too, not only the whole), and anything shaped like a credential.
    Calling it on its own output changes nothing.
    """

    def __init__(self, key: str | None) -> None:
        text = key or ""
        self._width = min(FRAGMENT, len(text))
        self._windows: set[str] = {
            text[i : i + self._width] for i in range(len(text) - self._width + 1) if text
        }

    def __call__(self, text: str) -> str:
        width = self._width
        spans: list[list[int]] = []
        for start in range(len(text) - width + 1 if width else 0):
            if text[start : start + width] in self._windows:
                if spans and start <= spans[-1][1]:
                    spans[-1][1] = start + width  # overlapping or touching: one redaction
                else:
                    spans.append([start, start + width])
        pieces: list[str] = []
        done = 0
        for begin, end in spans:
            pieces += [text[done:begin], REDACTED]
            done = end
        pieces.append(text[done:])
        return KEY_LIKE.sub(REDACTED, "".join(pieces))


def scrub(value: object, redact: Redact) -> Any:
    """A copy of a value with every string in it redacted: dicts, lists and dataclasses included."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {redact(str(k)): scrub(v, redact) for k, v in cast(Obj, value).items()}
    if isinstance(value, list):
        return [scrub(v, redact) for v in cast(list[Any], value)]
    if is_dataclass(value) and not isinstance(value, type):
        return replace(
            value, **{f.name: scrub(getattr(value, f.name), redact) for f in fields(value)}
        )
    return value


# The transport -----------------------------------------------------------------------------------


def rate_headers(headers: Any, redact: Redact) -> dict[str, str]:
    """Keep only the rate-limit headers and Retry-After, redacted: a server can reflect a key."""
    return {
        redact(str(name).lower()): redact(str(value))
        for name, value in headers.items()
        if str(name).lower().startswith("x-ratelimit-") or str(name).lower() == "retry-after"
    }


def error_body(status: int, body: object, message: str, redact: Redact) -> Obj:
    """Reduce an error to a code, a type and a short message, every one redacted.

    The SDK's own message repeats the whole body, so the body's message is used when it has one.
    """
    inner = as_obj(as_obj(body).get("error", body))
    found: Obj = {
        k: redact(inner[k])[:200] for k in ("code", "type") if isinstance(inner.get(k), str)
    }
    text = inner.get("message")
    if status not in (401, 403):
        found["message"] = redact(text if isinstance(text, str) else message)[:200]
    return {"error": found}


def summarize_stream(chunks: Iterable[tuple[float, Obj]]) -> Obj:
    """Fold timed stream chunks into one summary: text, usage, finish reason, first chunk."""
    summary: Obj = {"chunks": 0, "first_chunk_seconds": None, "text": "", "usage": None}
    for seconds, chunk in chunks:
        summary["chunks"] += 1
        if summary["first_chunk_seconds"] is None:
            summary["first_chunk_seconds"] = seconds
        summary["model"] = chunk.get("model", summary.get("model"))
        if isinstance(chunk.get("usage"), dict):
            summary["usage"] = chunk["usage"]
        for choice in as_list(chunk.get("choices")):
            choice = as_obj(choice)
            summary["text"] += as_obj(choice.get("delta")).get("content") or ""
            summary["finish_reason"] = choice.get("finish_reason") or summary.get("finish_reason")
    return summary


class SdkTransport:
    """The OpenAI SDK pointed at a provider. No SDK retries: the Session retries and counts."""

    def __init__(
        self, base_url: str, api_key: str, http_client: openai.DefaultHttpxClient | None = None
    ) -> None:
        self._redact = Redactor(api_key)
        client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            max_retries=0,
            timeout=120.0,
            http_client=http_client,
        )
        # The SDK's typed overloads cannot take a dict of parameters, so the call is untyped.
        self._create: Callable[..., Any] = client.chat.completions.with_raw_response.create

    def complete(self, params: Obj) -> Reply:
        start = time.monotonic()
        try:
            raw = self._create(**params)
            body = raw.parse().model_dump(mode="json", exclude_unset=True)
            seconds = time.monotonic() - start
            return Reply(raw.status_code, body, seconds, rate_headers(raw.headers, self._redact))
        except openai.APIError as err:
            return self._failure(err, time.monotonic() - start)

    def stream(self, params: Obj) -> Reply:
        start = time.monotonic()
        try:
            raw = self._create(**params, stream=True, stream_options={"include_usage": True})
            chunks = (
                (time.monotonic() - start, c.model_dump(mode="json", exclude_unset=True))
                for c in raw.parse()
            )
            summary = summarize_stream(chunks)
            seconds = time.monotonic() - start
            return Reply(raw.status_code, summary, seconds, rate_headers(raw.headers, self._redact))
        except openai.APIError as err:
            return self._failure(err, time.monotonic() - start)

    def _failure(self, err: openai.APIError, seconds: float) -> Reply:
        if isinstance(err, openai.APIStatusError):
            body = error_body(err.status_code, err.body, err.message, self._redact)
            headers = rate_headers(err.response.headers, self._redact)
            return Reply(err.status_code, body, seconds, headers)
        return Reply(0, {"error": {"type": self._redact(type(err).__name__)}}, seconds)


class BudgetExhausted(Exception):
    """The run has made MAX_CALLS calls."""


def retryable(status: int) -> bool:
    return status == 429 or 500 <= status < 600


def backoff(retry: int, reply: Reply, rand: Callable[[], float]) -> float:
    """Seconds before retry number `retry` (from 0): doubling, jittered, at least Retry-After."""
    delay = BACKOFF_BASE * 2**retry * (0.5 + rand())
    try:
        asked = float(reply.headers.get("retry-after", ""))
    except ValueError:
        asked = 0.0  # an HTTP date or nonsense: the backoff alone
    return min(max(delay, asked), BACKOFF_MAX)


class Session:
    """Counts every call, failed ones and retries included, and refuses the call after the last.

    A 429 or 5xx is retried up to `retries` times, waiting `backoff` between attempts. A retry that
    would pass the cap is not made: the last reply is returned as it came. The sleep and the random
    source are injected, so tests wait for nothing.
    """

    def __init__(
        self,
        transport: Transport,
        limit: int = MAX_CALLS,
        retries: int = MAX_RETRIES,
        sleep: Callable[[float], None] = time.sleep,
        rand: Callable[[], float] = random.random,
    ) -> None:
        self._transport = transport
        self._limit = limit
        self._retries = retries
        self._sleep = sleep
        self._rand = rand
        self.calls: list[Obj] = []
        self.replies: dict[str, Reply] = {}

    def send(self, step: str, params: Obj, *, stream: bool = False) -> Reply:
        if len(self.calls) >= self._limit:
            raise BudgetExhausted(step)
        send = self._transport.stream if stream else self._transport.complete
        retry = 0
        while True:
            reply = send(params)
            name = step if retry == 0 else f"{step} (retry {retry})"
            self.calls.append(
                {"step": name, "status": reply.status, "seconds": round(reply.seconds, 2)}
            )
            if (
                not retryable(reply.status)
                or retry >= self._retries
                or len(self.calls) >= self._limit
            ):
                break
            self._sleep(backoff(retry, reply, self._rand))
            retry += 1
        self.replies[step] = reply
        return reply


@dataclass
class Check:
    key: str
    question: str
    answer: str
    evidence: str
    passed: bool | None = None  # None: informative, not run, or not decisive
    data: Obj = field(default_factory=dict[str, Any])  # facts the verdict reads; not rendered


@dataclass
class Result:
    provider: str
    title: str
    base_url: str
    model_requested: str
    model_reported: str | None
    date: str
    checks: list[Check]
    calls: list[Obj]
    verdict: str


# The steps ---------------------------------------------------------------------------------------


def rejected(key: str, question: str, reply: Reply, passed: bool | None = None) -> Check:
    return Check(
        key,
        question,
        f"rejected, HTTP {reply.status}",
        f"{took(reply)}; {error_text(reply)}",
        passed,
    )


def assistant_turn(body: Obj) -> Obj:
    """The assistant message as returned, resent whole before its tool results (Meta: else 400)."""
    calls = [
        {"id": c.get("id"), "type": c.get("type", "function"), "function": c.get("function")}
        for c in tool_calls_of(body)
    ]
    return {"role": "assistant", "content": message_of(body).get("content"), "tool_calls": calls}


def check_chat(session: Session, model: str) -> Check:
    question = "Plain chat call, and the model id the API reports"
    params = {"model": model, "messages": [{"role": "user", "content": CHAT_PROMPT}]}
    reply = session.send("chat", params)
    if reply.status != 200:
        return rejected("chat", question, reply, False)
    text = message_of(reply.body).get("content")
    ok = isinstance(text, str) and text.strip() != ""
    return Check(
        "chat",
        question,
        f"works; the API reports `{reply.body.get('model')}`" if ok else "empty reply",
        f"{took(reply)}; finish_reason={finish_of(reply.body)}; reply {str(text).strip()[:40]!r}",
        ok,
    )


def check_tools_call(session: Session, model: str) -> tuple[Check, list[Obj] | None]:
    """The first tool turn. Returns the conversation so far if it ended in a read_file call.

    The format check passes for any known tool with arguments that parse as a JSON object.
    """
    question = "Native tool call (read_file and edit_file as JSON Schema tools)"
    messages: list[Obj] = [{"role": "user", "content": EDIT_TASK}]
    reply = session.send("tools_call", {"model": model, "messages": messages, "tools": TOOLS})
    if reply.status != 200:
        return rejected("tools_call", question, reply, False), None
    calls = tool_calls_of(reply.body)
    first = calls[0] if calls else {}
    args = arguments_of(first) if calls else None
    name = name_of(first)
    ok = name in ("read_file", "edit_file") and args is not None
    evidence = (
        f"{took(reply)}; finish_reason={finish_of(reply.body)}; tool_calls={len(calls)}; "
        f"type={first.get('type')}; name={name}; arguments={sorted(args) if args else args}; "
        f"id {'present' if first.get('id') else 'absent'}; "
        f"content={message_of(reply.body).get('content')!r}"
    )
    answer = "returned in the standard format" if ok else "no usable tool call in the reply"
    check = Check("tools_call", question, answer, evidence, ok)
    check.data = {"text_with_call": bool(message_of(reply.body).get("content"))}
    return check, [*messages, assistant_turn(reply.body)] if ok and name == "read_file" else None


def check_tools_result(session: Session, model: str, history: list[Obj]) -> Check:
    question = "A tool result sent back: the next turn uses it"
    call = as_list(history[-1].get("tool_calls"))[0]
    result = {"role": "tool", "tool_call_id": as_obj(call).get("id"), "content": FILE_NUMBERED}
    params = {"model": model, "messages": [*history, result], "tools": TOOLS}
    reply = session.send("tools_result", params)
    if reply.status != 200:
        return rejected("tools_result", question, reply, False)
    calls = tool_calls_of(reply.body)
    args = arguments_of(calls[0]) if calls else None
    name = name_of(calls[0]) if calls else None
    old = str((args or {}).get("old_text", ""))
    new = str((args or {}).get("new_text", ""))
    used = name == "edit_file" and OLD_VALUE in old
    once = bool(old) and FILE_SOURCE.count(old) == 1
    evidence = (
        f"{took(reply)}; finish_reason={finish_of(reply.body)}; name={name}; "
        f"old_text holds the value only the result had: {'yes' if used else 'no'}; "
        f"old_text appears exactly once in the file: {'yes' if once else 'no'}; "
        f"new_text holds 0.085: {'yes' if '0.085' in new else 'no'}"
    )
    answer = "yes, an edit_file call built on the result" if used else "no edit built on the result"
    return Check("tools_result", question, answer, evidence, used)


def check_parallel(session: Session, model: str) -> Check:
    question = "Parallel tool calls in one turn"
    messages = [{"role": "user", "content": READ_TWO_TASK}]
    reply = session.send("parallel", {"model": model, "messages": messages, "tools": TOOLS})
    if reply.status != 200:
        return rejected("parallel", question, reply)
    names = [name_of(c) for c in tool_calls_of(reply.body)]
    answer = f"yes, {len(names)} calls in one turn" if len(names) > 1 else f"no, {len(names)} call"
    return Check("parallel", question, answer, f"{took(reply)}; tool_calls={names}")


def check_reasoning_default(session: Session) -> Check:
    question = "Reasoning with no parameter sent (the default)"
    reply = session.replies.get("chat")
    if reply is None or reply.status != 200:
        return Check("reasoning_default", question, "not run", "the plain chat call failed")
    tokens = reasoning_tokens(reply.body)
    content = message_of(reply.body).get("reasoning_content", "absent")
    if tokens is None:
        answer = "unknown: usage has no reasoning_tokens field"
    elif tokens > 0:
        answer = f"on by default ({tokens} reasoning tokens with no parameter)"
    else:
        answer = "no reasoning tokens on this prompt with no parameter"
    evidence = f"completion_tokens_details.reasoning_tokens={tokens}; reasoning_content={content!r}"
    return Check("reasoning_default", question, answer, evidence)


def check_reasoning_set(session: Session, model: str, effort: str) -> Check:
    step = f"reasoning_{effort}"
    question = f"`reasoning_effort: {effort}` (top-level parameter, Chat Completions)"
    params = {
        "model": model,
        "messages": [{"role": "user", "content": CHAT_PROMPT}],
        "reasoning_effort": effort,
    }
    reply = session.send(step, params)
    if reply.status != 200:
        failed = rejected(step, question, reply)
        message = str(as_obj(reply.body.get("error")).get("message", ""))
        listed = re.search(r"Supported values: \[([^\]]*)\]", message)
        failed.data = {"supported": listed.group(1) if listed else None}
        return failed
    default = session.replies.get("chat")
    tokens = reasoning_tokens(reply.body)
    baseline = reasoning_tokens(default.body) if default else None
    check = Check(
        step,
        question,
        "accepted",
        f"{took(reply)}; reasoning_tokens={tokens} (default call: {baseline}); "
        f"completion_tokens={count(usage_of(reply.body), 'completion_tokens')}",
    )
    check.data = {"reasoning_tokens": tokens, "default_reasoning_tokens": baseline}
    return check


def check_limits(session: Session, model: str) -> Check:
    question = "`temperature` and `max_completion_tokens`: rejected, ignored or honored"
    params = {
        "model": model,
        "messages": [{"role": "user", "content": LONG_TASK}],
        "temperature": 0.2,
        "max_completion_tokens": 32,
    }
    reply = session.send("limits", params)
    if reply.status != 200:
        return rejected("limits", question, reply)
    used = count(usage_of(reply.body), "completion_tokens")
    honored = used is not None and used <= 32 and finish_of(reply.body) == "length"
    answer = (
        "both accepted; the token limit is honored (finish_reason length)"
        if honored
        else "both accepted; the token limit did not stop the reply"
    )
    spent = reasoning_tokens(reply.body)
    length = len(str(message_of(reply.body).get("content") or ""))
    if honored and length == 0 and spent:
        answer += "; reasoning used the budget and left the reply empty"
    evidence = (
        f"{took(reply)}; finish_reason={finish_of(reply.body)}; completion_tokens={used}, of which "
        f"reasoning_tokens={spent}; reply length {length} characters; "
        "temperature 0.2 has no effect one call can show"
    )
    return Check("limits", question, answer, evidence, honored)


def check_stream(session: Session, model: str) -> Check:
    question = "Streaming with `stream_options: {include_usage: true}`"
    params = {"model": model, "messages": [{"role": "user", "content": CHAT_PROMPT}]}
    reply = session.send("stream", params, stream=True)
    if reply.status != 200:
        return rejected("stream", question, reply)
    usage = usage_of(reply.body)
    first = reply.body.get("first_chunk_seconds")
    first_text = f"{first:.2f} s" if isinstance(first, float) else "unknown"
    return Check(
        "stream",
        question,
        "works; usage arrives in the last chunk" if usage else "works; no usage in any chunk",
        f"{took(reply)}; chunks={reply.body.get('chunks')}; first chunk after {first_text}; "
        f"usage: {', '.join(flatten(usage)) if usage else 'absent'}",
    )


def check_json_fallback(session: Session, model: str) -> Check:
    question = "JSON-in-text fallback (the call as a JSON object in the reply)"
    messages: list[Obj] = [
        {"role": "system", "content": JSON_SYSTEM},
        {"role": "user", "content": EDIT_TASK},
    ]
    first = session.send("json_call", {"model": model, "messages": messages})
    if first.status != 200:
        return rejected("json_fallback", question, first, False)
    text = str(message_of(first.body).get("content") or "")
    call = parse_json_tool_call(text)
    if call is None or call[0] != "read_file":
        return Check("json_fallback", question, "no parsable read_file call", took(first), False)
    follow = [
        *messages,
        {"role": "assistant", "content": text},
        {"role": "user", "content": f"Result of read_file:\n{FILE_NUMBERED}"},
    ]
    second = session.send("json_result", {"model": model, "messages": follow})
    if second.status != 200:
        return rejected("json_fallback", question, second, False)
    edit = parse_json_tool_call(str(message_of(second.body).get("content") or ""))
    used = edit is not None and edit[0] == "edit_file" and OLD_VALUE in str(edit[1].get("old_text"))
    answer = (
        "works: a parsable call, then an edit built on the result" if used else "second turn failed"
    )
    return Check("json_fallback", question, answer, f"{took(first)}; {took(second)}", used)


def usage_line(body: Obj) -> str:
    """Input, output, cached and reasoning tokens of one response; ? where not reported."""
    usage = usage_of(body)
    cached = cached_tokens(usage)[1]
    parts = (
        count(usage, "prompt_tokens"),
        count(usage, "completion_tokens"),
        cached,
        reasoning_tokens(body),
    )
    return "/".join("?" if n is None else str(n) for n in parts)


def check_usage(session: Session) -> Check:
    question = "Token usage on every response, and as what"
    replies = {s: r for s, r in session.replies.items() if r.status == 200 and s != "stream"}
    if not replies:
        return Check("usage", question, "not run", "no successful response")
    usages = {step: usage_of(r.body) for step, r in replies.items()}
    missing = [step for step, usage in usages.items() if usage is None]
    sample = next((u for u in usages.values() if u), None)
    cached = {step: cached_tokens(usage) for step, usage in usages.items()}
    where = next((path for path, n in cached.values() if n is not None), "not reported")
    hits = [n for _, n in cached.values() if n]
    answer = (
        f"yes, on all {len(replies)} responses"
        if not missing
        else f"missing on: {', '.join(missing)}. Record unknown, never zero"
    )
    answer += f"; cached input tokens non-zero on {len(hits)} of {len(replies)}"
    per_call = "; ".join(f"{step}: {usage_line(r.body)}" for step, r in replies.items())
    evidence = (
        f"fields: {', '.join(flatten(sample)) if sample else 'none'}; "
        f"cached input tokens at {where}; per call, in/out/cached/reasoning: {per_call}"
    )
    return Check("usage", question, answer, evidence)


def check_latency(session: Session) -> Check:
    times = [c["seconds"] for c in session.calls if c["status"] == 200]
    per_call = ", ".join(f"{c['step']} {c['seconds']:.2f} s" for c in session.calls)
    answer = (
        f"median {statistics.median(times):.2f} s, from {min(times):.2f} to {max(times):.2f} s "
        f"over {len(times)} calls"
        if times
        else "no successful call"
    )
    return Check("latency", "Latency per call, non-streaming and streaming", answer, per_call)


def check_rate_headers(session: Session) -> Check:
    seen: dict[str, str] = {}
    for reply in session.replies.values():
        seen |= reply.headers
    answer = ", ".join(f"{k}={v}" for k, v in sorted(seen.items())) or "none seen"
    return Check(
        "rate_headers", "Rate-limit headers on responses", answer, "x-ratelimit-* and retry-after"
    )


# The run -----------------------------------------------------------------------------------------


class Aborted(Exception):
    """The first call failed, so nothing else would work; the run stops before spending more."""


def build_verdict(provider: Provider, checks: dict[str, Check]) -> str:
    def passed(key: str) -> bool:
        return key in checks and checks[key].passed is True

    parallel = checks.get("parallel")
    parallel_text = f" Parallel calls: {parallel.answer}." if parallel else ""
    if passed("tools_call") and passed("tools_result"):
        head = (
            "Native tools. Front 12 sends `tools` and reads `tool_calls` in Chat Completions, "
            f"then answers each call with one `tool` message.{parallel_text}"
        )
        if checks["tools_call"].data.get("text_with_call"):
            head += " The reply can carry text next to a tool call, so `content` is not the end."
    elif passed("json_fallback"):
        head = "JSON fallback. Native tool calling failed here and the JSON-in-text format worked."
    elif "json_fallback" in checks:
        head = "Neither route passed: native tool calling failed and so did the JSON fallback."
    else:
        head = "Not decided: the native tool checks did not both pass and no fallback ran."
    default, low, none = (
        checks.get(k) for k in ("reasoning_default", "reasoning_low", "reasoning_none")
    )
    if default and low and none:
        head += (
            f" Reasoning: with no parameter, {default.answer}; `reasoning_effort` low was "
            f"{low.answer}; none was {none.answer}."
        )
        supported = none.data.get("supported")
        if supported:
            head += f" The API lists these values as supported: {supported}."
        now, before = low.data.get("reasoning_tokens"), low.data.get("default_reasoning_tokens")
        if isinstance(now, int) and isinstance(before, int):
            head += (
                f" One sample each, on a trivial prompt: low gave {now} reasoning tokens and the "
                f"default {before}, so what each setting costs is still unmeasured."
            )
    return head


def run_probe(
    transport: Transport,
    provider: Provider,
    model: str,
    now: datetime | None = None,
    sleep: Callable[[float], None] = time.sleep,
    rand: Callable[[], float] = random.random,
) -> Result:
    """Run the checks in priority order. The session refuses the call after MAX_CALLS."""
    session = Session(transport, sleep=sleep, rand=rand)
    checks: dict[str, Check] = {}
    history: list[Obj] | None = None

    def run(key: str, step: Callable[[], Check]) -> None:
        try:
            checks[key] = step()
        except BudgetExhausted:
            checks[key] = Check(key, key, "skipped", f"the run's {MAX_CALLS}-call budget was spent")

    def first_turn() -> Check:
        nonlocal history
        check, history = check_tools_call(session, model)
        return check

    chat = check_chat(session, model)
    if session.replies["chat"].status != 200:
        raise Aborted(f"the first call failed: {chat.evidence}")
    checks["chat"] = chat
    run("tools_call", first_turn)
    if history is not None:
        conversation = history
        run("tools_result", lambda: check_tools_result(session, model, conversation))
        if checks["tools_result"].passed is False:  # the route is not decided until JSON is tried
            run("json_fallback", lambda: check_json_fallback(session, model))
        run("parallel", lambda: check_parallel(session, model))
    elif checks["tools_call"].passed is False:
        run("json_fallback", lambda: check_json_fallback(session, model))
    else:
        checks["tools_result"] = Check(
            "tools_result", "A tool result sent back", "not run", "the first turn did not read"
        )
    run("reasoning_default", lambda: check_reasoning_default(session))
    run("reasoning_low", lambda: check_reasoning_set(session, model, "low"))
    run("reasoning_none", lambda: check_reasoning_set(session, model, "none"))
    run("limits", lambda: check_limits(session, model))
    run("stream", lambda: check_stream(session, model))
    run("usage", lambda: check_usage(session))
    run("latency", lambda: check_latency(session))
    run("rate_headers", lambda: check_rate_headers(session))
    return Result(
        provider=provider.name,
        title=provider.title,
        base_url=provider.base_url,
        model_requested=model,
        model_reported=session.replies["chat"].body.get("model"),
        date=(now or datetime.now(ART)).strftime("%Y-%m-%d %H:%M ART"),
        checks=list(checks.values()),
        calls=session.calls,
        verdict=build_verdict(provider, checks),
    )


# The report --------------------------------------------------------------------------------------

HEADER = """# API probe

Generated by `tools/probe_api.py`, which calls each provider's Chat Completions endpoint through the
official OpenAI SDK, at most {max_calls} times per run. Do not edit a section by hand: run
`uv run --frozen python tools/probe_api.py meta` (or `baseten`) again. No key, request header or
absolute path is written here.
"""
SECTION = re.compile(r"<!-- probe:(\w+) -->\n(.*?)\n<!-- /probe:\1 -->", re.DOTALL)


def cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def bullets(items: Iterable[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def render_section(result: Result) -> str:
    provider = PROVIDERS[result.provider]
    rows = "\n".join(
        f"| {cell(c.question)} | {cell(c.answer)} | {cell(c.evidence)} |" for c in result.checks
    )
    documented = ""
    if result.provider == "meta":
        documented = f"\n\n### Documented, not probed\n\n{bullets(DOCUMENTED_NOT_PROBED)}"
    return (
        f"## {result.title}\n\n"
        f"- Date: {result.date}\n"
        f"- Endpoint: `{result.base_url}`\n"
        f"- Model requested: `{result.model_requested}`; reported by the API: "
        f"`{result.model_reported}`\n"
        f"- Calls made: {len(result.calls)} of at most {MAX_CALLS}\n\n"
        f"### Answers\n\n| Question | Answer | Evidence |\n| --- | --- | --- |\n{rows}\n\n"
        f"### Verdict\n\n{result.verdict}{documented}\n\n"
        f"### Where the endpoint and model names came from\n\n{bullets(provider.sources)}"
    )


def skipped_section(provider: Provider, reason: str) -> str:
    return (
        f"## {provider.title}\n\n"
        f"Skipped: {reason}\n\n"
        f"Still unknown for agent B: which slug is right (`{provider.model}` in AGENTS.md; the "
        "Baseten changelog's example is `meta-models/muse-spark-open`), whether native tool "
        "calling and parallel calls work, which parameter sets reasoning, what usage is "
        "reported, and the latency. Run `uv run --frozen python tools/probe_api.py baseten` "
        f"once `{provider.key_name}` is set.\n\n"
        f"### Where the endpoint and model names came from\n\n{bullets(provider.sources)}"
    )


def merge_report(existing: str | None, name: str, section: str, overwrite: bool = True) -> str:
    """Put one provider's section in the report, keeping the others; a missing one is 'skipped'."""
    sections = {m.group(1): m.group(2) for m in SECTION.finditer(existing or "")}
    if overwrite or name not in sections:
        sections[name] = section
    for other in ORDER:
        reason = f"{PROVIDERS[other].key_name} is not set."
        sections.setdefault(other, skipped_section(PROVIDERS[other], reason))
    body = "\n\n".join(f"<!-- probe:{n} -->\n{sections[n]}\n<!-- /probe:{n} -->" for n in ORDER)
    return HEADER.format(max_calls=MAX_CALLS) + "\n" + body + "\n"


def write_report(
    path: Path, name: str, section: str, key: str | None, overwrite: bool = True
) -> None:
    existing = path.read_text(encoding="utf-8") if path.is_file() else None
    text = Redactor(key)(merge_report(existing, name, section, overwrite))
    path.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe a provider's chat and tool-calling API.")
    parser.add_argument("provider", choices=ORDER)
    parser.add_argument("--model", help="override the provider's model id")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--details", type=Path, help="also write the result as JSON here")
    args = parser.parse_args(argv)
    provider = PROVIDERS[args.provider]
    settings = Settings.load()
    key = {"meta": settings.meta_api_key, "baseten": settings.baseten_api_key}[provider.name]
    redact = Redactor(key or None)

    def say(text: str) -> None:
        print(redact(text))

    if not key:
        reason = f"{provider.key_name} is not set."
        say(f"skipped: {reason}")
        write_report(args.report, provider.name, skipped_section(provider, reason), None, False)
        return 2
    try:
        result = run_probe(
            SdkTransport(provider.base_url, key), provider, args.model or provider.model
        )
    except Aborted as err:
        say(f"aborted: {err}")
        return 1
    result = scrub(result, redact)
    for check in result.checks:
        say(f"{check.key}: {check.answer}")
    write_report(args.report, provider.name, render_section(result), key)
    if args.details:
        args.details.write_text(json.dumps(asdict(result), indent=2) + "\n", encoding="utf-8")
    say(f"calls made: {len(result.calls)}; report written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
