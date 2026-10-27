"""The helpers of tools/demo_check.py. The check itself, three runs of the real server, is
`make demo-check`; these pin the parts that decide its verdict."""

import contextlib
import http.client
import http.server
import json
import queue
import sys
import threading
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import demo_check
import demo_check_io as io
import smoke

LOG = demo_check.REPO_ROOT / "fixtures" / "events" / "handwritten-level1.jsonl"
SLEEPER = "import time; time.sleep(600)"


@contextlib.contextmanager
def trickling_server() -> Iterator[str]:
    """A server that sends one byte every 50 ms: never idle, never finished in time."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "1000")
            self.end_headers()
            try:
                for _ in range(60):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.05)
            except OSError:
                pass

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = http.server.ThreadingHTTPServer((smoke.HOST, 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield smoke.base_url(server.server_address[1])
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def controlled_event_wait(
    monkeypatch: pytest.MonkeyPatch, item: Exception | None, elapsed: float
) -> demo_check.EventStream:
    """Deliver the reader's result only after wait() has bounded its queue timeout."""
    now = [0.0]
    monkeypatch.setattr(io, "time", SimpleNamespace(monotonic=lambda: now[0]))
    stream = demo_check.EventStream.__new__(demo_check.EventStream)
    stream._clock = demo_check.OverallClock(started=0.0, deadline_s=1.0)
    stream._clock.set_phase("run 1: wait for change.proposed")
    stream.seen = []
    stream._queue = queue.Queue()

    def get(*, timeout: float) -> Exception | None:
        assert timeout == 1.0
        now[0] = elapsed
        if isinstance(item, queue.Empty):
            raise item
        return item

    monkeypatch.setattr(stream._queue, "get", get)
    return stream


def test_a_trickling_reply_is_bounded_in_total_not_just_between_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("no_proxy", "127.0.0.1")  # urlopen must reach the stub, not any proxy
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setattr(urllib.request, "_opener", None)  # rebuilt under the env above
    with trickling_server() as base:
        started = time.monotonic()
        with pytest.raises(smoke.SmokeError, match=r"GET /slow did not finish within 0\.5 s"):
            demo_check.request(base, "/slow", timeout=0.5)
        assert time.monotonic() - started < 3


@pytest.mark.parametrize(
    "item", [TimeoutError("timed out"), None, queue.Empty()], ids=["reader-timeout", "eof", "queue"]
)
@pytest.mark.parametrize("elapsed", [1.0, 2.0], ids=["at-deadline", "past-deadline"])
def test_a_blocked_event_wait_stops_at_the_overall_deadline(
    monkeypatch: pytest.MonkeyPatch, item: Exception | None, elapsed: float
) -> None:
    stream = controlled_event_wait(monkeypatch, item, elapsed=elapsed)
    with pytest.raises(
        smoke.SmokeError,
        match=r"overall deadline .* during run 1: wait for change\.proposed",
    ):
        stream.wait(lambda e: e.type == "change.proposed", 10, "change.proposed", after_seq=0)


@pytest.mark.parametrize(
    ("item", "reason"),
    [(None, "closed"), (TimeoutError("timed out"), "timed out"), (OSError("broken"), "broken")],
)
def test_a_stream_ending_before_the_deadline_keeps_its_reason(
    monkeypatch: pytest.MonkeyPatch, item: Exception | None, reason: str
) -> None:
    stream = controlled_event_wait(monkeypatch, item, elapsed=0.5)
    with pytest.raises(smoke.SmokeError, match=f"/events ended before change.proposed: {reason}"):
        stream.wait(lambda e: e.type == "change.proposed", 10, "change.proposed", after_seq=0)


def test_an_expired_deadline_is_named_for_the_phase_it_expired_in() -> None:
    clock = demo_check.OverallClock(deadline_s=0.05)
    clock.set_phase("run 1: wait for /graph")
    time.sleep(0.06)
    with pytest.raises(
        smoke.SmokeError, match=r"overall deadline .* during run 1: wait for /graph"
    ):
        clock.set_phase("run 1: check the UI")
    assert clock.phase == "run 1: wait for /graph"


def test_the_event_stream_open_is_bounded_by_the_overall_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class Response:
        status = 200

        def __iter__(self) -> Iterator[bytes]:
            return iter(())

    class Connection:
        def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
            captured.update(host=host, port=port, timeout=timeout)

        def request(self, *args: Any, **kwargs: Any) -> None:
            pass

        def getresponse(self) -> Response:
            return Response()

        def close(self) -> None:
            pass

    monkeypatch.setattr(http.client, "HTTPConnection", Connection)
    clock = demo_check.OverallClock(deadline_s=0.5)
    stream = demo_check.EventStream("http://127.0.0.1:9", clock=clock)
    try:
        assert captured["timeout"] is not None and 0 < captured["timeout"] <= 0.5
    finally:
        stream.close()


class Answer:
    """A stand-in for what urlopen returns."""

    status = 201
    headers: ClassVar[dict[str, str]] = {"Content-Type": "application/json"}

    def __enter__(self) -> "Answer":
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def read(self) -> bytes:
        return json.dumps({"agent_id": "agent-1", "change_id": "change-1", "slot": "A"}).encode()


def test_starting_an_agent_waits_for_the_venv_build_but_other_requests_do_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeouts: dict[str, float] = {}

    def urlopen(call: urllib.request.Request, timeout: float) -> Answer:
        timeouts[call.full_url] = timeout
        return Answer()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    created = demo_check.start_agent("http://test")
    demo_check.request("http://test", "/graph")
    assert created.change_id == "change-1"
    assert timeouts["http://test/agents"] == demo_check.START_AGENT_DEADLINE
    assert timeouts["http://test/graph"] == demo_check.REQUEST_TIMEOUT
    assert demo_check.START_AGENT_DEADLINE > demo_check.REQUEST_TIMEOUT
