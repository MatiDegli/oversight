"""Real publication needs a complete run and preserves the last good export on refusal."""

import asyncio
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, NoReturn

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import record_run
import recording
from oversight.events import EventStore

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "events"
MISSING = [
    ("graph.snapshot", "the run is missing graph.snapshot"),
    ("change.updated", "the run is missing an agent-authored change.updated"),
    ("user-edit", "the run is missing an agent-authored change.updated"),
    ("change.proposed", "the run is missing change.proposed with passed tests"),
    ("failed-tests", "the run is missing change.proposed with passed tests"),
    ("error-tests", "the run is missing change.proposed with passed tests"),
    ("timeout-tests", "the run is missing change.proposed with passed tests"),
    ("change.resolved", "the run is missing change.resolved approved with merge merged"),
    ("rejected", "the run is missing change.resolved approved with merge merged"),
    ("changes_requested", "the run is missing change.resolved approved with merge merged"),
    ("conflict", "the run is missing change.resolved approved with merge merged"),
    ("graph.delta", "the run is missing graph.delta after a merged approval"),
    ("early-delta", "the run is missing graph.delta after a merged approval"),
]


def rows_for(case: str = "complete") -> list[dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in (FIXTURES / "handwritten-level1.jsonl").read_text().splitlines()
    ]
    rows = [row for row in rows if row["type"] != case]
    for row in rows:
        payload = row["payload"]
        if case == "user-edit" and row["type"] == "change.updated":
            payload["author"] = "user"
        if case.endswith("-tests") and row["type"] == "change.proposed":
            payload["tests"].update(outcome=case.removesuffix("-tests"), passed=0, failed=1)
        if case in ("rejected", "changes_requested") and row["type"] == "change.resolved":
            payload.update(decision=case, merge=None, merge_commit=None)
        if case == "conflict" and row["type"] == "change.resolved":
            payload.update(
                merge="conflict",
                merge_commit=None,
                conflict={"kind": "merge", "paths": ["shop/pricing.py"], "tests": None},
            )
    if case == "early-delta":
        delta = next(row for row in rows if row["type"] == "graph.delta")
        rows.remove(delta)
        approval_index = next(i for i, row in enumerate(rows) if row["type"] == "change.resolved")
        rows.insert(approval_index, delta)
    return rows


def encode(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps({**row, "seq": seq}) + "\n" for seq, row in enumerate(rows, 1))


def write_store(directory: Path, text: str) -> Path:
    events = [recording.SERVER_EVENT.validate_json(line) for line in text.splitlines()]
    first = events[0]
    assert first.type == "run.started"
    run_id = first.payload.run_id
    path = directory / f"{run_id}.sqlite"

    async def build() -> None:
        async with EventStore(path, run_id, mode="replay") as store:
            for event in events:
                await store.publish(event.payload, ts=event.ts)

    asyncio.run(build())
    return path


@pytest.mark.parametrize("entrypoint", ["validate", "export", "build"])
@pytest.mark.parametrize("case,reason", MISSING)
def test_real_publication_requires_each_piece(
    tmp_path: Path, entrypoint: str, case: str, reason: str
) -> None:
    text = encode(rows_for(case))
    out = tmp_path / "recorded.jsonl"
    events = [recording.SERVER_EVENT.validate_json(line) for line in text.splitlines()]
    if entrypoint == "build":
        out.write_text(text)
        # Matching provenance must not let an incomplete recording through the build gate.
        recording.provenance_path(out).write_text(recording.receipt(text, events).model_dump_json())
    store = write_store(tmp_path / "runs", text) if entrypoint == "export" else None
    with pytest.raises(recording.RecordError, match="^" + re.escape(reason) + "$"):
        if entrypoint == "validate":
            recording.validate_text(text)
        elif entrypoint == "export":
            assert store is not None
            record_run.record_run(store, out)
        else:
            recording.load_recording(out)
    if entrypoint == "export":
        assert not out.exists()
        assert not recording.provenance_path(out).exists()


def test_existing_real_recording_passes_unchanged() -> None:
    log = FIXTURES / "recorded-demo.jsonl"
    assert recording.load_recording(log)["text"] == log.read_text()


@pytest.mark.parametrize(
    "failure", ["incomplete", "fake", "absolute-path", "scan", "export", "receipt"]
)
def test_refused_candidate_preserves_last_good_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    out = tmp_path / "recorded.jsonl"
    metadata = recording.provenance_path(out)
    record_run.record_run(write_store(tmp_path / "good", encode(rows_for())), out)
    before = (out.read_bytes(), metadata.read_bytes())
    rows = rows_for("graph.snapshot" if failure == "incomplete" else "complete")
    if failure == "fake":
        rows[0]["payload"]["models"][0]["provider"] = "fake"
    elif failure == "absolute-path":
        step = next(row for row in rows if row["type"] == "agent.step")
        step["payload"]["summary"] = "Read /private/example.py"
    store = write_store(tmp_path / "candidate", encode(rows))

    def refuse(*args: object, **kwargs: object) -> NoReturn:
        if failure == "export":
            raise sqlite3.DatabaseError("unreadable store")
        if failure == "receipt":
            raise OSError("cannot write receipt")
        raise recording.RecordError("secret scan refused candidate")

    targets = {"scan": "scan_secrets", "export": "export_run", "receipt": "receipt"}
    if failure in targets:
        monkeypatch.setattr(record_run, targets[failure], refuse)
    with pytest.raises(recording.RecordError):
        record_run.record_run(store, out)
    assert (out.read_bytes(), metadata.read_bytes()) == before
    assert not list(tmp_path.glob(".record-*"))


def test_passing_candidate_replaces_both_files_only_after_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "recorded.jsonl"
    metadata = recording.provenance_path(out)
    record_run.record_run(write_store(tmp_path / "good", encode(rows_for())), out)
    before = (out.read_bytes(), metadata.read_bytes())
    rows = rows_for()
    rows[0]["payload"]["run_id"] = "run-20261028-000000"
    store = write_store(tmp_path / "candidate", encode(rows))
    scan = record_run.scan_secrets
    scanned: list[Path] = []

    def check_candidate(path: Path) -> None:
        assert path.parent.parent == out.parent
        assert path.parent.name.startswith(".record-")
        assert path.read_bytes() != before[0]
        assert (out.read_bytes(), metadata.read_bytes()) == before
        scan(path)
        scanned.append(path)

    monkeypatch.setattr(record_run, "scan_secrets", check_candidate)
    record_run.record_run(store, out)
    assert len(scanned) == 1
    assert out.read_bytes() != before[0]
    assert metadata.read_bytes() != before[1]
    assert recording.load_recording(out)["text"] == out.read_text()
    assert not list(tmp_path.glob(".record-*"))
