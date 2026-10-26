"""The API probe over a fake transport: no network, no key, no credits."""

import json
import sys
import tomllib
from pathlib import Path
from typing import Any

import httpx2  # the SDK's HTTP client library: its MockTransport stands in for the network
import openai
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import probe_api as probe
from oversight.settings import Settings

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


def test_native_tools_decide_the_verdict_and_stay_inside_the_budget() -> None:
    fake = FakeTransport(native_table())
    result = probe.run_probe(fake, META, "muse-spark-1.3")
    found = checks(result)
    assert len(fake.steps) == len(result.calls) == probe.MAX_CALLS
    assert found["tools_call"].passed and found["tools_result"].passed
    assert "json_fallback" not in found
    assert result.verdict.startswith("Native tools.")
    assert "yes, 2 calls in one turn" in found["parallel"].answer
    assert result.model_reported == "muse-spark-1.3"
    assert found["reasoning_none"].answer == "rejected, HTTP 400"
    assert "limit is honored" in found["limits"].answer
    assert "supported: minimal, low, medium, high." in result.verdict
    assert "low gave 5 reasoning tokens and the default 5" in result.verdict
    assert "reasoning used the budget" not in found["limits"].answer  # the fake reply has text
    assert "text next to a tool call" in result.verdict


def test_an_empty_reply_under_a_small_budget_is_reported_as_reasoning_ate_it() -> None:
    body = truncated()
    body["choices"][0]["message"]["content"] = ""
    body["usage"]["completion_tokens_details"] = {"reasoning_tokens": 30}
    session = probe.Session(FakeTransport({"limits": ok(body)}))
    answer = probe.check_limits(session, "m").answer
    assert answer.endswith("reasoning used the budget and left the reply empty")


def test_the_tool_result_is_resent_with_the_assistant_turn_and_the_call_id() -> None:
    fake = FakeTransport(native_table())
    probe.run_probe(fake, META, "muse-spark-1.3")
    messages = fake.params[fake.steps.index("tools_result")]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "tool"]
    assert messages[1]["tool_calls"][0]["id"] == messages[2]["tool_call_id"] == "call_1"
    assert "1\tDEFAULT_RATE" in messages[2]["content"]


def test_an_edit_that_ignores_the_result_does_not_pass() -> None:
    table = native_table()
    guess = call(
        "edit_file", {"path": "src/pricing.py", "old_text": "= 0.1", "new_text": "= 0.085"}
    )
    table["tools_result"] = ok(completion(None, [guess]))
    found = checks(probe.run_probe(FakeTransport(table), META, "muse-spark-1.3"))
    assert found["tools_result"].passed is False


def test_the_json_fallback_runs_only_when_native_tools_fail_and_fits_the_budget() -> None:
    table = {
        "tools_call": ok(completion("I would read the file.")),
        "json_call": ok(completion(JSON_READ)),
        "json_result": ok(completion(JSON_EDIT)),
    }
    fake = FakeTransport(table)
    result = probe.run_probe(fake, META, "muse-spark-1.3")
    found = checks(result)
    assert found["tools_call"].passed is False
    assert found["json_fallback"].passed is True
    assert result.verdict.startswith("JSON fallback.")
    assert "tools_result" not in fake.steps and "parallel" not in fake.steps
    assert len(fake.steps) <= probe.MAX_CALLS


def test_when_both_routes_fail_the_verdict_says_so() -> None:
    table = {"tools_call": failure(400), "json_call": ok(completion("no json here"))}
    result = probe.run_probe(FakeTransport(table), META, "muse-spark-1.3")
    assert result.verdict.startswith("Neither route passed")


GUESS = call("edit_file", {"path": "src/pricing.py", "old_text": "= 0.1", "new_text": "= 0.085"})


@pytest.mark.parametrize(
    "bad", [failure(400), ok(completion(None, [GUESS]))], ids=["rejected", "ignores the result"]
)
def test_a_failed_tool_result_turn_still_tries_the_json_fallback(bad: probe.Reply) -> None:
    table = native_table() | {
        "tools_result": bad,
        "json_call": ok(completion(JSON_READ)),
        "json_result": ok(completion(JSON_EDIT)),
    }
    fake = FakeTransport(table)
    result = probe.run_probe(fake, META, "muse-spark-1.3")
    found = checks(result)
    assert found["tools_result"].passed is False
    assert found["json_fallback"].passed is True
    assert result.verdict.startswith("JSON fallback.")
    assert fake.steps.index("json_call") > fake.steps.index("tools_result")
    assert len(fake.steps) == len(result.calls) <= probe.MAX_CALLS


class Outage(FakeTransport):
    """Like FakeTransport, but answers the first `times` calls of one step with a 503."""

    def __init__(self, table: dict[str, probe.Reply], step: str, times: int) -> None:
        super().__init__(table)
        self.down = {step: times}

    def answer(self, params: Obj, stream: bool) -> probe.Reply:
        reply = super().answer(params, stream)
        step = self.steps[-1]
        if self.down.get(step, 0) > 0:
            self.down[step] -= 1
            return failure(503)
        return reply


def test_a_fallback_skipped_for_budget_leaves_the_verdict_not_decided() -> None:
    # chat takes three calls (two 503s), tools_call one, tools_result four more: the cap is spent
    table = native_table() | {"tools_result": failure(503)}
    result = probe.run_probe(Outage(table, "chat", 2), META, "muse-spark-1.3", sleep=no_sleep)
    found = checks(result)
    assert found["tools_result"].passed is False
    assert found["json_fallback"].answer == "skipped"
    assert len(result.calls) == probe.MAX_CALLS
    assert result.verdict.startswith("Not decided")
    assert "Neither route" not in result.verdict


def test_a_tool_result_turn_that_worked_does_not_run_the_fallback() -> None:
    fake = FakeTransport(native_table())
    probe.run_probe(fake, META, "muse-spark-1.3")
    assert "json_call" not in fake.steps


def test_a_failed_first_call_aborts_before_spending_more() -> None:
    fake = FakeTransport({"chat": failure(404)})
    with pytest.raises(probe.Aborted):
        probe.run_probe(fake, META, "no-such-model")
    assert fake.steps == ["chat"]


def test_the_session_refuses_the_call_after_the_last_and_counts_failures() -> None:
    fake = FakeTransport({"chat": failure(400)})
    session = probe.Session(fake, limit=2, sleep=no_sleep)
    params = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
    session.send("one", params)
    session.send("two", params)
    with pytest.raises(probe.BudgetExhausted):
        session.send("three", params)
    assert len(fake.steps) == 2


class Scripted:
    """Answers each call with the next reply, then with the last one for ever."""

    def __init__(self, *replies: probe.Reply) -> None:
        self.replies = list(replies)
        self.sent = 0

    def complete(self, params: Obj) -> probe.Reply:
        self.sent += 1
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]

    def stream(self, params: Obj) -> probe.Reply:
        return self.complete(params)


def test_two_429s_then_a_200_make_three_counted_calls_with_growing_delays() -> None:
    transport = Scripted(failure(429), failure(429), ok(completion()))
    slept: list[float] = []
    session = probe.Session(transport, sleep=slept.append, rand=lambda: 0.5)
    reply = session.send("chat", PARAMS)
    assert reply.status == 200 and transport.sent == 3
    assert [c["status"] for c in session.calls] == [429, 429, 200]
    assert [c["step"] for c in session.calls] == ["chat", "chat (retry 1)", "chat (retry 2)"]
    assert slept == [1.0, 2.0]
    assert session.replies["chat"] is reply


def test_the_call_cap_stops_the_retries() -> None:
    transport = Scripted(failure(503))
    slept: list[float] = []
    session = probe.Session(transport, limit=3, sleep=slept.append, rand=lambda: 0.5)
    reply = session.send("chat", PARAMS)
    assert reply.status == 503 and transport.sent == len(session.calls) == 3
    assert len(slept) == 2  # no wait after the last call the cap allows
    with pytest.raises(probe.BudgetExhausted):
        session.send("next", PARAMS)
    assert transport.sent == 3


def test_retries_are_bounded_even_under_a_large_cap() -> None:
    transport = Scripted(failure(429))
    session = probe.Session(transport, limit=50, sleep=no_sleep)
    session.send("chat", PARAMS)
    assert transport.sent == probe.MAX_RETRIES + 1


@pytest.mark.parametrize(("status", "attempts"), [(400, 1), (401, 1), (404, 1), (500, 4), (503, 4)])
def test_only_429_and_5xx_are_retried(status: int, attempts: int) -> None:
    transport = Scripted(failure(status))
    slept: list[float] = []
    probe.Session(transport, sleep=slept.append).send("chat", PARAMS)
    assert transport.sent == attempts and len(slept) == attempts - 1


def test_the_delay_doubles_is_jittered_capped_and_honors_retry_after() -> None:
    reply = failure(429)
    assert probe.backoff(0, reply, lambda: 0.0) == 0.5
    assert probe.backoff(0, reply, lambda: 1.0) == 1.5
    assert probe.backoff(2, reply, lambda: 0.5) == 4.0
    assert probe.backoff(20, reply, lambda: 1.0) == probe.BACKOFF_MAX
    asked = probe.Reply(429, {}, 0.1, {"retry-after": "7"})
    assert probe.backoff(0, asked, lambda: 0.5) == 7.0
    date = probe.Reply(429, {}, 0.1, {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"})
    assert probe.backoff(0, date, lambda: 0.5) == 1.0


def test_a_rate_limited_first_call_is_retried_before_the_run_aborts() -> None:
    fake = FakeTransport({"chat": failure(429)})
    slept: list[float] = []
    with pytest.raises(probe.Aborted):
        probe.run_probe(fake, META, "m", sleep=slept.append, rand=lambda: 0.5)
    assert fake.steps == ["chat"] * (probe.MAX_RETRIES + 1)
    assert slept == [1.0, 2.0, 4.0]


def test_retries_spend_the_budget_of_the_steps_after_them() -> None:
    table = native_table() | {"tools_call": failure(503)}
    fake = FakeTransport(table)
    result = probe.run_probe(fake, META, "muse-spark-1.3", sleep=no_sleep)
    assert len(fake.steps) == len(result.calls) <= probe.MAX_CALLS
    assert fake.steps.count("tools_call") == probe.MAX_RETRIES + 1


def test_missing_usage_is_unknown_and_never_zero() -> None:
    bare = {"model": "m", "choices": [{"message": {"content": "x"}, "finish_reason": "stop"}]}
    assert probe.reasoning_tokens(bare) is None
    assert probe.cached_tokens(probe.usage_of(bare)) == ("not reported", None)
    session = probe.Session(FakeTransport({}))
    session.replies["chat"] = ok(bare)
    answer = probe.check_usage(session)
    assert "missing on: chat" in answer.answer and "unknown, never zero" in answer.answer
    assert probe.cached_tokens({"prompt_tokens_details": {"cached_tokens": 0}}) == (
        "prompt_tokens_details.cached_tokens",
        0,
    )


def test_usage_is_listed_per_call_with_a_question_mark_where_missing() -> None:
    session = probe.Session(FakeTransport({}))
    session.replies["chat"] = ok(completion())
    session.replies["bare"] = ok({"model": "m"})
    evidence = probe.check_usage(session).evidence
    assert "chat: 20/9/0/5" in evidence and "bare: ?/?/?/?" in evidence
    cached = completion()
    cached["usage"]["prompt_tokens_details"]["cached_tokens"] = 12
    session.replies["hit"] = ok(cached)
    assert "non-zero on 1 of 3" in probe.check_usage(session).answer


def test_every_check_carries_the_field_names_of_usage() -> None:
    found = checks(probe.run_probe(FakeTransport(native_table()), META, "muse-spark-1.3"))
    assert "completion_tokens_details.reasoning_tokens=5" in found["usage"].evidence
    assert "prompt_tokens_details.cached_tokens" in found["usage"].evidence
    assert "x-ratelimit-remaining-requests=2999" in found["rate_headers"].answer


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"tool": "read_file", "arguments": {"path": "a"}}', ("read_file", {"path": "a"})),
        ('```json\n{"tool": "edit_file", "arguments": {}}\n```', ("edit_file", {})),
        (
            'First {not json} then {"tool": "read_file", "arguments": {"path": "b"}}',
            ("read_file", {"path": "b"}),
        ),
        ('{"name": "read_file"}', None),
        ("no object", None),
    ],
)
def test_json_tool_calls_are_found_in_prose_and_fences(text: str, expected: Any) -> None:
    assert probe.parse_json_tool_call(text) == expected


def test_stream_chunks_fold_into_text_usage_and_the_first_chunk_time() -> None:
    chunks: list[tuple[float, Obj]] = [
        (0.3, {"model": "m", "choices": [{"delta": {"content": "54"}, "finish_reason": None}]}),
        (0.4, {"model": "m", "choices": [{"delta": {"content": "1"}, "finish_reason": "stop"}]}),
        (0.5, {"model": "m", "choices": [], "usage": {"prompt_tokens": 7}}),
    ]
    summary = probe.summarize_stream(chunks)
    assert summary["text"] == "541" and summary["chunks"] == 3
    assert summary["first_chunk_seconds"] == 0.3 and summary["usage"] == {"prompt_tokens": 7}
    assert summary["finish_reason"] == "stop"


# The SDK transport, over an in-memory HTTP stand-in ---------------------------------------------


def sdk(handler: Any) -> probe.SdkTransport:
    client = openai.DefaultHttpxClient(transport=httpx2.MockTransport(handler))
    return probe.SdkTransport("https://example.test/v1", KEY, http_client=client)


PARAMS: Obj = {"model": "m", "messages": [{"role": "user", "content": "x"}]}


def test_a_429_is_one_call_not_three_and_the_key_is_scrubbed() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        body = {"error": {"code": "rate_limited", "message": f"slow down {KEY}"}}
        return httpx2.Response(429, json=body, headers={"retry-after": "2", "set-cookie": "a=b"})

    reply = sdk(handler).complete(PARAMS)
    assert len(seen) == 1  # the SDK's own retries are off: every attempt is a counted call
    assert reply.status == 429
    assert reply.body == {"error": {"code": "rate_limited", "message": "slow down [redacted]"}}
    assert reply.headers == {"retry-after": "2"}
    assert KEY not in json.dumps(reply.body)


def test_a_401_keeps_a_code_and_no_message() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(401, json={"error": {"code": "invalid_api_key", "message": KEY}})

    reply = sdk(handler).complete(PARAMS)
    assert reply.status == 401
    assert reply.body == {"error": {"code": "invalid_api_key"}}


def test_a_success_keeps_the_body_status_and_rate_limit_headers_only() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["authorization"] == f"Bearer {KEY}"
        headers = {"x-ratelimit-limit-requests": "3000", "x-request-id": "r1"}
        return httpx2.Response(200, json=completion(), headers=headers)

    reply = sdk(handler).complete(PARAMS)
    assert reply.status == 200 and reply.body["model"] == "muse-spark-1.3"
    assert reply.headers == {"x-ratelimit-limit-requests": "3000"}


def test_a_stream_asks_for_usage_and_is_summarized() -> None:
    sent: list[Obj] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        chunks = [
            {"model": "m", "choices": [{"delta": {"content": "5"}, "finish_reason": None}]},
            {"model": "m", "choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 1}},
        ]
        text = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx2.Response(200, text=text, headers={"content-type": "text/event-stream"})

    reply = sdk(handler).stream(PARAMS)
    assert sent[0]["stream"] is True and sent[0]["stream_options"] == {"include_usage": True}
    assert reply.status == 200 and reply.body["text"] == "5"
    assert reply.body["usage"] == {"prompt_tokens": 7, "completion_tokens": 1}


def test_a_network_error_is_status_zero_with_the_error_type_only() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError(f"cannot reach {KEY}")

    reply = sdk(handler).complete(PARAMS)
    assert reply.status == 0
    assert reply.body == {"error": {"type": "APIConnectionError"}}


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


def test_the_redactor_removes_the_key_any_fragment_of_it_and_credential_shapes() -> None:
    redact = probe.Redactor(LONG_KEY)
    assert redact(f"a {LONG_KEY} b") == "a [redacted] b"
    assert redact(f"x{FRAG}y") == "x[redacted]y"
    assert redact(f"x{LONG_KEY[3:20]}y") == "x[redacted]y"  # a long fragment is one redaction
    assert redact("short ey-fo") == "short ey-fo"  # under 8 characters says nothing about the key
    for shaped in ("Bearer abcdef123456", "sk-proj-abcdefgh1234", "a1b2c3d4e5f6g7h8i9j0k1l2m3n4"):
        assert shaped.split()[-1] not in probe.Redactor(None)(f"x {shaped} y")
    assert redact(redact(f"{LONG_KEY} {FRAG}")) == redact(f"{LONG_KEY} {FRAG}")  # idempotent


def test_the_redactor_leaves_the_committed_report_and_field_names_alone() -> None:
    redact = probe.Redactor(None)
    report = (Path(__file__).resolve().parents[2] / "docs" / "api-probe.md").read_text()
    assert redact(report) == report
    names = "completion_tokens_details.reasoning_tokens=258 meta-models/muse-spark-open-1026"
    assert redact(names) == names


def test_an_error_keeps_no_key_in_its_code_type_message_or_headers() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        headers = {"x-ratelimit-reset": f"r-{FRAG}", "retry-after": LONG_KEY, "x-other": LONG_KEY}
        return httpx2.Response(400, json=secret_error(), headers=headers)

    client = openai.DefaultHttpxClient(transport=httpx2.MockTransport(handler))
    reply = probe.SdkTransport("https://example.test/v1", LONG_KEY, http_client=client).complete(
        PARAMS
    )
    text = json.dumps([reply.body, reply.headers])
    assert LONG_KEY not in text and FRAG not in text
    assert reply.body["error"]["code"].startswith("c-") and "bad" in reply.body["error"]["message"]


def test_an_error_on_the_first_call_prints_and_writes_no_key_or_fragment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(400, json=secret_error(), headers={"retry-after": FRAG})

    real = probe.SdkTransport
    client = openai.DefaultHttpxClient(transport=httpx2.MockTransport(handler))
    monkeypatch.setattr(
        probe.Settings, "load", classmethod(lambda cls: Settings(meta_api_key=LONG_KEY))
    )
    monkeypatch.setattr(
        probe, "SdkTransport", lambda base_url, key: real(base_url, key, http_client=client)
    )
    report, details = tmp_path / "report.md", tmp_path / "details.json"
    assert probe.main(["meta", "--report", str(report), "--details", str(details)]) == 1
    seen = capsys.readouterr()
    written = "".join(f.read_text() for f in tmp_path.rglob("*") if f.is_file())
    assert seen.out.startswith("aborted: the first call failed")
    for secret in (LONG_KEY, FRAG):
        assert secret not in seen.out + seen.err + written


def test_a_run_that_reflects_the_key_prints_and_writes_none_of_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    table = native_table() | {
        "chat": probe.Reply(200, completion(f"541171 {FRAG}"), 1.0, {"retry-after": LONG_KEY}),
        "reasoning_none": probe.Reply(400, secret_error(), 0.1),
        "limits": probe.Reply(400, secret_error(), 0.1),
    }
    monkeypatch.setattr(
        probe.Settings, "load", classmethod(lambda cls: Settings(meta_api_key=LONG_KEY))
    )
    monkeypatch.setattr(probe, "SdkTransport", lambda base_url, key: FakeTransport(table))
    report, details = tmp_path / "report.md", tmp_path / "details.json"
    assert probe.main(["meta", "--report", str(report), "--details", str(details)]) == 0
    seen = capsys.readouterr()
    written = report.read_text() + details.read_text()
    for secret in (LONG_KEY, FRAG):
        assert secret not in seen.out + seen.err + written
    assert "[redacted]" in written and json.loads(details.read_text())["provider"] == "meta"


# The declared dependencies --------------------------------------------------------------------


def test_httpx2_is_declared_for_the_tests_and_locked_at_the_same_version() -> None:
    root = Path(__file__).resolve().parents[2]
    declared = tomllib.loads((root / "pyproject.toml").read_text())["dependency-groups"]["dev"]
    assert "httpx2==2.13.1" in declared
    packages = tomllib.loads((root / "uv.lock").read_text())["package"]
    workspace = next(p for p in packages if p["name"] == "oversight-workspace")
    assert {"name": "httpx2", "specifier": "==2.13.1"} in workspace["metadata"]["requires-dev"][
        "dev"
    ]


# The report -----------------------------------------------------------------------------------


def test_the_report_has_the_answers_the_sources_and_no_key_or_path() -> None:
    result = probe.run_probe(FakeTransport(native_table()), META, "muse-spark-1.3")
    text = probe.merge_report(None, "meta", probe.render_section(result))
    assert "| Question | Answer | Evidence |" in text
    assert "Calls made: 8 of at most 8" in text
    assert "https://dev.meta.ai/docs/tool-calling" in text
    assert "Skipped: BASETEN_API_KEY is not set." in text
    assert "Still unknown for agent B" in text
    assert KEY not in text and "/home" not in text and "Authorization" not in text


def test_a_section_is_replaced_and_the_other_provider_is_kept() -> None:
    result = probe.run_probe(FakeTransport(native_table()), META, "muse-spark-1.3")
    first = probe.merge_report(None, "meta", probe.render_section(result))
    again = probe.merge_report(first, "baseten", "## Baseten\n\nreal results")
    assert "real results" in again and "Native tools." in again
    skipped = probe.merge_report(again, "baseten", "## Baseten\n\nskipped", overwrite=False)
    assert "real results" in skipped and "skipped" not in skipped.split("real results")[1][:20]
    assert probe.merge_report(first, "meta", "## new meta").count("<!-- probe:meta -->") == 1


def test_a_report_is_redacted_whole_before_it_is_written(tmp_path: Path) -> None:
    path = tmp_path / "report.md"
    probe.write_report(path, "meta", f"## Meta\n\n{LONG_KEY} {FRAG}", LONG_KEY)
    written = path.read_text()
    assert "## Meta" in written and LONG_KEY not in written and FRAG not in written
    probe.write_report(path, "baseten", "## Baseten\n\nok", LONG_KEY)
    assert "## Meta" in path.read_text() and LONG_KEY not in path.read_text()


def test_meta_probes_the_contributor_model_by_default_and_model_still_overrides_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(probe.Settings, "load", classmethod(lambda cls: Settings(meta_api_key=KEY)))
    fake = FakeTransport(native_table())
    monkeypatch.setattr(probe, "SdkTransport", lambda base_url, key: fake)
    report = tmp_path / "report.md"
    assert probe.main(["meta", "--report", str(report)]) == 0
    assert fake.params and {p["model"] for p in fake.params} == {"muse-spark-1.3-contributor"}
    assert "Model requested: `muse-spark-1.3-contributor`" in report.read_text()
    first = len(fake.params)
    assert probe.main(["meta", "--model", "muse-spark-1.3", "--report", str(report)]) == 0
    assert {p["model"] for p in fake.params[first:]} == {"muse-spark-1.3"}
    assert "- Model requested: `muse-spark-1.3`; reported by the API" in report.read_text()


def test_a_rerun_keeps_hand_written_sections_before_and_after_the_blocks(tmp_path: Path) -> None:
    path = tmp_path / "report.md"
    probe.write_report(path, "meta", "## Meta\n\nfirst run", None)
    before = "## A note added by hand\n\nThe probe did not write this.\n"
    after = "## Contributor model (2026-10-26)\n\nNor this one.\n"
    path.write_text(before + "\n" + path.read_text() + "\n" + after)
    probe.write_report(path, "meta", "## Meta\n\nsecond run", None)
    text = path.read_text()
    assert text.startswith(before) and text.endswith(after)  # unchanged and in place
    assert text.index(before) < text.index("<!-- probe:meta -->") < text.index(after)
    assert "second run" in text and "first run" not in text  # only the block was rewritten
    assert text.count("<!-- probe:meta -->") == 1 and "<!-- probe:baseten -->" in text


def test_text_without_blocks_survives_and_a_missing_provider_block_is_added() -> None:
    notes = "# Someone's notes\n\nKeep them.\n"
    fresh = probe.merge_report(notes, "meta", "## Meta\n\nrun")
    assert fresh.startswith("# API probe") and fresh.endswith(notes)  # nothing of the file is lost
    only_meta = "<!-- probe:meta -->\n## Meta\n\nrun\n<!-- /probe:meta -->\n" + notes
    again = probe.merge_report(only_meta, "meta", "## Meta\n\nagain")
    assert "## Meta\n\nagain" in again and notes in again
    # the notes stay in place, after the meta block and before the block the file never had
    assert (
        again.index("<!-- /probe:meta -->")
        < again.index(notes)
        < again.index("<!-- probe:baseten -->")
    )
    assert "Skipped: BASETEN_API_KEY is not set." in again


def test_main_without_a_key_writes_a_skipped_section_and_calls_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(probe.Settings, "load", classmethod(lambda cls: Settings()))
    monkeypatch.setattr(probe, "SdkTransport", lambda *a, **k: pytest.fail("no key, no client"))
    report = tmp_path / "report.md"
    assert probe.main(["baseten", "--report", str(report)]) == 2
    assert "BASETEN_API_KEY is not set" in capsys.readouterr().out
    assert report.read_text().count("Skipped: ") == 2


def test_main_with_a_key_writes_the_report_and_prints_no_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(probe.Settings, "load", classmethod(lambda cls: Settings(meta_api_key=KEY)))
    fake = FakeTransport(native_table())
    monkeypatch.setattr(probe, "SdkTransport", lambda base_url, key: fake)
    report, details = tmp_path / "report.md", tmp_path / "details.json"
    assert probe.main(["meta", "--report", str(report), "--details", str(details)]) == 0
    out = capsys.readouterr().out
    assert "calls made: 8" in out
    assert KEY not in out + report.read_text() + details.read_text()
    assert json.loads(details.read_text())["provider"] == "meta"
