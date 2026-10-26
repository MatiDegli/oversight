"""tools/bounded.sh, the outer time bound of `make check`'s pytest run: it passes the command's
status through, and when the bound is what ended the command it prints one line naming the bound.
The real `timeout` runs against a command that waits forever, with a bound of one second, so each
test waits for the bound itself and for nothing else. A fake `timeout` on PATH logs the arguments
the script gives it."""

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BOUNDED = ROOT / "tools" / "bounded.sh"
BASH = shutil.which("bash") or "/usr/bin/bash"
LINE = "TIME BOUND: the run passed its outer bound of 1 s and was ended (exit {status})"

WAITS_FOREVER = "import threading; threading.Event().wait()"
IGNORES_TERM = (
    "import signal, threading; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
    "print('ready', flush=True); threading.Event().wait()"
)

# A timeout that logs its arguments and exits with $FAKE_STATUS, without running the command.
FAKE_TIMEOUT = textwrap.dedent(
    """\
    #!/bin/bash
    echo "$*" >> "$FAKE_LOG"
    exit "${FAKE_STATUS:-0}"
    """
)


def run_bounded(
    *arguments: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    base = {"PATH": os.environ["PATH"]} | (env or {})
    return subprocess.run(
        [BASH, str(BOUNDED), *arguments],
        env=base,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )


def bound_lines(stderr: str) -> list[str]:
    return [line for line in stderr.splitlines() if line.startswith("TIME BOUND:")]


def test_a_command_that_outlasts_the_bound_is_ended_and_the_bound_is_named() -> None:
    done = run_bounded("1", sys.executable, "-c", WAITS_FOREVER)
    assert done.returncode == 124, done.stderr
    lines = bound_lines(done.stderr)
    assert len(lines) == 1, done.stderr
    assert lines[0].startswith(LINE.format(status=124))
    assert "RUN_TIMEOUT" in lines[0]


def test_a_command_that_must_be_killed_names_the_bound_too() -> None:
    done = run_bounded("1", sys.executable, "-c", IGNORES_TERM, env={"BOUND_GRACE_S": "1"})
    assert done.returncode == 137, done.stderr
    lines = bound_lines(done.stderr)
    assert len(lines) == 1, done.stderr
    assert lines[0].startswith(LINE.format(status=137))


@pytest.mark.parametrize("status", [0, 1, 3])
def test_a_command_that_ends_by_itself_keeps_its_status_and_gets_no_line(status: int) -> None:
    done = run_bounded("900", sys.executable, "-c", f"import sys; sys.exit({status})")
    assert done.returncode == status
    assert bound_lines(done.stderr) == []


@pytest.mark.parametrize("status", [124, 137])
def test_the_statuses_of_timeout_from_the_command_itself_are_not_blamed_on_the_bound(
    status: int,
) -> None:
    done = run_bounded("900", sys.executable, "-c", f"import sys; sys.exit({status})")
    assert done.returncode == status
    assert bound_lines(done.stderr) == []


def test_the_bound_gives_a_grace_of_30_seconds_before_the_kill_and_passes_the_command_on(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "timeout"
    fake.write_text(FAKE_TIMEOUT)
    fake.chmod(0o755)
    log = tmp_path / "timeout.log"
    done = run_bounded(
        "900",
        "pytest",
        "-q",
        env={"PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_LOG": str(log), "FAKE_STATUS": "5"},
    )
    assert done.returncode == 5
    assert log.read_text().splitlines() == ["--kill-after=30 900 pytest -q"]


@pytest.mark.parametrize(
    "arguments", [(), ("900",), ("0", "true"), ("soon", "true"), ("1.5", "true")]
)
def test_it_needs_whole_seconds_and_a_command(arguments: tuple[str, ...]) -> None:
    done = run_bounded(*arguments)
    assert done.returncode == 64
    assert "usage" in done.stderr
