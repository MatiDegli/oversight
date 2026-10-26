import contextlib
import http.server
import json
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import smoke

SMOKE_SCRIPT = Path(__file__).resolve().parents[1] / "smoke.py"

# The smallest body GraphResponse accepts: an empty graph is still a graph.
VALID_GRAPH_BODY = {
    "run_id": "run-20261027-140312",
    "seq": 2,
    "graph": {
        "version": "deadbeef",
        "commit": "8cbf7534f0f7f390dd9d6e514081b6760d20423d",
        "nodes": [],
        "edges": [],
        "parse_errors": [],
        "positions": {},
    },
}


def _handler(responses: dict[str, tuple[int, bytes]]) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            status, body = responses.get(self.path, (404, b"{}"))
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # keep the test output quiet
            pass

    return Handler


@contextlib.contextmanager
def _fake_server(responses: dict[str, tuple[int, bytes]]) -> Iterator[str]:
    server = http.server.ThreadingHTTPServer((smoke.HOST, 0), _handler(responses))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield smoke.base_url(server.server_address[1])
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _json(body: dict[str, Any]) -> bytes:
    return json.dumps(body).encode("utf-8")


def test_a_free_port_can_be_bound() -> None:
    port = smoke.free_port()
    assert 0 < port < 65536
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((smoke.HOST, port))


def test_the_backend_command_replays_an_existing_log() -> None:
    # The one place the command lives; its log path is read from the repository root.
    assert smoke.BACKEND_ARGS[0] == "replay"
    assert (smoke.REPO_ROOT / smoke.BACKEND_ARGS[1]).is_file()


def test_nothing_answering_fails_at_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(smoke, "REQUEST_TIMEOUT", 0.2)
    stuck = socket.socket()  # listens, but never accepts or answers, like a stuck backend
    stuck.bind((smoke.HOST, 0))
    stuck.listen(1)
    port = stuck.getsockname()[1]
    started = time.monotonic()
    try:
        with pytest.raises(smoke.SmokeError, match="nothing answered /health"):
            smoke.wait_for_health(smoke.base_url(port), deadline_seconds=0.5)
    finally:
        stuck.close()
    assert time.monotonic() - started < 5


def test_health_that_answers_is_returned() -> None:
    body = b'{"ok": true, "run_id": "run-20261027-140312", "mode": "replay"}'
    with _fake_server({"/health": (200, body)}) as base:
        assert smoke.wait_for_health(base, deadline_seconds=2)["ok"] is True


def test_a_health_error_status_fails_at_once() -> None:
    with (
        _fake_server({"/health": (503, b"{}")}) as base,
        pytest.raises(smoke.SmokeError, match="/health answered 503"),
    ):
        smoke.wait_for_health(base, deadline_seconds=2)


def test_a_graph_that_fails_validation_names_the_field() -> None:
    with pytest.raises(smoke.SmokeError, match=r"/graph does not validate: run_id"):
        smoke.check_graph({"seq": 2, "graph": VALID_GRAPH_BODY["graph"]})


def test_a_graph_before_the_first_event_fails() -> None:
    body = dict(VALID_GRAPH_BODY, seq=0)
    with pytest.raises(smoke.SmokeError, match="before the first event"):
        smoke.check_graph(body)


def test_a_valid_graph_returns_its_seq() -> None:
    with _fake_server({"/graph": (200, _json(VALID_GRAPH_BODY))}) as base:
        assert smoke.wait_for_graph(base, deadline_seconds=2) == 2


def test_a_graph_error_status_fails_at_once() -> None:
    with (
        _fake_server({"/graph": (500, b"{}")}) as base,
        pytest.raises(smoke.SmokeError, match="/graph answered 500"),
    ):
        smoke.wait_for_graph(base, deadline_seconds=2)


def test_a_graph_without_a_snapshot_keeps_trying_until_the_deadline() -> None:
    with (
        _fake_server({"/graph": (404, b"{}")}) as base,
        pytest.raises(smoke.SmokeError, match="still had no snapshot"),
    ):
        smoke.wait_for_graph(base, deadline_seconds=0.3)


def test_the_real_smoke_passes_once_quickly() -> None:
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, str(SMOKE_SCRIPT)], capture_output=True, text=True, timeout=60
    )
    elapsed = time.monotonic() - started
    assert result.returncode == 0, result.stderr
    assert "smoke: ok" in result.stdout
    assert elapsed < 15, f"the smoke run took {elapsed:.1f} s"


def test_a_backend_that_dies_is_reported(tmp_path: Path) -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.exit(3)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        with pytest.raises(smoke.SmokeError, match="exited with code 3"):
            smoke.wait_for_health("http://127.0.0.1:1", deadline_seconds=5, child=child)
    finally:
        smoke.stop_child(child)
