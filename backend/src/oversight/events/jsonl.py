"""Event logs as JSONL: one envelope per line, in `seq` order (docs/proposal.md, rule 5)."""

from collections.abc import Iterable
from pathlib import Path

from pydantic import TypeAdapter

from oversight.events.store import StoredEvent, read_store
from oversight_contracts.events import ServerEvent

SERVER_EVENT: TypeAdapter[ServerEvent] = TypeAdapter(ServerEvent)


class LogError(ValueError):
    """A log line that is not a valid event; the message names the file and the line."""


def read_jsonl(path: Path) -> list[ServerEvent]:
    """Every event of a log, each validated against the contract. Blank lines are skipped."""
    events: list[ServerEvent] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
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
    with path.open("w", encoding="utf-8", newline="\n") as out:
        for event in events:
            out.write(event.envelope + "\n")
            count += 1
    return count


def export_run(store_path: Path, out: Path) -> int:
    """Export a run's SQLite file to a JSONL log; returns how many events were written."""
    return write_jsonl(out, read_store(store_path))
