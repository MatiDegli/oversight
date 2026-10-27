"""The helpers of tools/demo_check.py. The check itself, three runs of the real server, is
`make demo-check`; these pin the parts that decide its verdict."""

import contextlib
import fcntl
import http.server
import json
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import demo_check
import smoke

LOG = demo_check.REPO_ROOT / "fixtures" / "events" / "handwritten-level1.jsonl"
SLEEPER = "import time; time.sleep(600)"


def test_the_assets_an_index_names_are_found_once_each() -> None:
    html = (
        '<script type="module" crossorigin src="/assets/index-A1.js"></script>'
        '<link rel="stylesheet" href="/assets/index-B2.css">'
        '<link rel="modulepreload" href="/assets/index-A1.js"><a href="/review">not an asset</a>'
    )
    assert demo_check.asset_refs(html) == ["/assets/index-A1.js", "/assets/index-B2.css"]


def test_a_reply_must_have_the_status_and_headers_asked_for() -> None:
    reply = demo_check.Reply(200, {"cache-control": "no-cache", "content-type": "text/html"}, b"")
    assert demo_check.expect(reply, "GET /", cache_control="no-cache", content_type="text/html*")
    with pytest.raises(smoke.SmokeError, match="answered 200, not 404"):
        demo_check.expect(reply, "GET /", 404)
    with pytest.raises(smoke.SmokeError, match="has cache-control: 'no-cache', not 'immutable'"):
        demo_check.expect(reply, "GET /", cache_control="immutable")
    with pytest.raises(smoke.SmokeError, match="has content-type: 'text/html', not 'text/css"):
        demo_check.expect(reply, "GET /", content_type="text/css*")


@contextlib.contextmanager
def event_server(lines: list[str]) -> Iterator[str]:
    """A server whose /events sends `lines` as server-sent events and then closes the stream."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b": ping\r\n\r\n")
            for number, line in enumerate(lines, start=1):
                self.wfile.write(f"id: {number}\r\ndata: {line}\r\n\r\n".encode())

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = http.server.ThreadingHTTPServer((smoke.HOST, 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield smoke.base_url(server.server_address[1])
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def recorded(count: int) -> list[str]:
    return LOG.read_text(encoding="utf-8").splitlines()[:count]


def error_event(seq: int) -> str:
    payload = {"source": "server", "message": "boom", "agent_id": None, "change_id": None}
    envelope = {"seq": seq, "ts": "2026-10-27T14:03:12.000Z", "type": "error"}
    return json.dumps(envelope | {"payload": payload | {"fatal": False}})


def test_the_stream_returns_the_first_event_that_matches() -> None:
    with event_server(recorded(3)) as base:
        stream = demo_check.EventStream(base)
        try:
            found = stream.wait(lambda e: e.type == "graph.snapshot", 10, "a snapshot")
            assert found.type == "graph.snapshot"
            # Events already seen are searched again: a later wait still finds the first one.
            again = stream.wait(lambda e: e.type == "run.started", 10, "run.started")
            assert again.type == "run.started"
        finally:
            stream.close()


def test_the_stream_ending_before_the_event_fails_with_what_was_awaited() -> None:
    with event_server(recorded(2)) as base:
        stream = demo_check.EventStream(base)
        try:
            with pytest.raises(smoke.SmokeError, match=r"/events ended before change\.resolved"):
                stream.wait(lambda e: e.type == "change.resolved", 10, "change.resolved")
        finally:
            stream.close()


def test_an_error_event_fails_the_wait_at_once() -> None:
    with event_server([*recorded(1), error_event(2)]) as base:
        stream = demo_check.EventStream(base)
        try:
            with pytest.raises(smoke.SmokeError, match="an error event came before x: boom"):
                stream.wait(lambda e: e.type == "change.proposed", 10, "x")
        finally:
            stream.close()


def test_an_event_that_is_not_the_contract_fails() -> None:
    with event_server(['{"seq": 1, "type": "nonsense"}']) as base:
        stream = demo_check.EventStream(base)
        try:
            with pytest.raises(smoke.SmokeError, match="an event does not validate"):
                stream.wait(lambda e: True, 10, "anything")
        finally:
            stream.close()


@contextlib.contextmanager
def sleeper(*, new_session: bool) -> Iterator[subprocess.Popen[bytes]]:
    child = subprocess.Popen(
        [sys.executable, "-c", SLEEPER], start_new_session=new_session, stdout=subprocess.DEVNULL
    )
    try:
        yield child
    finally:
        child.kill()
        child.wait(timeout=10)


def test_a_live_process_of_the_servers_session_is_a_straggler_until_it_ends() -> None:
    with sleeper(new_session=True) as child:
        assert demo_check.stragglers(child.pid, set()) == {child.pid}
        child.kill()
        child.wait(timeout=10)  # reaped: gone from /proc
        assert demo_check.stragglers(child.pid, set()) == set()


def test_a_process_of_another_session_is_not_a_straggler() -> None:
    with sleeper(new_session=True) as server, sleeper(new_session=False) as other:
        assert demo_check.stragglers(server.pid, set()) == {server.pid}
        assert other.pid not in demo_check.stragglers(server.pid, set())


def test_a_descendant_seen_earlier_is_a_straggler_wherever_it_went() -> None:
    parent_code = (
        "import subprocess, sys, time\n"
        f"child = subprocess.Popen([sys.executable, '-c', {SLEEPER!r}])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(600)\n"
    )
    parent = subprocess.Popen([sys.executable, "-c", parent_code], stdout=subprocess.PIPE)
    child_pid = 0
    try:
        assert parent.stdout is not None
        child_pid = int(parent.stdout.readline())
        found = demo_check.descendants(parent.pid)
        assert found == {child_pid}
        # The parent dies, the child is left: only the earlier sighting still names it.
        parent.kill()
        parent.wait(timeout=10)
        assert demo_check.stragglers(parent.pid, found) == {child_pid}
        assert demo_check.stragglers(parent.pid, set()) == set()
    finally:
        parent.kill()
        parent.wait(timeout=10)
        if child_pid:
            with contextlib.suppress(ProcessLookupError):
                os.kill(child_pid, signal.SIGKILL)


def test_the_runtime_lock_is_free_unless_someone_holds_it(tmp_path: Path) -> None:
    descriptor = os.open(tmp_path / "server.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert demo_check.lock_is_free(tmp_path) is False
    finally:
        os.close(descriptor)  # closing the descriptor lets the lock go
    assert demo_check.lock_is_free(tmp_path) is True


def test_a_missing_lock_file_is_an_error_not_a_free_lock(tmp_path: Path) -> None:
    with pytest.raises(smoke.SmokeError, match="runtime lock cannot be opened"):
        demo_check.lock_is_free(tmp_path)


def test_the_server_gets_no_provider_key_even_one_set_in_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("META_API_KEY", "test-key")
    env = demo_check.server_env(8000, tmp_path / ".runtime")
    assert env["META_API_KEY"] == "" and env["BASETEN_API_KEY"] == ""
    assert env["OVERSIGHT_AGENT_MODEL"] == "fake"
