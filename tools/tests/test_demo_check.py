"""The helpers of tools/demo_check.py. The check itself, three runs of the real server, is
`make demo-check`; these pin the parts that decide its verdict."""

import contextlib
import fcntl
import http.client
import http.server
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar, cast

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
        assert demo_check.stragglers(child.pid) == {child.pid}
        child.kill()
        child.wait(timeout=10)  # reaped: gone from /proc
        assert demo_check.stragglers(child.pid) == set()


def test_a_process_of_another_session_is_not_a_straggler() -> None:
    with sleeper(new_session=True) as server, sleeper(new_session=False) as other:
        assert demo_check.stragglers(server.pid) == {server.pid}
        assert other.pid not in demo_check.stragglers(server.pid)


def test_a_process_orphaned_into_a_session_of_its_own_is_found_after_the_server_is_gone() -> None:
    """The server's child leaves its session and is orphaned while the run goes on. Nothing seen
    at the end could name it by session or group; the subreaper adopts it, so it is found."""
    parent_code = (
        "import subprocess, sys, time\n"
        f"child = subprocess.Popen([sys.executable, '-c', {SLEEPER!r}], start_new_session=True)\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(600)\n"
    )
    child_pid = 0
    with demo_check.adopting_orphans():
        parent = subprocess.Popen(
            [sys.executable, "-c", parent_code], start_new_session=True, stdout=subprocess.PIPE
        )
        try:
            assert parent.stdout is not None
            child_pid = int(parent.stdout.readline())
            parent.kill()
            parent.wait(timeout=10)  # reaped: the child is an orphan, adopted by this process
            assert child_pid not in demo_check.stragglers(parent.pid)  # by session alone: invisible
            assert child_pid in demo_check.stragglers(parent.pid, os.getpid())
        finally:
            parent.kill()
            parent.wait(timeout=10)
            if child_pid:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(child_pid, signal.SIGKILL)


def test_a_process_below_the_reaper_that_has_ended_is_not_a_straggler() -> None:
    with demo_check.adopting_orphans(), sleeper(new_session=True) as server:
        assert server.pid in demo_check.stragglers(server.pid, os.getpid())
        server.kill()
        server.wait(timeout=10)
        assert server.pid not in demo_check.stragglers(server.pid, os.getpid())


class Shutdown:
    """A stand-in for the server's Popen: SIGTERM starts a shutdown that takes `takes_s` seconds,
    which a `wait` with at least that timeout sees end, and a shorter one does not."""

    def __init__(self, takes_s: float) -> None:
        self.takes_s = takes_s
        self.killed = False
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        self.killed = True
        self.returncode = -signal.SIGKILL

    def wait(self, timeout: float) -> int:
        if self.returncode is None:
            if timeout < self.takes_s:
                raise subprocess.TimeoutExpired("server", timeout)
            self.returncode = 0
        return self.returncode


def test_a_shutdown_longer_than_smokes_deadline_but_within_the_checks_is_not_killed() -> None:
    takes_s = 6.0
    assert smoke.STOP_DEADLINE < takes_s <= demo_check.STOP_DEADLINE
    server = Shutdown(takes_s)
    demo_check.stop_server(cast("subprocess.Popen[Any]", server))
    assert server.returncode == 0 and not server.killed


def test_a_shutdown_past_the_checks_deadline_is_killed() -> None:
    server = Shutdown(demo_check.STOP_DEADLINE + 1)
    demo_check.stop_server(cast("subprocess.Popen[Any]", server))
    assert server.killed and server.returncode == -signal.SIGKILL


def test_smokes_own_stop_deadline_is_unchanged() -> None:
    server = Shutdown(smoke.STOP_DEADLINE + 1)
    smoke.stop_child(cast("subprocess.Popen[Any]", server))
    assert server.killed


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


def test_the_makefile_backstop_is_above_the_computed_worst_case() -> None:
    makefile = (demo_check.REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    assert "--print-deadline" in makefile  # one source of truth: demo_check computes it
    match = re.search(r"deadline \+ (\d+)", makefile)
    assert match is not None and 0 < int(match.group(1)) <= 300
    assert demo_check.OVERALL_DEADLINE > 540  # the old outer timeout killed healthy runs


def test_the_overall_deadline_is_the_worst_case_sum_plus_a_small_margin() -> None:
    per_run = (
        2 * demo_check.START_DEADLINE
        + demo_check.START_AGENT_DEADLINE
        + demo_check.PROPOSE_DEADLINE
        + 2 * demo_check.DECIDE_DEADLINE
        + 2 * demo_check.STOP_DEADLINE
        + demo_check.REQUESTS_PER_RUN * demo_check.REQUEST_TIMEOUT
    )
    assert demo_check.RUNS * per_run + demo_check.OVERALL_MARGIN == demo_check.OVERALL_DEADLINE
    assert 0 < demo_check.OVERALL_MARGIN <= 300


def test_an_expired_overall_deadline_names_the_phase() -> None:
    clock = demo_check.OverallClock(started=0.0, deadline_s=10.0)
    clock.phase = "run 2: POST /agents (venv build)"
    with pytest.raises(smoke.SmokeError, match=r"overall deadline .* during run 2: POST /agents"):
        clock.check(now=11.0)


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


@contextlib.contextmanager
def held_event_server() -> Iterator[str]:
    """A server whose /events answers, sends a ping, then holds the stream open."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b": ping\r\n\r\n")
            self.wfile.flush()
            time.sleep(3)

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


def test_a_blocked_event_wait_stops_at_the_overall_deadline() -> None:
    with held_event_server() as base:
        clock = demo_check.OverallClock(deadline_s=0.3)
        clock.set_phase("run 1: wait for change.proposed")
        stream = demo_check.EventStream(base, clock=clock)
        try:
            started = time.monotonic()
            with pytest.raises(
                smoke.SmokeError,
                match=r"overall deadline .* during run 1: wait for change\.proposed",
            ):
                stream.wait(lambda e: e.type == "change.proposed", 10, "change.proposed")
            assert time.monotonic() - started < 3
        finally:
            stream.close()


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


def test_the_stop_wait_is_bounded_by_what_the_overall_clock_has_left(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(demo_check, "STOP_DEADLINE", 5.0)

    class Recording(Shutdown):
        def __init__(self, takes_s: float) -> None:
            super().__init__(takes_s)
            self.timeouts: list[float] = []

        def wait(self, timeout: float) -> int:
            self.timeouts.append(timeout)
            return super().wait(timeout)

    server = Recording(600)
    started = time.monotonic()
    demo_check.stop_server(
        cast("subprocess.Popen[Any]", server), demo_check.OverallClock(deadline_s=0.2)
    )
    assert server.killed and time.monotonic() - started < 3
    assert server.timeouts and max(server.timeouts) <= 0.2


def test_an_expiry_before_the_stop_is_named_for_the_phase_it_expired_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = subprocess.Popen(
        [sys.executable, "-c", SLEEPER], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: server)
    monkeypatch.setattr(smoke, "wait_for_health", lambda *args, **kwargs: None)

    def decide(base: str, first: Any, clock: demo_check.OverallClock, run: int) -> Any:
        clock.phase = "run 1: wait for graph.delta"
        time.sleep(0.06)
        return object()

    monkeypatch.setattr(demo_check, "serve_and_decide", decide)
    try:
        with pytest.raises(
            smoke.SmokeError, match=r"overall deadline .* during run 1: wait for graph\.delta"
        ):
            demo_check.run_once(
                1, tmp_path, tmp_path / ".runtime", None, demo_check.OverallClock(deadline_s=0.05)
            )
        for _ in range(100):
            if server.poll() is not None:
                break
            time.sleep(0.02)
        assert server.poll() is not None  # the stop still ran, bounded, after the expiry
    finally:
        server.kill()
        server.wait(timeout=10)


def test_sigkill_of_the_check_alone_leaves_no_server_and_no_listener(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setattr(urllib.request, "_opener", None)
    port = smoke.free_port()
    tools_dir = Path(__file__).resolve().parents[1]
    parent_code = (
        "import sys, time\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, {str(tools_dir)!r})\n"
        "import demo_check\n"
        f"output = open({str(tmp_path / 'server.log')!r}, 'wb')\n"
        f"child = demo_check.start_server({port}, Path({str(tmp_path / '.runtime')!r}), output)\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(600)\n"
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", parent_code],
        cwd=demo_check.REPO_ROOT,
        stdout=subprocess.PIPE,
        text=True,
    )
    server_pid = 0
    alive, listening = True, True
    try:
        assert parent.stdout is not None
        server_pid = int(parent.stdout.readline())
        smoke.wait_for_health(smoke.base_url(port), 30)
        parent.kill()  # SIGKILL of the check alone: no cleanup in it can run
        parent.wait(timeout=10)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = demo_check.proc_table().get(server_pid, ("Z", 0, 0, 0))[0]
            alive = state != "Z"
            try:
                with socket.create_connection((smoke.HOST, port), timeout=0.2):
                    listening = True
            except OSError:
                listening = False
            if not alive and not listening:
                break
            time.sleep(0.05)
        assert not alive, f"server {server_pid} survived the check's SIGKILL"
        assert not listening, f"port {port} still listens after the check's SIGKILL"
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=10)
        if server_pid:
            with contextlib.suppress(ProcessLookupError):
                os.kill(server_pid, signal.SIGKILL)


def test_a_child_with_a_stale_expected_parent_exits_instead_of_execing() -> None:
    stale_parent = os.getpid() + 1  # the child's real parent is this process, not this PID
    child = subprocess.Popen(
        [sys.executable, "-c", "print('execed', flush=True)"],
        stdout=subprocess.PIPE,
        text=True,
        preexec_fn=lambda: demo_check._die_with_parent(stale_parent),
    )
    output, _ = child.communicate(timeout=10)
    assert child.returncode == 1
    assert output == ""


def test_the_server_gets_no_provider_key_even_one_set_in_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("META_API_KEY", "test-key")
    env = demo_check.server_env(8000, tmp_path / ".runtime")
    assert env["META_API_KEY"] == "" and env["BASETEN_API_KEY"] == ""
    assert env["OVERSIGHT_AGENT_MODEL"] == "fake"
