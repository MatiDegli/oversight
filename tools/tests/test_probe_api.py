"""The API probe over a fake transport: no network, no key, no credits."""

import json
import sys
from pathlib import Path
from typing import Any

import httpx2  # the SDK's HTTP client library: its MockTransport stands in for the network
import openai
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import probe_api as probe
from oversight.settings import Settings

KEY = "test-key"
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
        "tools_call": ok(completion(None, [read])),
        "tools_result": ok(completion(None, [edit])),
        "parallel": ok(
            completion(
                None,
                [call("read_file", {"path": "a"}, "c1"), call("read_file", {"path": "b"}, "c2")],
            )
        ),
        "reasoning_none": failure(400),
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
    read = '```json\n{"tool": "read_file", "arguments": {"path": "src/pricing.py"}}\n```'
    edit = 'Done: {"tool": "edit_file", "arguments": {"old_text": "= 0.0725", "new_text": "x"}}'
    table = {
        "tools_call": ok(completion("I would read the file.")),
        "json_call": ok(completion(read)),
        "json_result": ok(completion(edit)),
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


def test_a_failed_first_call_aborts_before_spending_more() -> None:
    fake = FakeTransport({"chat": failure(404)})
    with pytest.raises(probe.Aborted):
        probe.run_probe(fake, META, "no-such-model")
    assert fake.steps == ["chat"]


def test_the_session_refuses_the_call_after_the_last_and_counts_failures() -> None:
    fake = FakeTransport({"chat": failure(500)})
    session = probe.Session(fake, limit=2)
    params = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
    session.send("one", params)
    session.send("two", params)
    with pytest.raises(probe.BudgetExhausted):
        session.send("three", params)
    assert len(fake.steps) == 2


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


def test_a_report_that_would_hold_the_key_is_not_written(tmp_path: Path) -> None:
    path = tmp_path / "report.md"
    with pytest.raises(RuntimeError):
        probe.write_report(path, "meta", f"## Meta\n\n{KEY}", KEY)
    assert not path.exists()


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
