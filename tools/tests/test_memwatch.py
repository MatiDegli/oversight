"""The soft caps of a test run: the memory watchdog (tools/memwatch.py) and pytest-timeout.

The runaway tests run in a pytest of their own, in a temporary directory, with a cap of a few
hundred MiB and a bounded allocation. If a cap failed to fire, the inner test would end by itself
after a bounded wait, with exit code 0, and the assertions here would fail. The hard layer, the
systemd scope around `make check`'s pytest, is in test_capped.py.
"""

import os
import signal
import subprocess
import sys
import textwrap
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import memwatch

TOOLS = Path(__file__).resolve().parents[1]
MIB = 2**20
INNER_CAP_GB = "0.3"  # the cap of the inner runs: 307 MB, above the ~80 MB of a bare pytest
INNER_MIB = 600  # what an inner test, or its child, holds: twice the cap
INNER_WAIT_S = 30  # how long an inner test waits for the cap to end it
# How long this test waits for an inner pytest: twice an inner wait, and well below the 120 s of
# pytest-timeout, so the wait ends, and cleans up, before that signal can.
RUN_DEADLINE_S = 60


# --- the pieces ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, 8 * 2**30), ("", 8 * 2**30), ("2", 2 * 2**30), ("0.5", 2**29), (" 3 ", 3 * 2**30)],
)
def test_the_cap_comes_from_the_variable_in_gib(raw: str | None, expected: int) -> None:
    assert memwatch.parse_cap(raw) == expected


@pytest.mark.parametrize("raw", ["0", "-1", "lots", "nan", "inf", "8GB"])
def test_a_bad_cap_is_refused_not_ignored(raw: str) -> None:
    with pytest.raises(ValueError, match=memwatch.ENV_CAP):
        memwatch.parse_cap(raw)


@pytest.mark.parametrize(
    ("workers", "expected"),
    [(None, 8 * 2**30), ("1", 8 * 2**30), ("8", 2**30), ("16", 2**29), ("3", 8 * 2**30 // 3)],
)
def test_an_xdist_worker_gets_an_equal_share_of_the_budget_and_no_worker_all_of_it(
    workers: str | None, expected: int
) -> None:
    assert memwatch.worker_cap(8 * 2**30, workers) == expected


@pytest.mark.parametrize("workers", ["", "0", "-2", "many"])
def test_a_bad_worker_count_is_refused_not_ignored(workers: str) -> None:
    with pytest.raises(ValueError, match=memwatch.ENV_WORKERS):
        memwatch.worker_cap(8 * 2**30, workers)


def test_rss_is_this_process_and_grows_with_what_it_holds() -> None:
    before = memwatch.self_rss_bytes()
    assert before > 10 * MIB
    blob = b"x" * (64 * MIB)
    assert memwatch.self_rss_bytes() - before >= 48 * MIB
    del blob


def test_the_watchdog_calls_back_above_the_cap_and_not_below() -> None:
    seen: list[int] = []
    above = memwatch.Watchdog(1, seen.append)
    assert above.check() is True
    assert seen and seen[0] > MIB
    below = memwatch.Watchdog(10 * 2**30, seen.append)
    seen.clear()
    assert below.check() is False
    assert seen == []


def test_the_watchdog_thread_reports_once_and_stops() -> None:
    reported = threading.Event()
    calls: list[int] = []

    def report(used: int) -> None:
        calls.append(used)
        reported.set()

    watchdog = memwatch.Watchdog(1, report, interval=0.01)
    watchdog.start()
    assert reported.wait(timeout=30), "the thread never looked"
    watchdog.stop()
    assert not watchdog.alive
    assert len(calls) == 1


# --- the caps of this run -----------------------------------------------------------------------


def test_this_run_has_the_memory_cap_armed_and_knows_which_test_runs(
    request: pytest.FixtureRequest,
) -> None:
    watch = request.config.pluginmanager.get_plugin(memwatch.PLUGIN_NAME)
    assert isinstance(watch, memwatch.MemoryWatch), "the root conftest.py does not load memwatch"
    assert watch.watchdog.alive
    total = memwatch.parse_cap(os.environ.get(memwatch.ENV_CAP))
    # Under pytest-xdist this process is a worker, and its cap is its share of the budget.
    assert watch.watchdog.cap_bytes == memwatch.worker_cap(
        total, os.environ.get(memwatch.ENV_WORKERS)
    )
    assert watch.current == request.node.nodeid


def test_this_run_has_the_time_cap_armed(request: pytest.FixtureRequest) -> None:
    config = request.config
    assert config.pluginmanager.hasplugin("timeout"), "pytest-timeout is not installed"
    seconds = float(config.getini("timeout"))
    assert 0 < seconds <= 300, "a hang must end within minutes"
    assert config.getini("timeout_func_only") is True, "slow session fixtures need their own limit"
    assert config.getini("timeout_method") == "signal", "a timeout must fail one test, not the run"


# --- a runaway test in a pytest of its own ------------------------------------------------------


@dataclass(frozen=True)
class InnerRun:
    pid: int  # of the inner pytest
    returncode: int
    stdout: str
    stderr: str


def inner_pytest(directory: Path, source: str, *args: str, cap_gb: str = INNER_CAP_GB) -> InnerRun:
    """Run `source` as a test file in a fresh pytest with the memwatch plugin, outside this
    repository's configuration. However this function ends, the inner process does not outlive it:
    a deadline, an error, or the per-test timeout of this run (an exception raised from inside
    `communicate`) all kill and reap it."""
    (directory / "test_inner.py").write_text(textwrap.dedent(source))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "GIT_"))}
    env |= {"PYTHONPATH": str(TOOLS), memwatch.ENV_CAP: cap_gb, "INNER_DIR": str(directory)}
    process = subprocess.Popen(
        [sys.executable, "-m", "pytest", "-p", "memwatch", "-p", "no:cacheprovider", *args],
        cwd=directory,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=RUN_DEADLINE_S)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
    return InnerRun(process.pid, process.returncode, stdout, stderr)


def memory_cap_lines(stderr: str) -> list[str]:
    return [line for line in stderr.splitlines() if line.startswith("MEMORY CAP:")]


def test_a_test_that_outgrows_the_cap_ends_the_run_naming_itself(tmp_path: Path) -> None:
    run = inner_pytest(
        tmp_path,
        f"""\
        import os, threading
        from pathlib import Path

        def test_grows():
            # The handshake comes first: the run is known to be in the test before it allocates.
            Path(os.environ["INNER_DIR"], "started.pid").write_text(str(os.getpid()))
            blob = b"x" * ({INNER_MIB} * 2**20)
            threading.Event().wait({INNER_WAIT_S})  # the watchdog ends the run long before this
        """,
    )
    started = tmp_path / "started.pid"
    assert started.exists(), "the inner test never started:\n" + run.stdout + run.stderr
    assert int(started.read_text()) == run.pid, "the process that ended is not the one that grew"
    assert run.returncode == memwatch.EXIT_CODE, run.stdout + run.stderr
    lines = memory_cap_lines(run.stderr)
    assert len(lines) == 1, run.stderr
    assert "test_inner.py::test_grows" in lines[0]
    assert "0.30 GiB" in lines[0]
    assert memwatch.ENV_CAP in lines[0]


def test_a_child_over_the_cap_is_not_counted_and_not_signalled(tmp_path: Path) -> None:
    """The watchdog reads its own process only: the memory of a child, which the cgroup of
    `make check` accounts for, neither ends the run nor gets a signal. The inner test looks
    itself, so nothing here waits on a clock."""
    run = inner_pytest(
        tmp_path,
        f"""\
        import subprocess, sys

        HOLDER = (
            "import sys; blob = b'x' * ({INNER_MIB} * 2**20); "
            "print('ready', flush=True); sys.stdin.read()"
        )

        def test_child_holds_more_than_the_cap(request):
            watch = request.config.pluginmanager.get_plugin("{memwatch.PLUGIN_NAME}")
            child = subprocess.Popen([sys.executable, "-c", HOLDER], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, text=True)
            try:
                assert child.stdout.readline().strip() == "ready"  # it holds its memory now
                assert watch.watchdog.check() is False, "the child's memory was counted"
                assert child.poll() is None, "the child was signalled"
            finally:
                child.stdin.close()
                child.wait(timeout={INNER_WAIT_S})
        """,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "1 passed" in run.stdout
    assert memory_cap_lines(run.stderr) == []


def test_a_bad_cap_in_the_environment_stops_the_run_before_any_test(tmp_path: Path) -> None:
    run = inner_pytest(tmp_path, "def test_never_runs(): ...\n", cap_gb="lots")
    assert run.returncode != 0
    assert memwatch.ENV_CAP in run.stderr
    assert "passed" not in run.stdout


def test_a_test_that_outlives_the_timeout_fails_alone_with_its_name(tmp_path: Path) -> None:
    run = inner_pytest(
        tmp_path,
        f"""\
        import threading

        def test_hangs():
            threading.Event().wait({INNER_WAIT_S})  # the timeout ends it long before this

        def test_next_still_runs():
            assert True
        """,
        "-o",
        "timeout=1",
        cap_gb="8",
    )
    assert run.returncode == 1, run.stdout + run.stderr
    assert "Failed: Timeout (>1.0s) from pytest-timeout." in run.stdout
    assert "FAILED test_inner.py::test_hangs" in run.stdout
    assert "1 failed, 1 passed" in run.stdout


# --- the same caps in a pytest-xdist run, where the Makefile's pytest runs ----------------------

XDIST = ("-n", "2", "--max-worker-restart=0")  # what make check passes, with two workers
XDIST_CAP_GB = "1.2"  # the budget of the run: 1229 MiB, so 614 MiB for each of two workers
XDIST_HELD_MIB = 900  # above a worker's share, and below the whole budget


def test_a_worker_over_its_share_ends_the_run_naming_itself_and_none_is_restarted(
    tmp_path: Path,
) -> None:
    """900 MiB is under the budget of the run and over a worker's half of it: only the division
    ends the run. The controller sees a crashed worker, and restarting is off."""
    run = inner_pytest(
        tmp_path,
        f"""\
        import threading

        def test_grows():
            blob = b"x" * ({XDIST_HELD_MIB} * 2**20)
            threading.Event().wait({INNER_WAIT_S})  # the watchdog ends the worker long before this
        """,
        *XDIST,
        cap_gb=XDIST_CAP_GB,
    )
    assert run.returncode != 0, run.stdout + run.stderr
    lines = memory_cap_lines(run.stderr)
    assert len(lines) == 1, run.stdout + run.stderr
    assert "test_inner.py::test_grows" in lines[0]
    assert "0.60 GiB" in lines[0]
    assert "split over 2 workers" in lines[0]
    assert "crashed" in run.stdout
    assert "replacing crashed worker" not in run.stdout + run.stderr, "a worker was restarted"


def test_the_time_cap_fails_one_test_in_a_worker_and_the_rest_run(tmp_path: Path) -> None:
    run = inner_pytest(
        tmp_path,
        f"""\
        import threading

        def test_hangs():
            threading.Event().wait({INNER_WAIT_S})  # the timeout ends it long before this

        def test_next_still_runs():
            assert True
        """,
        *XDIST,
        "-o",
        "timeout=1",
        cap_gb="8",
    )
    assert run.returncode == 1, run.stdout + run.stderr
    assert "Failed: Timeout (>1.0s) from pytest-timeout." in run.stdout
    assert "FAILED test_inner.py::test_hangs" in run.stdout
    assert "1 failed, 1 passed" in run.stdout


def test_a_worker_shares_the_process_group_and_the_scope_of_its_controller(tmp_path: Path) -> None:
    """tools/bounded.sh ends a process group and tools/capped.sh holds a cgroup, both from the
    controller's side: a worker in either of its own would escape the outer bound or the 10G."""
    run = inner_pytest(
        tmp_path,
        """\
        import os
        from pathlib import Path

        def test_where_the_worker_runs():
            parent = os.getppid()
            cgroup = Path("/proc/self/cgroup").read_text()
            Path(os.environ["INNER_DIR"], "worker.txt").write_text(
                f"{parent} {os.getpgid(0) == os.getpgid(parent)} "
                f"{cgroup == Path(f'/proc/{parent}/cgroup').read_text()}"
            )
        """,
        *XDIST,
        cap_gb="8",
    )
    assert run.returncode == 0, run.stdout + run.stderr
    parent, same_group, same_scope = (tmp_path / "worker.txt").read_text().split()
    assert int(parent) == run.pid, "the test did not run in a worker of the inner pytest"
    assert same_group == "True", "a worker is outside its controller's process group"
    assert same_scope == "True", "a worker is outside its controller's cgroup"


class Interrupted(Exception):
    """Stands for what pytest-timeout raises inside a test body when its limit is reached."""


def test_the_inner_process_is_killed_and_reaped_whatever_ends_the_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a slow runner the per-test timeout of this run can fire while it waits for an inner
    pytest, and that is an exception from `communicate`, not subprocess.TimeoutExpired. The inner
    process, which waits for half a minute, must be dead and reaped when the exception leaves."""
    started: list[subprocess.Popen[str]] = []

    class Interrupting(subprocess.Popen[str]):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            started.append(self)

        def communicate(self, input: str | None = None, timeout: float | None = None) -> Any:
            if timeout is not None:  # the wait with a deadline; the cleanup waits without one
                raise Interrupted
            return super().communicate(input)

    monkeypatch.setattr(subprocess, "Popen", Interrupting)
    try:
        with pytest.raises(Interrupted):
            inner_pytest(
                tmp_path,
                f"""\
                import threading

                def test_waits():
                    threading.Event().wait({INNER_WAIT_S})
                """,
            )
        assert len(started) == 1
        assert started[0].poll() == -signal.SIGKILL, "the inner pytest was left running"
    finally:
        for process in started:  # whatever the assertions found, leave nothing behind
            if process.poll() is None:
                process.kill()
                process.wait()


def test_the_wait_for_an_inner_pytest_ends_before_the_per_test_timeout_with_room_to_clean_up(
    request: pytest.FixtureRequest,
) -> None:
    seconds = float(request.config.getini("timeout"))
    assert seconds >= 2 * RUN_DEADLINE_S, "the deadline must pass well before pytest-timeout fires"
    assert RUN_DEADLINE_S >= 2 * INNER_WAIT_S, "an inner run may take as long as its own wait"


def test_over_the_cap_the_watchdog_signals_no_process_and_only_ends_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The branch where the watchdog's own process is over the cap, with a live child: whatever
    sends a signal is intercepted, and none may be called. A stop_descendants brought back here
    would signal pids read from /proc, which may have been reused by someone else's process."""
    signals: list[str] = []

    def recorder(name: str) -> Callable[..., None]:
        def record(*args: object, **kwargs: object) -> None:
            signals.append(f"{name}{args}")

        return record

    exits: list[int] = []
    for owner, name in [
        (os, "kill"),
        (os, "killpg"),
        (signal, "pthread_kill"),
        (signal, "raise_signal"),
        (subprocess.Popen, "send_signal"),
        (subprocess.Popen, "terminate"),
        (subprocess.Popen, "kill"),
    ]:
        monkeypatch.setattr(owner, name, recorder(f"{owner.__name__}.{name}"))
    monkeypatch.setattr(os, "_exit", exits.append)

    reader, writer = os.pipe()
    child = subprocess.Popen(
        [sys.executable, "-c", "print('ready', flush=True); import sys; sys.stdin.read()"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert child.stdin is not None
    assert child.stdout is not None
    try:
        assert child.stdout.readline().strip() == "ready"  # a live descendant exists now
        watch = memwatch.MemoryWatch(1, writer)
        watch.current = "tests/test_x.py::test_over"
        assert watch.watchdog.check() is True  # this very process is far over a cap of 1 byte
        os.close(writer)
        written = os.read(reader, 4096).decode()
    finally:
        monkeypatch.undo()  # the signal senders are real again for the cleanup
        child.stdin.close()
        child.wait(timeout=30)
        child.stdout.close()
        os.close(reader)
    assert exits == [memwatch.EXIT_CODE]
    assert written.startswith("MEMORY CAP: tests/test_x.py::test_over used ")
    assert signals == [], "the watchdog signalled a process"
    assert child.returncode == 0, "the child did not end by itself, as its stdin closed"
