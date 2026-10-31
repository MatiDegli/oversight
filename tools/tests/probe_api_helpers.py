"""Shared support moved verbatim from the split test modules."""

import json
from typing import Any

import httpx2  # the SDK's HTTP client library: its MockTransport stands in for the network
import openai

import probe_api as probe

KEY = "test-key"


# A longer fake key and a fragment of it, for the sanitizer: low entropy, so that nothing but the
# key logic (not the credential-shaped patterns) can be what removes them.
LONG_KEY = "test-key-aaaabbbbccccdddd"


FRAG = LONG_KEY[10:18]


META = probe.PROVIDERS["meta"]


Obj = dict[str, Any]


def completion(
    content: str | None = "541171", tool_calls: list[Obj] | None = None, **extra: Any
) -> Obj:
    message: Obj = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    usage = {
        "prompt_tokens": 20,
        "completion_tokens": 9,
        "total_tokens": 29,
        "prompt_tokens_details": {"cached_tokens": 0},
        "completion_tokens_details": {"reasoning_tokens": 5},
    }
    body: Obj = {
        "model": "muse-spark-1.3",
        "choices": [{"message": message, "finish_reason": "tool_calls" if tool_calls else "stop"}],
        "usage": usage,
    }
    return body | extra


def truncated() -> Obj:
    """A reply cut off by max_completion_tokens: 32 tokens and finish_reason length."""
    body = completion("Once upon")
    body["choices"][0]["finish_reason"] = "length"
    body["usage"] = {"completion_tokens": 32}
    return body


def call(name: str, arguments: Obj, id: str = "call_1") -> Obj:
    function = {"name": name, "arguments": json.dumps(arguments)}
    return {"id": id, "type": "function", "function": function}


def ok(body: Obj, seconds: float = 1.0) -> probe.Reply:
    return probe.Reply(200, body, seconds, {"x-ratelimit-remaining-requests": "2999"})


SUPPORTED = "Supported values: [minimal, low, medium, high]"


JSON_READ = '```json\n{"tool": "read_file", "arguments": {"path": "src/pricing.py"}}\n```'


JSON_EDIT = 'Done: {"tool": "edit_file", "arguments": {"old_text": "= 0.0725", "new_text": "x"}}'


def no_sleep(seconds: float) -> None:
    """Stands in for time.sleep: no test waits on the clock."""


def failure(status: int) -> probe.Reply:
    return probe.Reply(status, {"error": {"code": "bad_request", "message": "no"}}, 0.1)


def route(params: Obj) -> str:
    """Which probe step sent these parameters."""
    messages = params["messages"]
    if "tools" in params:
        if messages[-1]["role"] == "tool":
            return "tools_result"
        return "parallel" if messages[-1]["content"] == probe.READ_TWO_TASK else "tools_call"
    if messages[0]["role"] == "system":
        return "json_result" if len(messages) > 2 else "json_call"
    if "reasoning_effort" in params:
        return f"reasoning_{params['reasoning_effort']}"
    return "limits" if "max_completion_tokens" in params else "chat"


class FakeTransport:
    """Answers each step from a table, and records the steps it was asked, in order."""

    def __init__(self, table: dict[str, probe.Reply]) -> None:
        self.table = table
        self.steps: list[str] = []
        self.params: list[Obj] = []

    def answer(self, params: Obj, stream: bool) -> probe.Reply:
        step = "stream" if stream else route(params)
        self.steps.append(step)
        self.params.append(params)
        return self.table.get(step, ok(completion()))

    def complete(self, params: Obj) -> probe.Reply:
        return self.answer(params, False)

    def stream(self, params: Obj) -> probe.Reply:
        return self.answer(params, True)


def native_table() -> dict[str, probe.Reply]:
    read = call("read_file", {"path": "src/pricing.py"})
    edit = call(
        "edit_file", {"path": "src/pricing.py", "old_text": "= 0.0725", "new_text": "= 0.085"}
    )
    return {
        "tools_call": ok(completion("I'll read it first.", [read])),
        "tools_result": ok(completion(None, [edit])),
        "parallel": ok(
            completion(
                None,
                [call("read_file", {"path": "a"}, "c1"), call("read_file", {"path": "b"}, "c2")],
            )
        ),
        "reasoning_none": probe.Reply(
            400,
            {"error": {"type": "invalid_request_error", "message": f"no. {SUPPORTED}"}},
            0.1,
        ),
        "limits": ok(truncated()),
        "stream": ok(
            {
                "model": "muse-spark-1.3",
                "chunks": 4,
                "first_chunk_seconds": 0.4,
                "usage": {"prompt_tokens": 20},
            }
        ),
    }


def checks(result: probe.Result) -> dict[str, probe.Check]:
    return {c.key: c for c in result.checks}


class Scripted:
    """Answers each call with the next reply, then with the last one for ever."""

    def __init__(self, *replies: probe.Reply) -> None:
        self.replies = list(replies)
        self.sent = 0
        self.params: list[Obj] = []

    def complete(self, params: Obj) -> probe.Reply:
        self.sent += 1
        self.params.append(params)
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]

    def stream(self, params: Obj) -> probe.Reply:
        return self.complete(params)


# The SDK transport, over an in-memory HTTP stand-in ---------------------------------------------


def sdk(handler: Any) -> probe.SdkTransport:
    client = openai.DefaultHttpxClient(transport=httpx2.MockTransport(handler))
    return probe.SdkTransport("https://example.test/v1", KEY, http_client=client)


PARAMS: Obj = {"model": "m", "messages": [{"role": "user", "content": "x"}]}


# The sanitizer ---------------------------------------------------------------------------------


def secret_error() -> Obj:
    """An error that reflects the key, and a piece of it, in its code, type and message."""
    return {
        "error": {
            "code": f"c-{LONG_KEY}",
            "type": f"t-{FRAG}",
            "message": f"bad {LONG_KEY}, and {FRAG}, and Bearer {LONG_KEY}",
        }
    }
