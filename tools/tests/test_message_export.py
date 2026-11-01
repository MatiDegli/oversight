"""Publication withholds human text unless the command and receipt explicitly include it."""

import json
from pathlib import Path

import pytest
from tools.tests.test_record_run import RUN_ID, real_store

import record_run
import recording
from oversight.events import read_jsonl, read_store
from oversight_contracts.events import AgentMessageSent, LeadReply


def payload_for(kind: str) -> AgentMessageSent | LeadReply:
    if kind == "agent.message":
        return AgentMessageSent(agent_id="agent-1", text="Please inspect [redacted].")
    return LeadReply(
        agent_id="agent-1",
        answers_seq=None,
        text="Please inspect [redacted].",
        through_seq=None,
        cut=None,
    )


@pytest.mark.parametrize("kind", ["agent.message", "lead.reply"])
@pytest.mark.parametrize("include", [False, True])
def test_export_choice_and_receipt(tmp_path: Path, include: bool, kind: str) -> None:
    payload = payload_for(kind)
    store = real_store(tmp_path, extra=payload)
    out = tmp_path / "events.jsonl"
    source = [row.envelope for row in read_store(store)]
    record_run.record_run(store, out, include_message_text=include)
    message = json.loads(out.read_text().splitlines()[-1])
    assert message["type"] == kind
    assert message["payload"]["text"] == (payload.text if include else None)
    bundle = recording.load_recording(out)
    provenance = bundle["provenance"]
    assert isinstance(provenance, dict)
    assert provenance["message_text"] == ("included" if include else "withheld")
    events = recording.validate_text(str(bundle["text"]), include_message_text=include)
    assert events[-1].payload.model_dump()["text"] == (payload.text if include else None)
    assert [row.envelope for row in read_store(store)] == source
    assert json.loads(source[-1])["payload"]["text"] == payload.text


@pytest.mark.parametrize("kind", ["agent.message", "lead.reply"])
@pytest.mark.parametrize("choice", [None, "withheld"])
def test_build_refuses_text_without_included_receipt(
    tmp_path: Path,
    choice: str | None,
    kind: str,
) -> None:
    store = real_store(tmp_path, extra=payload_for(kind))
    out = tmp_path / "events.jsonl"
    record_run.record_run(store, out, include_message_text=True)
    marker = recording.provenance_path(out)
    metadata = json.loads(marker.read_text())
    if choice is None:
        del metadata["message_text"]
    else:
        metadata["message_text"] = choice
    marker.write_text(json.dumps(metadata))
    with pytest.raises(recording.RecordError, match="message text"):
        recording.load_recording(out)


def test_old_receipt_defaults_to_withheld(tmp_path: Path) -> None:
    store = real_store(tmp_path)
    out = tmp_path / "events.jsonl"
    record_run.record_run(store, out)
    marker = recording.provenance_path(out)
    metadata = json.loads(marker.read_text())
    del metadata["message_text"]
    marker.write_text(json.dumps(metadata))
    assert recording.load_recording(out)["text"] == out.read_text()


def test_cli_include_message_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_store(tmp_path, extra=AgentMessageSent(agent_id="agent-1", text="Please inspect."))
    monkeypatch.setattr(record_run, "RUNTIME_DIR", tmp_path)
    out = tmp_path / "events.jsonl"
    assert record_run.main([RUN_ID, "--out", str(out), "--include-message-text"]) == 0
    assert read_jsonl(out)[-1].payload.model_dump()["text"] == "Please inspect."


def test_withheld_reply_requires_receipt_and_cannot_be_live_or_included(tmp_path: Path) -> None:
    from pydantic import TypeAdapter, ValidationError

    from oversight_contracts.events import ServerEvent

    store = real_store(tmp_path, extra=payload_for("lead.reply"))
    before = store.read_bytes()
    out = tmp_path / "events.jsonl"
    record_run.record_run(store, out)
    text = out.read_text()
    raw_reply = text.splitlines()[-1]
    for context in (None, {"live": True}):
        with pytest.raises(ValidationError, match="null reply text"):
            TypeAdapter(ServerEvent).validate_json(raw_reply, context=context)
    with pytest.raises(recording.RecordError, match="invalid event"):
        recording.validate_text(text, include_message_text=True)
    assert recording.load_recording(out)["text"] == text
    marker = recording.provenance_path(out)
    metadata = marker.read_bytes()
    marker.unlink()
    with pytest.raises(recording.RecordError, match="provenance"):
        recording.load_recording(out)
    marker.write_bytes(metadata)
    assert recording.load_recording(out)["text"] == text
    assert store.read_bytes() == before


@pytest.mark.parametrize("cut", ["tool_calls", "model_calls", "time", "spend_brake"])
def test_null_text_cut_exports_with_either_authorization(tmp_path: Path, cut: str) -> None:
    payload = LeadReply.model_validate(
        dict(
            agent_id="agent-1",
            answers_seq=None,
            text=None,
            through_seq=None,
            cut=cut,
        )
    )
    store = real_store(tmp_path, extra=payload)
    for include in (False, True):
        out = tmp_path / f"{include}.jsonl"
        record_run.record_run(store, out, include_message_text=include)
        assert recording.load_recording(out)["text"] == out.read_text()
