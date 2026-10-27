"""Publication checks shared by the run exporter and the static replay build."""

import asyncio
import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from oversight.events import absolute_paths
from oversight.workspace import run_process
from oversight_contracts.common import RunId
from oversight_contracts.events import ServerEvent

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_EVENT = TypeAdapter(ServerEvent)


class RecordError(Exception):
    """The one-line reason recording a run was refused."""


class Provenance(BaseModel):
    """An exporter receipt, bound to the exact JSONL bytes; not an event or a signature."""

    model_config = ConfigDict(extra="forbid")
    exporter: Literal["oversight.record_run.v1", "oversight.fake_recording.v1"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: RunId
    event_count: int = Field(ge=1)


def provenance_path(log: Path) -> Path:
    return log.with_name(log.name + ".provenance.json")


def validate_text(text: str, *, fake_fixture: bool = False) -> list[ServerEvent]:
    """Validate every line against the unchanged contracts, then enforce publication rules."""
    events: list[ServerEvent] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = SERVER_EVENT.validate_json(line)
        except ValueError:
            raise RecordError(f"invalid event at line {number}") from None
        if event.seq != len(events) + 1:
            raise RecordError(f"event sequence gap at line {number}")
        if absolute_paths(json.loads(line)):
            raise RecordError("an absolute path appears in the log")
        events.append(event)
    if not events or events[0].type != "run.started":
        raise RecordError("the log must start with run.started")
    allow_fake_provider = fake_fixture
    for event in events:
        if (
            event.type == "change.reviewed"
            and event.payload.reviewer_provider == "fake"
            and not allow_fake_provider
        ):
            raise RecordError("fake review provider requires explicit fixture authorization")
        if event.type == "run.started":
            if not event.payload.models or any(
                (model.provider != "fake" if fake_fixture else model.provider == "fake")
                or not model.model.strip()
                for model in event.payload.models
            ):
                raise RecordError(
                    "the run has an empty model or the wrong provider for fake fixture mode"
                    if fake_fixture
                    else "the run used an empty or scripted fake model, not a real model"
                )
            if event.seq != 1:
                raise RecordError("the log contains another run.started")
    return events


def receipt(text: str, events: list[ServerEvent], *, fake_fixture: bool = False) -> Provenance:
    first = events[0]
    assert first.type == "run.started"
    return Provenance(
        exporter="oversight.fake_recording.v1" if fake_fixture else "oversight.record_run.v1",
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        run_id=first.payload.run_id,
        event_count=len(events),
    )


def load_recording(log: Path, *, fake_fixture: bool = False) -> dict[str, object]:
    """The build gate: require the exporter receipt and revalidate the exact bundled text."""
    try:
        text = log.read_bytes().decode("utf-8")
        provenance = Provenance.model_validate_json(provenance_path(log).read_bytes())
        events = validate_text(text, fake_fixture=fake_fixture)
        if provenance != receipt(text, events, fake_fixture=fake_fixture):
            raise RecordError("exporter provenance does not match the recorded log")
        # Recheck secrets at build time too, in case scanning rules have changed.
        with TemporaryDirectory(prefix="oversight-bundle-") as scratch:
            snapshot = Path(scratch) / "events.jsonl"
            snapshot.write_text(text, encoding="utf-8", newline="\n")
            scan_secrets(snapshot)
        return {"text": text, "provenance": provenance.model_dump(mode="json")}
    except (OSError, ValueError):
        raise RecordError("recorded log or exporter provenance is missing or invalid") from None


def strings(value: object) -> Iterator[str]:
    """Decode strings too: JSON escaping must not hide credentials inside file contents."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in cast(dict[str, object], value).items():
            yield key
            yield from strings(item)
    elif isinstance(value, list):
        for item in cast(list[object], value):
            yield from strings(item)


def scan_secrets(path: Path) -> None:
    """Scan the exact export and decoded strings, failing closed if gitleaks cannot run."""
    text = path.read_text(encoding="utf-8")
    decoded = [
        string for line in text.splitlines() if line.strip() for string in strings(json.loads(line))
    ]
    with TemporaryDirectory(prefix="oversight-secrets-") as scratch:
        directory = Path(scratch)
        (directory / "export.txt").write_text(text + "\n" + "\n".join(decoded), encoding="utf-8")
        try:
            result = asyncio.run(
                run_process(
                    [
                        "gitleaks",
                        "dir",
                        "--config",
                        str(REPO_ROOT / ".gitleaks.toml"),
                        "--redact",
                        "--no-banner",
                        "--log-level",
                        "error",
                        "--ignore-gitleaks-allow",
                        "--gitleaks-ignore-path",
                        "/dev/null",
                        str(directory),
                    ],
                    cwd=directory,
                    timeout_s=30,
                )
            )
        except OSError:
            raise RecordError(
                "secret scan unavailable: install gitleaks before recording"
            ) from None
        if result.timed_out or result.returncode != 0:
            # Never include scanner output: it can contain the very secret being refused.
            raise RecordError("secret scan failed or found a credential; recording refused")
