"""Opt-in production lead preview exchange over scripted transports only."""

import json
from dataclasses import asdict
from unittest.mock import AsyncMock

import httpx2
import pytest
from tools.tests.probe_api_helpers import (
    FRAG,
    LONG_KEY,
    META,
    FakeTransport,
    Scripted,
    call,
    checks,
    completion,
    failure,
    native_table,
    no_sleep,
    ok,
    sdk,
    secret_error,
    truncated,
)

import probe_api as probe
from oversight.agents import LEAD_LIMITS, build_lead_tool_schemas, prompt_version
from oversight.agents._prompts import LEAD_PROMPT
from oversight.settings import Settings
from oversight.workspace import PreviewManager

ARGS = {"kind": "preview", "agent_id": probe.PREVIEW_TARGET, "caption": "Try the current change"}
FINAL = "You can open the preview. Review and Approve remain yours."


def status():
    return ok(completion(None, [call("status", {})]))


def view(arguments=None):
    return ok(completion(None, [call("show_view", ARGS if arguments is None else arguments)]))


def finish(text=FINAL):
    return ok(completion(None, [call("finish", {"reply": text})]))


def run(*replies):
    transport = Scripted(*replies)
    result = probe.run_probe(
        transport, META, META.model, checks=("preview_suggestion",), sleep=no_sleep
    )
    return result, transport, checks(result)["preview_suggestion"]


def test_valid_exchange_uses_exact_production_prompt_schema_and_adapters(monkeypatch):
    seen = []
    original = probe.LeadTools.call

    async def audited(self, name, arguments):
        seen.append(name)
        return await adapter(self, name, arguments)

    # A real method spy via AsyncMock preserves the production argument parser.
    adapter = AsyncMock(side_effect=original)
    monkeypatch.setattr(probe.LeadTools, "call", audited)
    lifecycle = AsyncMock(side_effect=AssertionError("lifecycle is outside this probe"))
    for name in ("start", "stop", "renew", "get"):
        monkeypatch.setattr(PreviewManager, name, lifecycle)
    result, transport, check = run(status(), view(), finish())
    assert check.passed and check.answer == "passed" and len(result.calls) == 3
    assert check.data == {
        "successful_status": True,
        "view_count": 1,
        "final_answer": FINAL,
        "authority_preserved": True,
        "prompt_version": prompt_version(),
        "scenario": probe.PREVIEW_SCENARIO,
        "classification": "passed",
    }
    assert seen == ["status", "show_view", "finish"]
    assert adapter.call_count == 3
    lifecycle.assert_not_called()
    for params in transport.params:
        assert params["messages"][0] == {"role": "system", "content": LEAD_PROMPT}
        assert params["tools"] == build_lead_tool_schemas(LEAD_LIMITS)
        assert params["reasoning_effort"] == "low" and params["max_completion_tokens"] == 2048
    assert probe.PREVIEW_PERSON in str(transport.params[0]["messages"])
    observation = transport.params[1]["messages"][-1]
    assert observation["role"] == "tool"
    assert 'eligible preview data:\n```json\n{"agent_id":"agent-2"}' in observation["content"]
    assert "Shown preview view" in transport.params[2]["messages"][-1]["content"]
    assert prompt_version() in probe.render_section(result)
    assert probe.PREVIEW_SCENARIO in probe.render_section(result)


@pytest.mark.parametrize(
    "arguments,classification",
    [
        (ARGS | {"agent_id": "agent-999"}, "wrong_reference"),
        (ARGS | {"agent_id": "agent-1"}, "wrong_reference"),
        ({"kind": "preview"}, "invalid_arguments"),
        (ARGS | {"agent_ids": ["agent-2"]}, "invalid_arguments"),
        (ARGS | {"agent_id": None}, "wrong_reference"),
        *[
            (ARGS | {key: value}, "invalid_arguments")
            for key, value in {
                "revision": 1,
                "head_commit": "b" * 40,
                "change_id": "change-1",
                "url": "http://secret.invalid",
                "command": "launch",
                "profile": "demo",
                "token": "planted-token",
                "facts": "invented",
                "paths": None,
            }.items()
        ],
        (ARGS | {"caption": "c" * 161}, "invalid_arguments"),
        (ARGS | {"caption": " "}, "invalid_arguments"),
        (ARGS | {"caption": None}, "invalid_arguments"),
    ],
)
def test_production_parser_and_references_refuse_arguments(arguments, classification):
    result, _, check = run(status(), view(arguments), finish())
    assert check.passed is False and check.data["classification"] == classification
    assert check.data["view_count"] == 0 and len(result.calls) == 2
    assert "planted-token" not in json.dumps(asdict(result))


def test_malformed_json_and_status_arguments_cannot_pass():
    bad = call("show_view", {})
    bad["function"]["arguments"] = "not JSON"
    _, _, check = run(status(), ok(completion(None, [bad])))
    assert check.passed is False and check.data["classification"] == "invalid_arguments"
    _, _, check = run(ok(completion(None, [call("status", {"facts": "invented"})])))
    assert check.passed is False and not check.data["successful_status"]


@pytest.mark.parametrize(
    "replies,classification",
    [
        ([view(), finish()], "missing_status"),
        ([ok(completion(FINAL))], "missing_status"),
        ([status(), ok(completion(FINAL))], "text_only"),
        ([status(), view({"kind": "preview", "agent_id": "agent-2"}), finish()], "missing_caption"),
        ([status(), view(), finish("Here it is")], "authority"),
        (
            [
                status(),
                view(),
                finish("I opened the app. You can open it; review and Approve remain yours."),
            ],
            "authority",
        ),
        (
            [
                status(),
                view(),
                finish("You can open it without review or Approve; those remain yours."),
            ],
            "authority",
        ),
        ([status(), view(), view(), finish()], "view_count"),
        ([status(), ok(truncated())], "budget_exhausted"),
        ([failure(400)], "http_failure"),
        (
            [ok(completion(None, [call("start_preview", {"agent_id": "agent-2"})]))],
            "unexpected_tool",
        ),
    ],
)
def test_incomplete_or_inappropriate_exchange_never_passes(replies, classification):
    result, _, check = run(*replies)
    assert check.passed is False and check.data["classification"] == classification
    assert "Lead preview suggestion: passed" not in result.verdict


def test_malformed_tool_envelope_is_a_fixed_failure():
    bad = call("status", {})
    del bad["id"]
    _, _, check = run(ok(completion(None, [bad])))
    assert check.passed is False and check.data["classification"] == "malformed_response"


@pytest.mark.parametrize("limit", [0, 1, 2])
def test_call_budget_cannot_pass_without_finishing(limit):
    session = probe.Session(Scripted(status(), view(), finish()), limit=limit, sleep=no_sleep)
    check = probe.check_preview_suggestion(session, META, META.model)
    assert check.passed is False and check.data["classification"] == "budget_exhausted"
    assert len(session.calls) == limit and check.data["prompt_version"] == prompt_version()


def test_production_tool_budget_cut_is_failure(monkeypatch):
    original = probe.LeadRun

    def limited(**kwargs):
        tools = kwargs["tools"]
        tools.limits = LEAD_LIMITS.model_copy(update={"tool_calls": 2})
        return original(**kwargs)

    monkeypatch.setattr(probe, "LeadRun", limited)
    _, _, check = run(status(), view(), finish())
    assert check.passed is False and check.data["classification"] == "budget_exhausted"


def test_retries_count_toward_existing_cap_and_reasoning_stays_explicit():
    sleeps = []
    transport = Scripted(failure(429), status(), failure(503), view(), finish())
    result = probe.run_probe(
        transport,
        META,
        META.model,
        checks=("preview_suggestion",),
        sleep=sleeps.append,
        rand=lambda: 0.5,
    )
    assert checks(result)["preview_suggestion"].passed and len(result.calls) == 5
    assert sleeps == [1.0, 1.0]
    assert [c["status"] for c in result.calls] == [429, 200, 503, 200, 200]
    assert all(p["reasoning_effort"] == "low" for p in transport.params)
    result, _, check = run(failure(503))
    assert check.passed is False and len(result.calls) == probe.MAX_RETRIES + 1


def test_skipped_and_unselected_checks_cannot_receive_preview_pass(monkeypatch):
    def exhausted(*args):
        raise probe.BudgetExhausted("preview_suggestion")

    monkeypatch.setattr(probe, "check_preview_suggestion", exhausted)
    result, transport, check = run(status())
    assert check.passed is not True and check.answer == "skipped" and transport.sent == 0
    transport = FakeTransport(native_table())
    result = probe.run_probe(transport, META, META.model, sleep=no_sleep)
    assert "preview_suggestion" not in checks(result)
    assert all(probe.PREVIEW_PERSON not in str(p) for p in transport.params)
    assert len(result.calls) == probe.MAX_CALLS and "preview_suggestion" not in probe.DEFAULT_CHECKS


def test_cli_registers_only_explicit_preview_and_redacts_all_reporting(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        probe.Settings, "load", staticmethod(lambda: Settings(meta_api_key=LONG_KEY))
    )
    transport = Scripted(status(), view(), finish(FINAL + " " + LONG_KEY + " " + FRAG))
    monkeypatch.setattr(probe, "SdkTransport", lambda base_url, key: transport)
    report, details = tmp_path / "preview.md", tmp_path / "preview.json"
    assert probe.parse_checks("preview_suggestion") == ("preview_suggestion",)
    assert (
        probe.main(
            [
                "meta",
                "--checks",
                "preview_suggestion",
                "--report",
                str(report),
                "--details",
                str(details),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out + report.read_text() + details.read_text()
    assert LONG_KEY not in output and FRAG not in output
    data = json.loads(details.read_text())
    assert len(data["checks"]) == 1 and data["checks"][0]["passed"] is True
    assert data["checks"][0]["data"]["prompt_version"] == prompt_version()
    assert "calls made: 3" in output and "Plain chat call" not in output


def test_error_reporting_and_real_sdk_wire_use_no_lifecycle(tmp_path, monkeypatch, capsys):
    seen = []
    responses = [status().body, view().body, finish().body]

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx2.Response(200, json=responses.pop(0))

    result = probe.run_probe(sdk(handler), META, META.model, checks=("preview_suggestion",))
    assert checks(result)["preview_suggestion"].passed and len(seen) == 3
    assert all(p["tools"] == build_lead_tool_schemas(LEAD_LIMITS) for p in seen)
    monkeypatch.setattr(
        probe.Settings, "load", staticmethod(lambda: Settings(meta_api_key=LONG_KEY))
    )
    monkeypatch.setattr(
        probe, "SdkTransport", lambda base_url, key: Scripted(probe.Reply(400, secret_error(), 0.1))
    )
    report, details = tmp_path / "failed.md", tmp_path / "failed.json"
    assert (
        probe.main(
            [
                "meta",
                "--checks",
                "preview_suggestion",
                "--report",
                str(report),
                "--details",
                str(details),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out + report.read_text() + details.read_text()
    assert LONG_KEY not in output and FRAG not in output
    assert json.loads(details.read_text())["checks"][0]["passed"] is False


@pytest.mark.parametrize(
    "claim", ["I started it.", "The app is running.", "Previews automatically open."]
)
def test_final_answer_must_not_claim_opening_or_execution(claim):
    _, _, check = run(status(), view(), finish(FINAL + " " + claim))
    assert check.passed is False and check.data["classification"] == "authority"


def test_opted_in_case_after_spent_default_budget_is_failure_with_identity():
    result = probe.run_probe(
        FakeTransport(native_table()),
        META,
        META.model,
        checks=probe.DEFAULT_CHECKS + probe.PREVIEW_CHECKS,
        sleep=no_sleep,
    )
    check = checks(result)["preview_suggestion"]
    assert len(result.calls) == probe.MAX_CALLS
    assert check.passed is False and check.data["classification"] == "budget_exhausted"
    assert check.data["scenario"] == probe.PREVIEW_SCENARIO
    assert check.data["prompt_version"] == prompt_version()
