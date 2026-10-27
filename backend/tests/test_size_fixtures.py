"""Approved ceilings accept the demo, fake scripts and every replay fixture."""

import json
from pathlib import Path

from oversight.agents import load_script
from oversight.events import read_jsonl
from oversight.limits import MAX_BODY_BYTES, MAX_FILE_BYTES, bounded_text
from oversight.settings import REPO_ROOT
from oversight_contracts.events import ChangeUpdatedEvent


def test_all_legitimate_files_and_embedded_changes_fit() -> None:
    for directory in ("fixtures", "demo-repo"):
        paths = [path for path in (REPO_ROOT / directory).rglob("*") if path.is_file()]
        assert paths
        for path in paths:
            assert path.stat().st_size <= MAX_FILE_BYTES, path
            bounded_text(path)
    for path in (REPO_ROOT / "fixtures/fake-model").glob("*.json"):
        script = load_script(path)
        assert len(json.dumps({"task": script.task}).encode()) <= MAX_BODY_BYTES
        for turn in script.turns:
            for call in turn.tool_calls:
                assert len(call.arguments.encode()) <= MAX_BODY_BYTES
    for path in (REPO_ROOT / "fixtures/events").glob("*.jsonl"):
        for event in read_jsonl(path):
            if isinstance(event, ChangeUpdatedEvent):
                for content in (event.payload.file.original, event.payload.file.modified):
                    assert content is None or len(content.encode()) <= MAX_FILE_BYTES, path


def test_replay_log_total_can_exceed_file_and_body_limits(tmp_path: Path) -> None:
    source = REPO_ROOT / "fixtures/events/handwritten-level1.jsonl"
    events = read_jsonl(source)
    log = tmp_path / "long.jsonl"
    copies = MAX_BODY_BYTES // source.stat().st_size + 1
    log.write_bytes(source.read_bytes() * copies)
    assert log.stat().st_size > MAX_BODY_BYTES
    assert len(read_jsonl(log)) == len(events) * copies
