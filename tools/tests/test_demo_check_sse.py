"""Exercise the SSE reader with byte streams, without opening a connection."""

import http.client
import json
import queue
import sys
from io import BytesIO
from pathlib import Path
from typing import cast

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import demo_check_io as io
import smoke


def message(seq: int = 1, *, multiline: bool = False) -> bytes:
    envelope = {
        "seq": seq,
        "ts": "2026-11-03T12:00:00.000Z",
        "type": "agent.message",
        "payload": {"agent_id": "agent-1", "text": "Hello"},
    }
    data = json.dumps(envelope, indent=2 if multiline else None)
    return b"".join(b"data: " + line.encode() + b"\n" for line in data.splitlines())


def reader(data: bytes) -> io.EventStream:
    stream = io.EventStream.__new__(io.EventStream)
    stream._clock = None
    stream._queue = queue.Queue()
    stream.seen = []
    stream._response = cast(http.client.HTTPResponse, BytesIO(data))
    stream._pump()
    return stream


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        (b"", "missing SSE id"),
        (b"id:\n", r"SSE id '' differs from JSON seq 1"),
        (b"id: 2\n", r"SSE id '2' differs from JSON seq 1"),
        (b"id: 01\n", r"SSE id '01' differs from JSON seq 1"),
        (b"id: 1\nevent: update\n", r"SSE event 'update' is not a default message"),
        (b"id: 1\nevent: Message\n", r"SSE event 'Message' is not a default message"),
    ],
)
def test_invalid_sse_metadata_is_refused(fields: bytes, reason: str) -> None:
    stream = reader(fields + message() + b"\n")
    with pytest.raises(smoke.SmokeError, match=reason):
        stream.wait(lambda e: True, 1, "a message", after_seq=0)
    assert stream.seen == []


@pytest.mark.parametrize("event", [b"", b"event:\n", b"event\n", b"event: message\n"])
@pytest.mark.parametrize("ending", [b"\n", b"\r\n"])
@pytest.mark.parametrize("multiline", [False, True])
def test_default_messages_accept_pings_line_endings_and_multiline_data(
    event: bytes, ending: bytes, multiline: bool
) -> None:
    data = (
        b": ping\n\n"
        + event
        + b"id: 1\n: a comment within the message\n"
        + message(multiline=multiline)
        + b"\n: ping\n\n"
    ).replace(b"\n", ending)
    stream = reader(data)
    found = stream.wait(lambda e: True, 1, "a message", after_seq=0)
    assert found.seq == 1
    assert found.type == "agent.message"
    assert found.payload.text == "Hello"
    assert stream.seen == [found]
    assert stream._queue.get_nowait() is None


@pytest.mark.parametrize("fields", [b"id: 1\n", b"id: 1\nevent: message\n"])
def test_id_is_required_on_every_message(fields: bytes) -> None:
    stream = reader(fields + message() + b"\n" + message(2) + b"\n")
    assert stream.wait(lambda e: True, 1, "first message", after_seq=0).seq == 1
    with pytest.raises(smoke.SmokeError, match="missing SSE id"):
        stream.wait(lambda e: True, 1, "second message", after_seq=1)
    assert len(stream.seen) == 1


def test_metadata_in_a_block_without_data_does_not_leak_into_the_next_message() -> None:
    stream = reader(b"event: update\nid: 99\n\nid:1\n" + message() + b"\n")
    assert stream.wait(lambda e: True, 1, "a message", after_seq=0).seq == 1


def test_last_field_values_and_fields_after_data_follow_sse_order() -> None:
    stream = reader(b"id: 99\nevent: update\n" + message() + b"event: message\nid: 1\n\n")
    assert stream.wait(lambda e: True, 1, "a message", after_seq=0).seq == 1


def test_good_path_keeps_consecutive_events_and_wait_correlation() -> None:
    stream = reader(b"id: 1\n" + message() + b"\nid: 2\n" + message(2) + b"\n")
    found = stream.wait(lambda e: True, 1, "second message", after_seq=1, agent_id="agent-1")
    assert found.seq == 2
    assert [event.seq for event in stream.seen] == [1, 2]
