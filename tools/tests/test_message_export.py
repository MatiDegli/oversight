"""Publication withholds human text unless the command and receipt explicitly include it."""

import json
from pathlib import Path

import pytest
from tools.tests.test_record_run import RUN_ID, real_store

import record_run
import recording
from oversight.events import read_jsonl
from oversight_contracts.events import AgentMessageSent


@pytest.mark.parametrize("include", [False, True])
def test_export_choice_and_receipt(tmp_path: Path, include: bool) -> None:
    payload = AgentMessageSent(agent_id="agent-1", text="Please inspect [redacted].")
    store = real_store(tmp_path, extra=payload)
    out = tmp_path / "events.jsonl"
    record_run.record_run(store, out, include_message_text=include)
    message = read_jsonl(out)[-1]
    assert message.type == "agent.message"
    assert message.payload.text == (payload.text if include else None)
    bundle = recording.load_recording(out)
    provenance = bundle["provenance"]
    assert isinstance(provenance, dict)
    assert provenance["message_text"] == ("included" if include else "withheld")


@pytest.mark.parametrize("choice", [None, "withheld"])
def test_build_refuses_text_without_included_receipt(tmp_path: Path, choice: str | None) -> None:
    store = real_store(tmp_path, extra=AgentMessageSent(agent_id="agent-1", text="Please inspect."))
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
