"""Probe SDK transport, redaction, reports and CLI."""

import json
import tomllib
from pathlib import Path

import httpx2  # the SDK's HTTP client library: its MockTransport stands in for the network
import openai
import pytest
from tools.tests.probe_api_helpers import (
    FRAG,
    KEY,
    LONG_KEY,
    META,
    PARAMS,
    FakeTransport,
    Obj,
    completion,
    native_table,
    sdk,
    secret_error,
)

import probe_api as probe
from oversight.settings import Settings


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
    assert "Not probed in this report: whether `meta-models/muse-spark-open-1026`" in text
    assert "still unknown" not in text.lower() and "which slug" not in text
    assert "tools/probe_api.py baseten` once `BASETEN_API_KEY` is set" in text
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
