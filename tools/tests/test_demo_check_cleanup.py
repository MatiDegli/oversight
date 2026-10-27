"""The helpers of tools/demo_check.py. The check itself, three runs of the real server, is
`make demo-check`; these pin the parts that decide its verdict."""

import contextlib
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import demo_check
import smoke

LOG = demo_check.REPO_ROOT / "fixtures" / "events" / "handwritten-level1.jsonl"
SLEEPER = "import time; time.sleep(600)"


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
