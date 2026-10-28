"""Exported event lines obey the same byte boundary as imported lines."""

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from oversight.events.jsonl import SERVER_EVENT, LogError, read_jsonl, write_jsonl
from oversight.events.store import StoredEvent
from oversight.limits import MAX_EVENT_LINE_BYTES, MAX_GRAPH_EVENT_LINE_BYTES

GRAPH_PAYLOADS = {
    "graph.snapshot": {
        "version": "v0",
        "commit": "c0",
        "nodes": [],
        "edges": [],
        "parse_errors": [],
        "positions": {},
    },
    "graph.delta": {
        "from_version": "v0",
        "to_version": "v1",
        "commit": "c1",
        "cause": "rescan",
        "nodes_added": [],
        "nodes_changed": [],
        "nodes_removed": [],
        "edges_added": [],
        "edges_changed": [],
        "edges_removed": [],
        "parse_errors": [],
    },
}


@pytest.mark.parametrize("extra", [0, 1])
@pytest.mark.parametrize("newline", [b"", b"\n", b"\r\n"])
@pytest.mark.parametrize(
    ("event_type", "limit"),
    [
        ("error", MAX_EVENT_LINE_BYTES),
        *[(kind, MAX_GRAPH_EVENT_LINE_BYTES) for kind in GRAPH_PAYLOADS],
    ],
)
def test_event_line_byte_boundary_in_both_directions(
    tmp_path: Path, extra: int, newline: bytes, event_type: str, limit: int
) -> None:
    # Multibyte text catches accidental character limits; JSON whitespace fills the line exactly.
    envelope = (
        '{"seq":1,"ts":"2026-10-27T14:00:00.000Z","type":"error",'
        '"payload":{"source":"server","message":"é","fatal":false,'
        '"agent_id":null,"change_id":null}}'
    )
    if event_type in GRAPH_PAYLOADS:
        envelope = json.dumps(
            {
                "seq": 1,
                "ts": "2026-10-27T14:00:00.000Z",
                "type": event_type,
                "payload": GRAPH_PAYLOADS[event_type],
            }
        )
    envelope += " " * (limit + extra - len(envelope.encode("utf-8")))
    event = StoredEvent(1, "2026-10-27T14:00:00.000Z", event_type, envelope)
    source, target = tmp_path / "input.jsonl", tmp_path / "export.jsonl"
    source.write_bytes(envelope.encode("utf-8") + newline)
    if extra:
        with pytest.raises(LogError, match=r"input.jsonl, line 1: event exceeds size limit"):
            read_jsonl(source)
        with pytest.raises(LogError, match=r"export.jsonl, line 1: event exceeds size limit"):
            write_jsonl(target, [event])
        assert target.read_bytes() == b""
    else:
        assert len(read_jsonl(source)) == 1
        assert write_jsonl(target, [event]) == 1
        assert target.read_bytes() == envelope.encode("utf-8") + b"\n"
        assert read_jsonl(target) == read_jsonl(source)


@pytest.mark.parametrize("event_type", GRAPH_PAYLOADS)
@pytest.mark.parametrize("direction", ["read", "write"])
@pytest.mark.parametrize("header", ["plain", "escaped", "duplicate"])
def test_two_mib_graph_event_round_trips(
    tmp_path: Path, event_type: str, direction: str, header: str
) -> None:
    payload = {**GRAPH_PAYLOADS[event_type], "commit": "é" * MAX_EVENT_LINE_BYTES}
    envelope = json.dumps(
        {"seq": 1, "ts": "2026-10-27T14:00:00.000Z", "payload": payload, "type": event_type},
        ensure_ascii=False,
    )
    if header == "escaped":
        envelope = envelope.replace('"type"', r'"ty\u0070e"').replace("graph.", r"graph\u002e")
    elif header == "duplicate":
        envelope = envelope.replace('"type":', '"type":"error","type":')
    assert 2 * MAX_EVENT_LINE_BYTES < len(envelope.encode()) < 3 * MAX_EVENT_LINE_BYTES
    path = tmp_path / "large.jsonl"
    if direction == "write":
        event = StoredEvent(1, "2026-10-27T14:00:00.000Z", event_type, envelope)
        assert write_jsonl(path, [event]) == 1
    else:
        path.write_bytes(envelope.encode() + b"\n")
    assert read_jsonl(path)[0].model_dump(mode="json")["payload"]["commit"] == payload["commit"]


@pytest.mark.parametrize(
    "type_fields",
    [
        '"type":"error"',
        '"type":"graph.snapshot","type":"error"',
        '"type":"error","nested":{"type":"graph.snapshot"}',
        '"type":"error","message":"\\"type\\":\\"graph.delta\\""',
        '"type":"graph.snapshot.extra"',
    ],
)
@pytest.mark.parametrize("direction", ["read", "write"])
def test_large_non_graph_is_refused_before_contract_decoding(
    tmp_path: Path, type_fields: str, direction: str
) -> None:
    envelope = '{"payload":{},' + type_fields + "}" + " " * MAX_EVENT_LINE_BYTES
    path = tmp_path / "oversized.jsonl"
    path.write_text(envelope)
    with (
        patch.object(SERVER_EVENT, "validate_json", side_effect=AssertionError("decoded too soon")),
        pytest.raises(LogError, match="event exceeds size limit"),
    ):
        if direction == "read":
            read_jsonl(path)
        else:
            # The envelope decides the limit, even if storage metadata names a graph event.
            write_jsonl(path, [StoredEvent(1, "", "graph.snapshot", envelope)])


def test_oversized_line_read_is_bounded_before_decoding(tmp_path: Path) -> None:
    sizes: list[int] = []

    class TrackingReader(io.BytesIO):
        def readline(self, size: int | None = -1) -> bytes:
            assert size is not None
            sizes.append(size)
            return super().readline(size)

    handle = TrackingReader(b" " * (MAX_GRAPH_EVENT_LINE_BYTES + 100))
    with (
        patch.object(Path, "open", return_value=handle),
        patch.object(SERVER_EVENT, "validate_json", side_effect=AssertionError("decoded too soon")),
        pytest.raises(LogError, match="event exceeds size limit"),
    ):
        read_jsonl(tmp_path / "oversized.jsonl")
    assert sizes == [MAX_GRAPH_EVENT_LINE_BYTES + 2]
