"""Event logs as JSONL: one envelope per line, in `seq` order (docs/proposal.md, rule 5)."""

import json
import re
from collections.abc import Iterable
from pathlib import Path

from pydantic import TypeAdapter

from oversight.events.references import validate_cross_review_limit
from oversight.events.store import StoredEvent, read_store
from oversight.limits import MAX_EVENT_LINE_BYTES, MAX_GRAPH_EVENT_LINE_BYTES
from oversight_contracts.events import ServerEvent

SERVER_EVENT: TypeAdapter[ServerEvent] = TypeAdapter(ServerEvent)
_JSON_STRING = rb'"[^"\\]*(?:\\.[^"\\]*)*"'
_JSON_TOKENS = re.compile(_JSON_STRING + rb"|[{}\[\]]")
_STRING_VALUE = re.compile(_JSON_STRING)
_KEY_SEPARATOR = re.compile(rb"\s*:\s*")
# A JSON character can occupy six bytes as a Unicode escape, plus the surrounding quotes.
_MAX_TYPE_TOKEN_BYTES = 6 * len("graph.snapshot") + 2


class LogError(ValueError):
    """A log line that is not a valid event; the message names the file and the line."""


def _is_graph_event(line: bytes) -> bool:
    """Inspect only the root type; skip payload tokens without decoding their contents.

    Track nesting and consume whole strings so nested keys and quoted JSON cannot select a
    larger ceiling. The last root type wins, matching the contract decoder's duplicate keys.
    Full JSON syntax and schema validation remain the contract decoder's responsibility.
    """
    depth = 0
    kind: str | None = None
    for token in _JSON_TOKENS.finditer(line):
        first = line[token.start()]
        if first in b"{[":
            depth += 1
        elif first in b"}]":
            depth -= 1
        elif depth == 1 and token.end() - token.start() <= _MAX_TYPE_TOKEN_BYTES:
            separator = _KEY_SEPARATOR.match(line, token.end())
            if separator is None:
                continue
            try:
                if json.loads(token.group()) != "type":
                    continue
                value = _STRING_VALUE.match(line, separator.end())
                kind = (
                    json.loads(value.group())
                    if value and value.end() - value.start() <= _MAX_TYPE_TOKEN_BYTES
                    else None
                )
            except ValueError:
                return False
    return kind in ("graph.snapshot", "graph.delta")


def _check_line_size(line: bytes, path: Path, number: int) -> None:
    if len(line) <= MAX_EVENT_LINE_BYTES:
        return
    if len(line) <= MAX_GRAPH_EVENT_LINE_BYTES and _is_graph_event(line):
        return
    raise LogError(f"{path.name}, line {number}: event exceeds size limit")


def read_jsonl(path: Path) -> list[ServerEvent]:
    """Every event of a log, each validated against the contract. Blank lines are skipped."""
    events: list[ServerEvent] = []
    prior: dict[int, ServerEvent] = {}
    with path.open("rb") as handle:
        number = 0
        while line := handle.readline(MAX_GRAPH_EVENT_LINE_BYTES + 2):
            number += 1
            content = line.removesuffix(b"\r\n").removesuffix(b"\n")
            _check_line_size(content, path, number)
            if not line.strip():
                continue
            try:
                event = SERVER_EVENT.validate_json(line)
                validate_cross_review_limit(event, prior)
                events.append(event)
                prior[event.seq] = event
            except ValueError as error:
                raise LogError(f"{path.name}, line {number}: {error}") from error
    return events


def write_jsonl(path: Path, events: Iterable[StoredEvent]) -> int:
    """Write the envelopes as stored, one per line; returns how many were written."""
    count = 0
    with path.open("wb") as out:
        for event in events:
            line = event.envelope.encode("utf-8")
            _check_line_size(line, path, count + 1)
            out.write(line + b"\n")
            count += 1
    return count


def export_run(store_path: Path, out: Path) -> int:
    """Export a run's SQLite file to a JSONL log; returns how many events were written."""
    return write_jsonl(out, read_store(store_path))
