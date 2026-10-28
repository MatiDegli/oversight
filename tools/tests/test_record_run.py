"""tools/record_run.py: export a tiny store, and refuse fake, tainted and mis-ordered runs."""

import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import record_run
import recording
import validate_recording
from oversight.events import EventPayload, EventStore, read_jsonl
from oversight.workspace import ProcessResult
from oversight_contracts.events import (
    AgentStep,
    ChangeResolved,
    ChangeUpdated,
    ModelProfile,
    RunStarted,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "events"
RUN_ID = "run-20261027-140312"
FAKE = ModelProfile(
    id="fake", label="Scripted fake model", provider="fake", model="fake-scripted", reasoning=None
)


def started(run_id: str = RUN_ID, models: list[ModelProfile] | None = None) -> RunStarted:
    payload = read_jsonl(FIXTURES / "handwritten-level1.jsonl")[0].payload
    assert isinstance(payload, RunStarted)
    return payload.model_copy(update={"run_id": run_id, **({"models": models} if models else {})})


def step(summary: str = "Read shop/pricing.py") -> AgentStep:
    return AgentStep(
        agent_id="agent-1",
        step=1,
        tool="read_file",
        summary=summary,
        result="ok",
        error=None,
        duration_ms=1,
        tests=None,
    )


def write_store(path: Path, run_id: str, events: list[tuple[EventPayload, str]]) -> Path:
    async def build() -> None:
        async with EventStore(path, run_id, mode="replay") as store:
            for payload, ts in events:
                await store.publish(payload, ts=ts)

    asyncio.run(build())
    return path


def real_store(tmp_path: Path, run_id: str = RUN_ID, *, extra: EventPayload | None = None) -> Path:
    payloads: list[EventPayload] = [started(run_id), step()]
    payloads.extend(
        event.payload
        for event in read_jsonl(FIXTURES / "handwritten-level1.jsonl")
        if event.type
        in ("graph.snapshot", "change.updated", "change.proposed", "change.resolved", "graph.delta")
    )
    if extra is not None:
        payloads.append(extra)
    return write_store(
        tmp_path / "runs" / f"{run_id}.sqlite",
        run_id,
        [
            (payload, "2026-10-27T14:03:12.000Z" if index == 0 else "2026-10-27T14:03:13.000Z")
            for index, payload in enumerate(payloads)
        ],
    )


def test_exports_a_complete_store(tmp_path: Path) -> None:
    store = real_store(tmp_path)
    out = tmp_path / "recorded.jsonl"
    exported = record_run.record_run(store, out)
    assert exported.count == 7
    assert exported.model == "muse-spark"
    assert exported.first_ts == "2026-10-27T14:03:12.000Z"
    assert exported.last_ts == "2026-10-27T14:03:13.000Z"
    assert [event.type for event in read_jsonl(out)] == [
        "run.started",
        "agent.step",
        "graph.snapshot",
        "change.updated",
        "change.proposed",
        "change.resolved",
        "graph.delta",
    ]
    bundled = recording.load_recording(out)
    assert bundled["text"] == out.read_text()
    assert bundled["provenance"] == {
        "exporter": "oversight.record_run.v1",
        "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
        "run_id": RUN_ID,
        "event_count": 7,
    }


@pytest.mark.parametrize(
    "path", ["/etc/private/demo.py", "//server/private/demo.py", r"\\server\private\demo.py"]
)
def test_refuses_an_absolute_path_without_creating_output(tmp_path: Path, path: str) -> None:
    store = write_store(
        tmp_path / "runs" / f"{RUN_ID}.sqlite",
        RUN_ID,
        [
            (started(), "2026-10-27T14:03:12.000Z"),
            (step(f"failed in {path}"), "2026-10-27T14:03:13.000Z"),
        ],
    )
    out = tmp_path / "recorded.jsonl"
    with pytest.raises(record_run.RecordError, match="absolute path"):
        record_run.record_run(store, out)
    assert not out.exists()


def test_refuses_a_fake_run_without_creating_output(tmp_path: Path) -> None:
    store = write_store(
        tmp_path / "runs" / f"{RUN_ID}.sqlite",
        RUN_ID,
        [
            (started(models=[FAKE]), "2026-10-27T14:03:12.000Z"),
            (step(), "2026-10-27T14:03:13.000Z"),
        ],
    )
    out = tmp_path / "recorded.jsonl"
    with pytest.raises(record_run.RecordError, match="fake"):
        record_run.record_run(store, out)
    assert not out.exists()


def test_refuses_a_log_that_does_not_start_with_run_started(tmp_path: Path) -> None:
    store = write_store(
        tmp_path / "runs" / f"{RUN_ID}.sqlite",
        RUN_ID,
        [(step(), "2026-10-27T14:03:12.000Z")],
    )
    out = tmp_path / "recorded.jsonl"
    with pytest.raises(record_run.RecordError, match=r"run\.started"):
        record_run.record_run(store, out)
    assert not out.exists()


def test_latest_picks_the_newest_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    older = real_store(tmp_path / "old", "run-20261027-000000")
    newer = real_store(tmp_path / "new", "run-20261028-000000")
    runs = tmp_path / "runs"
    runs.mkdir()
    older = older.rename(runs / older.name)
    newer = newer.rename(runs / newer.name)
    os.utime(older, (1_000, 1_000))
    os.utime(newer, (2_000, 2_000))
    assert record_run.latest_store(runs) == newer
    assert record_run.store_for(None, latest=True, runtime_dir=tmp_path) == newer

    monkeypatch.setattr(record_run, "RUNTIME_DIR", tmp_path)
    out = tmp_path / "recorded.jsonl"
    assert record_run.main(["--latest", "--out", str(out)]) == 0
    assert len(read_jsonl(out)) == 7


def test_main_records_a_named_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = real_store(tmp_path)
    runtime = tmp_path / ".runtime"
    target = runtime / "runs" / store.name
    target.parent.mkdir(parents=True)
    store.rename(target)
    monkeypatch.setattr(record_run, "RUNTIME_DIR", runtime)
    out = tmp_path / "recorded.jsonl"
    assert record_run.main([RUN_ID, "--out", str(out)]) == 0
    assert out.exists()
    assert record_run.main(["run-missing", "--out", str(tmp_path / "x.jsonl")]) == 1


@pytest.mark.parametrize("alias", ["same", "symlink", "hardlink"])
def test_output_cannot_replace_source_store(tmp_path: Path, alias: str) -> None:
    store = real_store(tmp_path)
    before = store.read_bytes()
    out = store if alias == "same" else tmp_path / "out.jsonl"
    if alias == "symlink":
        out.symlink_to(store)
    elif alias == "hardlink":
        out.hardlink_to(store)
    with pytest.raises(record_run.RecordError, match="source"):
        record_run.record_run(store, out)
    assert store.read_bytes() == before
    assert out.read_bytes() == before


@pytest.mark.parametrize("run_id", ["../../outside", "/outside/run", "run-missing", RUN_ID + "\n"])
def test_store_for_refuses_invalid_run_ids(tmp_path: Path, run_id: str) -> None:
    with pytest.raises(record_run.RecordError, match="run id"):
        record_run.store_for(run_id, latest=False, runtime_dir=tmp_path)


@pytest.mark.parametrize("latest", [False, True])
def test_store_for_refuses_links_outside_runs(tmp_path: Path, latest: bool) -> None:
    outside = real_store(tmp_path / "outside")
    runtime = tmp_path / ".runtime"
    runs = runtime / "runs"
    runs.mkdir(parents=True)
    (runs / outside.name).symlink_to(outside)
    before = outside.read_bytes()
    with pytest.raises(record_run.RecordError, match="outside"):
        record_run.store_for(None if latest else RUN_ID, latest=latest, runtime_dir=runtime)
    assert outside.read_bytes() == before


def test_store_for_refuses_a_linked_runs_directory(tmp_path: Path) -> None:
    outside = real_store(tmp_path / "outside")
    runtime = tmp_path / ".runtime"
    runtime.mkdir()
    (runtime / "runs").symlink_to(outside.parent, target_is_directory=True)
    with pytest.raises(record_run.RecordError, match="outside"):
        record_run.store_for(RUN_ID, latest=False, runtime_dir=runtime)


@pytest.mark.parametrize("location", ["summary", "comment", "contents"])
@pytest.mark.parametrize("prefix,length", [("ghp_", 36), ("AKIA", 16)])
def test_refuses_credentials_before_publishing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], location: str, prefix: str, length: int
) -> None:
    # Synthetic, assembled here so the repository contains no credential-shaped literal.
    secret = prefix + ("A1b2C3d4E5f6G7h8J9k0L1m2N3p4Q5r6S7t8"[:length])
    if prefix == "AKIA":
        secret = secret.upper()
    payload: EventPayload = step(f"token={secret}")
    events = read_jsonl(FIXTURES / "handwritten-level1.jsonl")
    if location == "comment":
        resolved = next(e.payload for e in events if isinstance(e.payload, ChangeResolved))
        payload = resolved.model_copy(update={"comment": f"token={secret}"})
    elif location == "contents":
        updated = next(e.payload for e in events if isinstance(e.payload, ChangeUpdated))
        file = updated.file.model_copy(update={"modified": f'api_key = "{secret}"\n'})
        payload = updated.model_copy(update={"file": file})
    store = real_store(tmp_path, extra=payload)
    before = store.read_bytes()
    out = tmp_path / "recorded.jsonl"
    with pytest.raises(record_run.RecordError, match="secret") as error:
        record_run.record_run(store, out)
    assert secret not in str(error.value)
    assert secret not in str(capsys.readouterr())
    assert not out.exists()
    assert store.read_bytes() == before
    assert not list(tmp_path.glob(".record-*"))


@pytest.mark.parametrize("failure", ["missing", "timeout", "error"])
def test_secret_scanner_fails_closed_before_output_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    out = tmp_path / "recorded.jsonl"

    async def failed_scan(*args: object, **kwargs: object) -> ProcessResult:
        assert not out.exists()
        if failure == "missing":
            raise FileNotFoundError("gitleaks")
        return ProcessResult(2, "", "", failure == "timeout", False, 1)

    monkeypatch.setattr(recording, "run_process", failed_scan)
    with pytest.raises(record_run.RecordError, match="secret scan"):
        record_run.record_run(real_store(tmp_path), out)
    assert not out.exists()


def write_marker(log: Path, run_id: str = RUN_ID) -> None:
    recording.provenance_path(log).write_text(
        json.dumps(
            {
                "exporter": "oversight.record_run.v1",
                "sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
                "run_id": run_id,
                "event_count": len(log.read_text().splitlines()),
            }
        )
    )


@pytest.mark.parametrize("text", ["not JSON", (FIXTURES / "handwritten-level1.jsonl").read_text()])
def test_build_gate_refuses_text_without_exporter_provenance(tmp_path: Path, text: str) -> None:
    log = tmp_path / "recorded.jsonl"
    log.write_text(text)
    with pytest.raises(recording.RecordError, match="provenance"):
        recording.load_recording(log)


@pytest.mark.parametrize("case", ["fake", "invalid-json", "invalid-payload", "gap", "empty-models"])
def test_build_gate_validates_every_line_even_with_a_matching_marker(
    tmp_path: Path, case: str
) -> None:
    log = tmp_path / "recorded.jsonl"
    record_run.record_run(real_store(tmp_path), log)
    events = [json.loads(line) for line in log.read_text().splitlines()]
    if case == "fake":
        events[0]["payload"]["models"][0]["provider"] = "fake"
    elif case == "invalid-payload":
        events[1]["payload"]["duration_ms"] = "invalid"
    elif case == "empty-models":
        events[0]["payload"]["models"] = []
    elif case == "gap":
        events[1]["seq"] = 3
    log.write_text("\n".join(json.dumps(event) for event in events) + "\n")
    if case == "invalid-json":
        with log.open("a") as stream:
            stream.write("not JSON\n")
    write_marker(log)
    with pytest.raises(recording.RecordError):
        recording.load_recording(log)


@pytest.mark.parametrize("case", ["modified", "wrong-run", "wrong-count", "missing-exporter"])
def test_build_gate_binds_provenance_to_exported_bytes(tmp_path: Path, case: str) -> None:
    log = tmp_path / "recorded.jsonl"
    record_run.record_run(real_store(tmp_path), log)
    marker = recording.provenance_path(log)
    metadata = json.loads(marker.read_text())
    if case == "modified":
        log.write_text(log.read_text().replace("Read shop/pricing.py", "Changed summary"))
    elif case == "wrong-run":
        metadata["run_id"] = "run-20261028-000000"
    elif case == "wrong-count":
        metadata["event_count"] = 99
    else:
        del metadata["exporter"]
    marker.write_text(json.dumps(metadata))
    with pytest.raises(recording.RecordError, match="provenance"):
        recording.load_recording(log)


def test_provenance_output_cannot_replace_source(tmp_path: Path) -> None:
    store = real_store(tmp_path)
    before = store.read_bytes()
    out = tmp_path / "recorded.jsonl"
    recording.provenance_path(out).symlink_to(store)
    with pytest.raises(record_run.RecordError, match="source"):
        record_run.record_run(store, out)
    assert store.read_bytes() == before
    assert not out.exists()


def test_fake_fixture_roundtrip_and_public_refusal(tmp_path: Path) -> None:
    store = write_store(
        tmp_path / f"{RUN_ID}.sqlite",
        RUN_ID,
        [
            (started(models=[FAKE]), "2026-10-27T14:03:12.000Z"),
            (step(), "2026-10-27T14:03:13.000Z"),
        ],
    )
    out = tmp_path / "fake.jsonl"
    assert record_run.record_run(store, out, fake_fixture=True).count == 2
    bundle = recording.load_recording(out, fake_fixture=True)
    provenance = bundle["provenance"]
    assert isinstance(provenance, dict)
    assert provenance["exporter"] == "oversight.fake_recording.v1"
    assert validate_recording.main(["--fake-fixture", str(out)]) == 0
    assert validate_recording.main([str(out)]) == 1
    with pytest.raises(recording.RecordError, match="fake"):
        recording.load_recording(out)
    marker = recording.provenance_path(out)
    metadata = json.loads(marker.read_text())
    metadata["exporter"] = "oversight.record_run.v1"
    marker.write_text(json.dumps(metadata))
    with pytest.raises(recording.RecordError):
        recording.load_recording(out, fake_fixture=True)
    with pytest.raises(recording.RecordError):
        recording.load_recording(out)


def test_fake_mode_refuses_real_model(tmp_path: Path) -> None:
    out = tmp_path / "fake.jsonl"
    with pytest.raises(recording.RecordError, match="provider"):
        record_run.record_run(real_store(tmp_path), out, fake_fixture=True)
    assert not out.exists()


def test_fake_review_authorization_is_independent_of_run_model() -> None:
    rows = [
        json.loads(line)
        for line in (FIXTURES / "handwritten-level1.jsonl").read_text().splitlines()
    ]
    fixture = FIXTURES.parent / "contracts/reserved-1.4.json"
    review = next(
        row for row in json.loads(fixture.read_text()) if row["type"] == "change.reviewed"
    )
    review["seq"] = len(rows) + 1
    rows.append(review)
    text = "\n".join(json.dumps(row) for row in rows)
    with pytest.raises(recording.RecordError, match="fake review"):
        recording.validate_text(text)
    rows[0]["payload"]["models"] = [FAKE.model_dump(mode="json")]
    text = "\n".join(json.dumps(row) for row in rows)
    assert recording.validate_text(text, fake_fixture=True)[-1].type == "change.reviewed"


@pytest.mark.parametrize("same_review", [False, True])
def test_recording_refuses_second_cross_review_resolution(same_review: bool) -> None:
    rows = [
        json.loads(line)
        for line in (FIXTURES / "handwritten-level1.jsonl").read_text().splitlines()
    ]
    reserved = json.loads((FIXTURES.parent / "contracts/reserved-1.4.json").read_text())
    review = next(row for row in reserved if row["type"] == "change.reviewed")
    rows[0]["payload"]["models"] = [FAKE.model_dump(mode="json")]
    resolved = next(row for row in reserved if row["type"] == "change.resolved")
    resolved["payload"].update(
        decision="changes_requested",
        decided_by="cross_review",
        reason=None,
        comment="Fix this",
        head_commit=review["payload"]["head_commit"],
        merge=None,
        merge_commit=None,
        conflict=None,
        assessment=None,
        reasons=[],
    )
    review["seq"] = len(rows) + 1
    resolved["seq"] = len(rows) + 2
    resolved["payload"]["review_seq"] = review["seq"]
    duplicate = json.loads(json.dumps(resolved))
    duplicate["seq"] = len(rows) + 3
    if same_review:
        duplicate["payload"]["change_id"] = "change-99"
    else:
        duplicate["payload"]["review_seq"] += 10
    text = "\n".join(json.dumps(row) for row in [*rows, review, resolved, duplicate])
    with pytest.raises(recording.RecordError, match="only one cross_review"):
        recording.validate_text(text, fake_fixture=True)
