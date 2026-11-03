import asyncio
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

from oversight.settings import Settings

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import smoke

SMOKE_SCRIPT = Path(__file__).resolve().parents[1] / "smoke.py"
# Native smoke also checks console shutdown, empty Jobs and the released runtime lock, with
# 30 s startup/graph waits and 20 s supervision waits; it cannot share POSIX's 15 s speed bound.
NT_SMOKE_TIMEOUT_S = 180

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


@pytest.mark.parametrize("inherited", [False, True])
def test_the_backend_settings_override_personal_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inherited: bool
) -> None:
    # The one place the command lives; the fake model's script is read from the repository root.
    assert smoke.BACKEND_ARGS == ("serve",)
    personal = {
        "META_API_KEY": "fake-meta-test-value",
        "BASETEN_API_KEY": "fake-baseten-test-value",
        "OVERSIGHT_AGENT_MODEL": "muse-spark",
        "OVERSIGHT_AGENT_MODEL_B": "muse-spark-open",
        "OVERSIGHT_FAKE_SCRIPT": "personal-a.json",
        "OVERSIGHT_FAKE_SCRIPT_B": "personal-b.json",
        "OVERSIGHT_CROSS_REVIEW": "true",
        "OVERSIGHT_WORKSPACE_SOURCE": "C:/fake-personal-repository",
    }
    env_file = tmp_path / "personal.env"
    env_file.write_text(
        "".join(f"{key}={value}\n" for key, value in personal.items()), encoding="utf-8"
    )
    for key, value in personal.items():
        if inherited:
            monkeypatch.setenv(key, value)
        else:
            monkeypatch.delenv(key, raising=False)
    env = smoke.child_env(8000, tmp_path / ".runtime")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    settings = Settings.load(env_file)
    assert env["META_API_KEY"] == env["BASETEN_API_KEY"] == ""
    assert settings.meta_api_key is None and settings.baseten_api_key is None
    assert settings.agent_model == settings.agent_model_b == "fake"
    assert settings.fake_script == settings.fake_script_b == smoke.REPO_ROOT / smoke.FAKE_SCRIPT
    assert settings.fake_script is not None
    assert settings.fake_script.is_file()
    assert settings.cross_review is False
    assert settings.workspace_source == ""


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


def _health_body(ok: bool) -> bytes:
    return json.dumps(
        {
            "ok": ok,
            "run_id": "run-20261027-140312",
            "contract_version": "1.0.0",
            "mode": "replay",
        }
    ).encode("utf-8")


def test_health_that_answers_is_returned() -> None:
    with _fake_server({"/health": (200, _health_body(ok=True))}) as base:
        assert smoke.wait_for_health(base, deadline_seconds=2).ok is True


def test_a_health_body_that_is_not_the_contract_fails_with_one_line() -> None:
    with (
        _fake_server({"/health": (200, b"[]")}) as base,
        pytest.raises(smoke.SmokeError, match="/health does not validate"),
    ):
        smoke.wait_for_health(base, deadline_seconds=2)


def test_health_that_does_not_say_ok_fails() -> None:
    with (
        _fake_server({"/health": (200, _health_body(ok=False))}) as base,
        pytest.raises(smoke.SmokeError, match="/health does not say ok"),
    ):
        smoke.wait_for_health(base, deadline_seconds=2)


@contextlib.contextmanager
def _dribbling_server() -> Iterator[str]:
    """Serves /health as a body that arrives one byte at a time, each within the idle timeout."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "600")
            self.end_headers()
            for _ in range(600):
                self.wfile.write(b"x")
                self.wfile.flush()
                time.sleep(0.05)

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


def test_a_body_sent_slowly_cannot_outlive_the_deadline() -> None:
    # Every byte arrives well within REQUEST_TIMEOUT (2 s), so only an absolute deadline ends
    # this; without one the smoke would read for 9999 * 0.05 s and never reach its cleanup.
    started = time.monotonic()
    with (
        _dribbling_server() as base,
        pytest.raises(smoke.SmokeError, match="nothing answered /health"),
    ):
        smoke.wait_for_health(base, deadline_seconds=0.6)
    assert time.monotonic() - started < 5


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


@pytest.mark.timeout(NT_SMOKE_TIMEOUT_S + 15)
def test_the_real_smoke_passes_once_quickly() -> None:
    timeout_s = NT_SMOKE_TIMEOUT_S if sys.platform == "win32" else 60
    speed_bound_s = NT_SMOKE_TIMEOUT_S if sys.platform == "win32" else 15

    async def run() -> tuple[int | None, str, str]:
        # Inherit the console: the native driver's CTRL_BREAK proof needs console attachment.
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            str(SMOKE_SCRIPT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(child.communicate(), timeout_s)
        finally:
            if child.returncode is None:
                child.kill()
                await asyncio.wait_for(child.wait(), 5)
        return child.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")

    started = time.monotonic()
    code, stdout, stderr = asyncio.run(run())
    elapsed = time.monotonic() - started
    assert code == 0, stderr
    assert stdout.splitlines().count("smoke: ok") == 1
    assert elapsed < speed_bound_s, f"the smoke run took {elapsed:.1f} s"


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


@pytest.mark.parametrize("failure", [None, smoke.SmokeError, OSError, subprocess.SubprocessError])
def test_nt_smoke_reports_success_only_after_shared_supervision_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: type[Exception] | None,
) -> None:
    import demo_check_nt as nt

    called: list[tuple[int, bool]] = []

    async def checks(runs: int, *, demo: bool) -> None:
        called.append((runs, demo))
        if failure is not None:
            raise failure("injected failure")

    monkeypatch.setattr(nt, "run_checks", checks)
    monkeypatch.setattr(smoke.sys, "platform", "win32")
    result = smoke.main()
    assert called == [(1, False)]
    output = capsys.readouterr()
    assert result == (1 if failure else 0)
    assert output.out == ("" if failure else "smoke: ok\n")
    assert output.err == ("smoke: injected failure\n" if failure else "")


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["", "health", "graph"])
async def test_nt_smoke_always_stops_with_logs_and_safe_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
):
    import demo_check_nt as nt

    calls = []

    class Server:
        child = object()

        async def stop(self, deadline_s):
            assert 0 < deadline_s <= nt.STOP_DEADLINE
            calls.append("stop")

    async def start(argv, env, log):
        assert argv[-1] == "serve" and log == tmp_path / "server-1.log"
        assert env["META_API_KEY"] == env["BASETEN_API_KEY"] == ""
        assert env["OVERSIGHT_AGENT_MODEL"] == env["OVERSIGHT_AGENT_MODEL_B"] == "fake"
        assert env["OVERSIGHT_CROSS_REVIEW"] == "false" and env["OVERSIGHT_WORKSPACE_SOURCE"] == ""
        return Server()

    def phase(name):
        def check(*a):
            calls.append(name)
            if failure == name:
                raise smoke.SmokeError(name)

        return check

    async def empty(*a):
        calls.append("empty")

    async def lock(*a):
        assert calls[-1] == "empty"
        calls.append("lock")

    monkeypatch.setattr(nt, "start_server", start)
    monkeypatch.setattr(nt, "retain_outer", lambda: 77)
    monkeypatch.setattr(nt, "wait_members", empty)
    monkeypatch.setattr(nt, "probe_lock", lock)
    monkeypatch.setattr(smoke, "wait_for_health", phase("health"))
    monkeypatch.setattr(smoke, "wait_for_graph", phase("graph"))
    if failure:
        with pytest.raises(smoke.SmokeError, match=failure):
            await nt.run_once(
                1, tmp_path, tmp_path / ".runtime", None, nt.OverallClock(), demo=False
            )
        assert calls[-1] == "stop" and "empty" not in calls
    else:
        await nt.run_once(1, tmp_path, tmp_path / ".runtime", None, nt.OverallClock(), demo=False)
        assert calls == ["health", "graph", "stop", "empty", "lock"]
