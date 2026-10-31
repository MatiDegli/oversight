"""Opt-in decision reasoning, structured choices and probabilities."""

import json
import sys
from pathlib import Path

import httpx2  # the SDK's HTTP client library: its MockTransport stands in for the network
import pytest
from tools.tests.probe_api_helpers import (
    FRAG,
    KEY,
    LONG_KEY,
    META,
    FakeTransport,
    Obj,
    Scripted,
    checks,
    completion,
    failure,
    native_table,
    ok,
    sdk,
    secret_error,
)

import probe_api as probe
from oversight.settings import Settings

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# Decision probes: opt-in, fake-only, with no prerequisite calls hidden in --checks.
DECISION_CHECKS = ("decision_reasoning_off", "structured_choice", "choice_logprobs")


def decision_body(tokens: int | None = 0) -> Obj:
    body = completion('{"decision":"asked"}')
    if tokens is None:
        body.pop("usage")
    else:
        body["usage"]["completion_tokens_details"]["reasoning_tokens"] = tokens
    return body


def logged_body(alternatives: list[tuple[str, float]] | None = None) -> Obj:
    import math

    body = decision_body()
    top = alternatives if alternatives is not None else [("asked", 0.6), ("not", 0.2), ("can", 0.1)]
    body["choices"][0]["logprobs"] = {
        "content": [
            {"token": '{"decision":"', "logprob": 0.0, "top_logprobs": []},
            {
                "token": "asked",
                "logprob": math.log(0.6),
                "top_logprobs": [{"token": t, "logprob": math.log(p)} for t, p in top],
            },
            {"token": '"}', "logprob": 0.0, "top_logprobs": []},
        ]
    }
    return body


def test_selected_checks_make_only_the_named_calls() -> None:
    fake = FakeTransport(native_table())
    result = probe.run_probe(fake, META, "m", checks=("stream", "reasoning_low"))
    assert set(checks(result)) == {"stream", "reasoning_low"}
    assert fake.steps == ["reasoning_low", "stream"]
    assert len(result.calls) == 2
    assert probe.run_probe(fake, META, "m", checks=()).calls == []


@pytest.mark.parametrize("names", ["unknown", "chat,", "", "chat,unknown"])
def test_invalid_check_names_fail_before_loading_settings(
    names: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden() -> None:
        pytest.fail("invalid checks must fail before settings or transport")

    monkeypatch.setattr(probe.Settings, "load", forbidden)
    with pytest.raises(SystemExit) as exc:
        probe.main(["meta", "--checks", names])
    assert exc.value.code == 2


@pytest.mark.parametrize("provider,attempts", [(META, 1), (probe.PROVIDERS["baseten"], 2)])
def test_reasoning_setting_is_reused_and_each_attempt_is_reported(
    provider: probe.Provider, attempts: int
) -> None:
    transport = Scripted(ok(decision_body(98)), ok(decision_body()), ok(logged_body()))
    if provider.name == "meta":
        transport = Scripted(ok(decision_body()), ok(decision_body()), ok(logged_body()))
    result = probe.run_probe(transport, provider, "m", checks=DECISION_CHECKS)
    found = checks(result)
    off = found["decision_reasoning_off"]
    assert off.passed is True
    assert len(off.data["attempts"]) == attempts
    assert len(result.calls) == attempts + 2
    assert "reasoning_tokens=0" in off.evidence
    expected = (
        {"reasoning_effort": "minimal"}
        if provider.name == "meta"
        else {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
    )
    for params in transport.params[-2:]:
        assert {key: params[key] for key in expected} == expected
        assert params["response_format"] == probe.DECISION_FORMAT
    assert transport.params[-1]["logprobs"] is True
    assert transport.params[-1]["top_logprobs"] == 5
    assert found["structured_choice"].passed is True
    assert found["choice_logprobs"].passed is True
    assert "constrained choice: yes; probability over options: yes" in result.verdict


@pytest.mark.parametrize("tokens", [None, 12])
def test_accepted_reasoning_setting_without_zero_does_not_establish_off(tokens: int | None) -> None:
    result = probe.run_probe(
        Scripted(ok(decision_body(tokens)), ok(decision_body()), ok(logged_body())),
        META,
        "m",
        checks=DECISION_CHECKS,
    )
    assert checks(result)["decision_reasoning_off"].passed is False
    assert "reasoning off is not established" in result.verdict


@pytest.mark.parametrize(
    "content,passed",
    [
        ('{"decision":"not_asked"}', True),
        ('{"decision":"cannot_tell"}', True),
        ('{"decision":"asked","extra":1}', False),
        ('{"decision":1}', False),
        ('```json\n{"decision":"asked"}\n```', False),
        ("asked", False),
        ('{"decision":"asked","decision":"asked"}', False),
    ],
)
def test_structured_choice_validates_exactly_the_schema(content: str, passed: bool) -> None:
    body = decision_body()
    body["choices"][0]["message"]["content"] = content
    result = probe.run_probe(Scripted(ok(body)), META, "m", checks=("structured_choice",))
    check = checks(result)["structured_choice"]
    assert check.passed is passed
    assert check.answer == ("accepted" if passed else "ignored")


def test_rejected_structured_logprobs_retry_once_without_schema() -> None:
    body = logged_body()
    body["choices"][0]["message"]["content"] = "asked"
    body["choices"][0]["logprobs"]["content"] = body["choices"][0]["logprobs"]["content"][1:2]
    result = probe.run_probe(
        Scripted(failure(400), ok(body)), META, "m", checks=("choice_logprobs",)
    )
    assert len(result.calls) == 2
    check = checks(result)["choice_logprobs"]
    assert check.passed is True
    assert "HTTP 400" in check.evidence and "without response_format" in check.evidence


def test_probabilities_normalize_first_option_tokens_and_report_unmatched_mass() -> None:
    result = probe.choice_probabilities(logged_body())
    assert result["probabilities"] == pytest.approx(
        {"asked": 2 / 3, "not_asked": 2 / 9, "cannot_tell": 1 / 9}
    )
    assert result["unmatched_mass"] == pytest.approx(0.1)
    assert result["position"] == 1
    assert result["scope"] == "answer only"


def test_probability_handles_a_multi_token_enum_after_a_shared_quote() -> None:
    body = logged_body()
    body["choices"][0]["message"]["content"] = '{"decision":"not_asked"}'
    entries = body["choices"][0]["logprobs"]["content"]
    entries[0]["token"] = '{"decision":'
    entries.insert(1, {"token": '"', "top_logprobs": [{"token": '"', "logprob": 0.0}]})
    entries[2]["token"] = "not"
    entries.insert(3, {"token": "_asked", "top_logprobs": []})
    result = probe.choice_probabilities(body)
    assert result["position"] == 2
    assert result["probabilities"]["not_asked"] == pytest.approx(2 / 9)


def test_missing_option_is_unknown_instead_of_zero_probability() -> None:
    result = probe.choice_probabilities(logged_body([("asked", 0.6), ("not", 0.2), ("other", 0.1)]))
    assert result["probabilities"] == {
        "asked": pytest.approx(0.75),
        "not_asked": pytest.approx(0.25),
        "cannot_tell": None,
    }
    assert result["unmatched_mass"] == pytest.approx(0.2)
    assert result["complete"] is False


def test_no_matching_top_option_has_no_normalized_probabilities() -> None:
    result = probe.choice_probabilities(logged_body([("other", 0.7)]))
    assert result["probabilities"] == dict.fromkeys(("asked", "not_asked", "cannot_tell"))
    assert result["unmatched_mass"] == 1.0
    assert result["complete"] is False


def test_logprob_scope_distinguishes_answer_from_reasoning_and_unaligned_tokens() -> None:
    body = logged_body()
    body["choices"][0]["message"]["reasoning_content"] = "Classify first."
    assert probe.choice_probabilities(body)["scope"] == "answer only"
    body["choices"][0]["logprobs"]["content"].insert(0, {"token": "Classify first."})
    assert probe.choice_probabilities(body)["scope"] == "reasoning and answer"
    body["choices"][0]["logprobs"]["content"][0]["token"] = "unexplained"
    result = probe.choice_probabilities(body)
    assert result["scope"] == "cannot tell" and result["complete"] is False


def test_baseten_stops_at_the_first_zero_reasoning_attempt() -> None:
    transport = Scripted(ok(decision_body()))
    result = probe.run_probe(transport, probe.PROVIDERS["baseten"], "m", checks=DECISION_CHECKS[:1])
    assert len(result.calls) == 1
    assert transport.params[0]["reasoning_effort"] == "none"
    assert "response_format" not in transport.params[0]


def test_baseten_falls_back_after_a_rejected_reasoning_switch() -> None:
    transport = Scripted(failure(400), ok(decision_body()), ok(decision_body()))
    result = probe.run_probe(transport, probe.PROVIDERS["baseten"], "m", checks=DECISION_CHECKS[:2])
    assert len(result.calls) == 3
    assert checks(result)["decision_reasoning_off"].passed
    assert "HTTP 400" in checks(result)["decision_reasoning_off"].evidence
    assert "extra_body" in transport.params[-1] and "reasoning_effort" not in transport.params[-1]


def test_no_accepted_reasoning_switch_skips_later_calls() -> None:
    result = probe.run_probe(Scripted(failure(400)), META, "m", checks=DECISION_CHECKS)
    assert len(result.calls) == 1
    assert checks(result)["structured_choice"].answer == "not run"
    assert checks(result)["choice_logprobs"].answer == "not run"


def test_choice_calls_with_reasoning_cannot_receive_a_positive_off_verdict() -> None:
    body = logged_body()
    body["usage"]["completion_tokens_details"]["reasoning_tokens"] = 12
    result = probe.run_probe(
        Scripted(ok(decision_body()), ok(decision_body(12)), ok(body)),
        META,
        "m",
        checks=DECISION_CHECKS,
    )
    assert "constrained choice: no; probability over options: no" in result.verdict
    assert "zero reasoning tokens not reported on every choice call" in result.verdict


def test_reasoning_attempt_evidence_survives_exhausted_budget() -> None:
    session = probe.Session(Scripted(ok(decision_body(98))), limit=1)
    check = probe.check_decision_reasoning_off(session, probe.PROVIDERS["baseten"], "m")
    assert "reasoning_tokens=98" in check.evidence and "budget spent" in check.evidence
    assert len(check.data["attempts"]) == 1


def test_logprob_rejection_evidence_survives_exhausted_budget() -> None:
    session = probe.Session(Scripted(failure(400)), limit=1)
    session.decision_settings = {"reasoning_effort": "minimal"}
    check = probe.check_choice_logprobs(session, "m")
    assert "HTTP 400" in check.evidence and "fallback skipped" in check.evidence
    assert not check.passed


@pytest.mark.parametrize("value", [None, [], [{"token": "unrelated", "top_logprobs": []}]])
def test_absent_empty_and_unaligned_logprobs_are_inconclusive(value: object) -> None:
    body = logged_body()
    body["choices"][0]["logprobs"]["content"] = value
    result = probe.choice_probabilities(body)
    assert result["present"] is isinstance(value, list)
    assert result["complete"] is False
    assert result["probabilities"] == dict.fromkeys(probe.DECISIONS)


def test_merged_json_token_and_multiple_alternatives_per_option() -> None:
    body = logged_body()
    entry = body["choices"][0]["logprobs"]["content"][1]
    prefix = '{"decision":"'
    entry["token"] = prefix + "asked"
    for alternative in entry["top_logprobs"]:
        alternative["token"] = prefix + alternative["token"]
    body["choices"][0]["logprobs"]["content"].pop(0)
    result = probe.choice_probabilities(body)
    assert result["position"] == 0
    assert result["complete"] is True
    assert result["probabilities"]["asked"] == pytest.approx(2 / 3)
    alternatives = [("a", 0.3), ("asked", 0.3), ("not", 0.2), ("can", 0.1), ("askedly", 0.05)]
    assert probe.choice_probabilities(logged_body(alternatives))["probabilities"][
        "asked"
    ] == pytest.approx(2 / 3)


def test_sdk_serializes_baseten_extra_body_and_strict_schema() -> None:
    seen: list[Obj] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        params = json.loads(request.content)
        seen.append(params)
        if len(seen) == 1:
            return httpx2.Response(200, json=decision_body(98))
        return httpx2.Response(200, json=logged_body())

    result = probe.run_probe(sdk(handler), probe.PROVIDERS["baseten"], "m", checks=DECISION_CHECKS)
    assert len(result.calls) == len(seen) == 4
    assert seen[0]["reasoning_effort"] == "none"
    for params in seen[1:]:
        assert params["chat_template_kwargs"] == {"enable_thinking": False}
        assert "extra_body" not in params and "reasoning_effort" not in params
    assert seen[2]["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "decision",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "decision": {"type": "string", "enum": ["asked", "not_asked", "cannot_tell"]}
                },
                "required": ["decision"],
                "additionalProperties": False,
            },
        },
    }


def test_checks_cli_writes_only_selected_rows_and_one_decision_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(probe.Settings, "load", staticmethod(lambda: Settings(meta_api_key=KEY)))
    transport = Scripted(ok(decision_body()), ok(decision_body()), ok(logged_body()))
    monkeypatch.setattr(probe, "SdkTransport", lambda base_url, key: transport)
    report = tmp_path / "report.md"
    assert probe.main(["meta", "--checks", ",".join(DECISION_CHECKS), "--report", str(report)]) == 0
    assert "calls made: 3" in capsys.readouterr().out
    text = report.read_text()
    assert text.count("| Decision ") == 3
    assert text.count("constrained choice:") == 1
    assert "Plain chat call" not in text


def test_decision_errors_are_redacted_in_terminal_report_and_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        probe.Settings, "load", staticmethod(lambda: Settings(meta_api_key=LONG_KEY))
    )
    transport = Scripted(probe.Reply(400, secret_error(), 0.1))
    monkeypatch.setattr(probe, "SdkTransport", lambda base_url, key: transport)
    report, details = tmp_path / "report.md", tmp_path / "details.json"
    assert (
        probe.main(
            [
                "meta",
                "--checks",
                ",".join(DECISION_CHECKS),
                "--report",
                str(report),
                "--details",
                str(details),
            ]
        )
        == 0
    )
    text = capsys.readouterr().out + report.read_text() + details.read_text()
    assert LONG_KEY not in text and FRAG not in text
