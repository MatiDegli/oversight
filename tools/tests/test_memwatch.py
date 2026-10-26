"""The soft caps of a test run: the memory watchdog (tools/memwatch.py) and pytest-timeout.

The runaway tests run in a pytest of their own, in a temporary directory, with a cap of a few
hundred MiB and a bounded allocation. If a cap failed to fire, the inner test would end by itself
after a bounded wait, with exit code 0, and the assertions here would fail. The hard layer, the
systemd scope around `make check`'s pytest, is in test_capped.py.
"""

import os
import subprocess
import sys
import textwrap
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import memwatch

TOOLS = Path(__file__).resolve().parents[1]
MIB = 2**20
INNER_CAP_GB = "0.3"  # the cap of the inner runs: 307 MB, above the ~80 MB of a bare pytest
INNER_MIB = 600  # what an inner test, or its child, holds: twice the cap
INNER_WAIT_S = 30  # how long an inner test waits for the cap to end it
RUN_DEADLINE_S = 120  # how long this test waits for an inner pytest


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
    assert watch.watchdog.cap_bytes == memwatch.parse_cap(os.environ.get(memwatch.ENV_CAP))
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
    repository's configuration. It is killed if it outlasts the deadline."""
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
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise
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
