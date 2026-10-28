"""Event logs as JSONL: one envelope per line, in `seq` order (docs/proposal.md, rule 5)."""

from collections.abc import Iterable
from pathlib import Path

from pydantic import TypeAdapter

from oversight.events.store import StoredEvent, read_store
from oversight.limits import MAX_EVENT_LINE_BYTES
from oversight_contracts.events import ServerEvent

SERVER_EVENT: TypeAdapter[ServerEvent] = TypeAdapter(ServerEvent)


class LogError(ValueError):
    """A log line that is not a valid event; the message names the file and the line."""


def read_jsonl(path: Path) -> list[ServerEvent]:
    """Every event of a log, each validated against the contract. Blank lines are skipped."""
    events: list[ServerEvent] = []
    with path.open("rb") as handle:
        number = 0
        while line := handle.readline(MAX_EVENT_LINE_BYTES + 2):
            number += 1
            content = line.removesuffix(b"\r\n").removesuffix(b"\n")
            if len(content) > MAX_EVENT_LINE_BYTES:
                raise LogError(f"{path.name}, line {number}: event exceeds size limit")
            if not line.strip():
                continue
            try:
                events.append(SERVER_EVENT.validate_json(line))
            except ValueError as error:
                raise LogError(f"{path.name}, line {number}: {error}") from error
    return events


def write_jsonl(path: Path, events: Iterable[StoredEvent]) -> int:
    """Write the envelopes as stored, one per line; returns how many were written."""
    count = 0
    with path.open("wb") as out:
        for event in events:
            line = event.envelope.encode("utf-8")
            if len(line) > MAX_EVENT_LINE_BYTES:
                raise LogError(f"{path.name}, line {count + 1}: event exceeds size limit")
            out.write(line + b"\n")
            count += 1
    return count


def export_run(store_path: Path, out: Path) -> int:
    """Export a run's SQLite file to a JSONL log; returns how many events were written."""
    return write_jsonl(out, read_store(store_path))
