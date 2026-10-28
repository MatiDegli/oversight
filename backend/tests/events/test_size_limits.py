"""Exported event lines obey the same byte boundary as imported lines."""

from pathlib import Path

import pytest

from oversight.events.jsonl import LogError, read_jsonl, write_jsonl
from oversight.events.store import StoredEvent
from oversight.limits import MAX_EVENT_LINE_BYTES


@pytest.mark.parametrize("extra", [0, 1])
@pytest.mark.parametrize("newline", [b"", b"\n", b"\r\n"])
def test_event_line_byte_boundary_in_both_directions(
    tmp_path: Path, extra: int, newline: bytes
) -> None:
    # Multibyte text catches accidental character limits; JSON whitespace fills the line exactly.
    envelope = (
        '{"seq":1,"ts":"2026-10-27T14:00:00.000Z","type":"error",'
        '"payload":{"source":"server","message":"é","fatal":false,'
        '"agent_id":null,"change_id":null}}'
    )
    envelope += " " * (MAX_EVENT_LINE_BYTES + extra - len(envelope.encode("utf-8")))
    event = StoredEvent(1, "2026-10-27T14:00:00.000Z", "error", envelope)
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
