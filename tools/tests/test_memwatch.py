"""The memory cap and the time cap of a test run (tools/memwatch.py, pytest-timeout).

The runaway tests run in a pytest of their own, in a temporary directory, with a cap of a few
hundred MiB and a bounded allocation. If a cap failed to fire, the inner test would end by itself
after a bounded wait, with exit code 0, and the assertions here would fail.
"""

import os
import select
import subprocess
import sys
import textwrap
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import memwatch

TOOLS = Path(__file__).resolve().parents[1]
MIB = 2**20
CHILD_MIB = 200  # what a child holds in the process-tree tests
INNER_CAP_GB = "0.3"  # the cap of the inner runs: 307 MB, above the ~80 MB of a bare pytest
INNER_MIB = 600  # what an inner test allocates, twice the cap
INNER_WAIT_S = 30  # how long an inner test, or its child, waits for the cap to end it
RUN_DEADLINE_S = 120  # how long this test waits for an inner pytest

# Starts a process that holds CHILD_MIB resident, says "ready <pid>" on stdout, and waits for its
# stdin to close.
HOLDER = (
    f"import os, sys; blob = b'x' * ({CHILD_MIB} * 2**20); "
    "print('ready', os.getpid(), flush=True); sys.stdin.read()"
)
# Starts HOLDER as its own child, so the holder is the grandchild of whoever ran this.
SPAWNER = f"import subprocess, sys; subprocess.run([sys.executable, '-c', {HOLDER!r}])"


@pytest.fixture
def spawn() -> Iterator[Callable[[str], tuple[subprocess.Popen[str], int]]]:
    """Run a script with stdin and stdout piped; wait for its "ready <pid>" line and return the
    process and that pid. Every process is killed and reaped at the end."""
    started: list[subprocess.Popen[str]] = []

    def run(script: str) -> tuple[subprocess.Popen[str], int]:
        process = subprocess.Popen(
            [sys.executable, "-c", script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        started.append(process)
        assert process.stdout is not None
        line = process.stdout.readline().split()  # blocks until the holder has its memory
        assert line[:1] == ["ready"], f"the holder did not start: {line}"
        return process, int(line[1])

    yield run
    for process in started:
        process.kill()
        process.wait(timeout=10)


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


def test_rss_is_read_from_proc_and_a_missing_process_has_none() -> None:
    assert memwatch.rss_bytes(os.getpid()) > 10 * MIB
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait(timeout=30)
    assert memwatch.rss_bytes(gone.pid) == 0


def test_the_tree_counts_a_child_and_a_grandchild(
    spawn: Callable[[str], tuple[subprocess.Popen[str], int]],
) -> None:
    own = memwatch.rss_bytes(os.getpid())
    child, child_pid = spawn(HOLDER)
    assert child_pid in memwatch.descendants(os.getpid())
    assert memwatch.tree_rss_bytes(os.getpid()) - own >= (CHILD_MIB - 20) * MIB
    assert child.stdin is not None
    child.stdin.close()
    child.wait(timeout=30)

    spawner, grandchild_pid = spawn(SPAWNER)
    found = memwatch.descendants(os.getpid())
    assert spawner.pid in found
    assert grandchild_pid in found
    own = memwatch.rss_bytes(os.getpid())
    assert memwatch.tree_rss_bytes(os.getpid()) - own >= (CHILD_MIB - 20) * MIB


def test_stopping_the_descendants_kills_every_level(
    spawn: Callable[[str], tuple[subprocess.Popen[str], int]],
) -> None:
    spawner, _ = spawn(SPAWNER)
    assert spawner.stdout is not None
    memwatch.stop_descendants(os.getpid())
    spawner.wait(timeout=30)
    assert spawner.returncode == -9
    # the grandchild shares the spawner's stdout: the pipe ends only when it has died too
    assert wait_for_eof(spawner.stdout.fileno()), "the grandchild outlived the call"


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


def inner_pytest(
    directory: Path,
    source: str,
    *args: str,
    cap_gb: str = INNER_CAP_GB,
    pass_fds: tuple[int, ...] = (),
) -> subprocess.CompletedProcess[str]:
    """Run `source` as a test file in a fresh pytest with the memwatch plugin, outside this
    repository's configuration."""
    (directory / "test_inner.py").write_text(textwrap.dedent(source))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "GIT_"))}
    env |= {"PYTHONPATH": str(TOOLS), memwatch.ENV_CAP: cap_gb, "INNER_DIR": str(directory)}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "memwatch", "-p", "no:cacheprovider", *args],
        cwd=directory,
        env=env,
        capture_output=True,
        text=True,
        timeout=RUN_DEADLINE_S,
        pass_fds=pass_fds,
    )


def wait_for_eof(descriptor: int, deadline_s: float = 15.0) -> bool:
    """Whether the read end of a pipe reaches end of file within the deadline: every process that
    held its write end is gone. The wait is on that event, so it does not depend on whose child
    the processes were."""
    while True:
        readable, _, _ = select.select([descriptor], [], [], deadline_s)
        if not readable:
            return False
        if os.read(descriptor, 4096) == b"":
            return True


def memory_cap_lines(stderr: str) -> list[str]:
    return [line for line in stderr.splitlines() if line.startswith("MEMORY CAP:")]


def test_a_test_that_outgrows_the_cap_ends_the_run_naming_itself(tmp_path: Path) -> None:
    result = inner_pytest(
        tmp_path,
        f"""\
        import threading

        def test_grows():
            blob = b"x" * ({INNER_MIB} * 2**20)
            threading.Event().wait({INNER_WAIT_S})  # the watchdog ends the run long before this
        """,
    )
    assert result.returncode == memwatch.EXIT_CODE, result.stdout + result.stderr
    lines = memory_cap_lines(result.stderr)
    assert len(lines) == 1, result.stderr
    assert "test_inner.py::test_grows" in lines[0]
    assert "0.30 GiB" in lines[0]
    assert memwatch.ENV_CAP in lines[0]


def test_a_child_that_outgrows_the_cap_ends_the_run_and_dies_with_it(tmp_path: Path) -> None:
    # The write end of this pipe goes to the inner pytest and from it to the runaway child: the
    # read end reaches end of file only when both are gone.
    read_end, write_end = os.pipe()
    try:
        result = inner_pytest(
            tmp_path,
            f"""\
            import os, subprocess, sys, threading
            from pathlib import Path

            # The child does not end when its parent does: only the watchdog's kill ends it early.
            HOLDER = (
                "import os, threading; blob = b'x' * ({INNER_MIB} * 2**20); "
                "print(os.getpid(), flush=True); threading.Event().wait({INNER_WAIT_S})"
            )

            def test_child_grows():
                child = subprocess.Popen([sys.executable, "-c", HOLDER],
                                         stdout=subprocess.PIPE, text=True,
                                         pass_fds=({write_end},))
                assert child.stdout is not None
                Path(os.environ["INNER_DIR"], "child.pid").write_text(child.stdout.readline())
                threading.Event().wait({INNER_WAIT_S})  # the watchdog ends the run long before this
            """,
            pass_fds=(write_end,),
        )
    finally:
        os.close(write_end)
    pid_file = tmp_path / "child.pid"
    assert pid_file.exists(), result.stdout + result.stderr
    child_gone = wait_for_eof(read_end)
    os.close(read_end)
    if not child_gone:  # the failure case: do not leave the runaway behind
        os.kill(int(pid_file.read_text()), 9)
    assert result.returncode == memwatch.EXIT_CODE, result.stdout + result.stderr
    lines = memory_cap_lines(result.stderr)
    assert len(lines) == 1, result.stderr
    assert "test_inner.py::test_child_grows" in lines[0]
    assert child_gone, "the runaway child outlived the run"


def test_a_bad_cap_in_the_environment_stops_the_run_before_any_test(tmp_path: Path) -> None:
    result = inner_pytest(tmp_path, "def test_never_runs(): ...\n", cap_gb="lots")
    assert result.returncode != 0
    assert memwatch.ENV_CAP in result.stderr
    assert "passed" not in result.stdout


def test_a_test_that_outlives_the_timeout_fails_alone_with_its_name(tmp_path: Path) -> None:
    result = inner_pytest(
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
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Failed: Timeout (>1.0s) from pytest-timeout." in result.stdout
    assert "FAILED test_inner.py::test_hangs" in result.stdout
    assert "1 failed, 1 passed" in result.stdout
