"""The API probe over a fake transport: no network, no key, no credits."""

import sys
from pathlib import Path
from typing import Any

import pytest
from tools.tests.probe_api_helpers import (
    JSON_EDIT,
    JSON_READ,
    META,
    PARAMS,
    FakeTransport,
    Obj,
    Scripted,
    call,
    checks,
    completion,
    failure,
    native_table,
    no_sleep,
    ok,
    truncated,
)

import probe_api as probe

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


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
