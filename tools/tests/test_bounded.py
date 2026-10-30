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


@pytest.mark.parametrize("status", [124, 137])
@pytest.mark.parametrize(
    ("ended", "names_bound"), [("11.00", True), ("10.99", True), ("10.98", False)]
)
def test_monotonic_elapsed_time_decides_even_when_seconds_stays_zero(
    tmp_path: Path, status: int, ended: str, names_bound: bool
) -> None:
    uptime = tmp_path / "uptime"
    uptime.write_text("10.00 0.00\n")
    bash_env = tmp_path / "bash-env"
    # Removing the special variable freezes it; the old SECONDS rule never names the bound.
    bash_env.write_text("unset SECONDS\nreadonly SECONDS=0\n")
    done = run_bounded(
        "1",
        sys.executable,
        "-c",
        "import pathlib, sys; pathlib.Path(sys.argv[1]).write_text(sys.argv[2]); "
        "sys.exit(int(sys.argv[3]))",
        str(uptime),
        f"{ended} 0.00\n",
        str(status),
        env={"BOUND_UPTIME_PATH": str(uptime), "BASH_ENV": str(bash_env)},
    )
    assert done.returncode == status, done.stderr
    expected = [f"{LINE.format(status=status)}; RUN_TIMEOUT sets it"] if names_bound else []
    assert bound_lines(done.stderr) == expected, done.stderr


@pytest.mark.parametrize("unavailable_at", ["start", "end", "both"])
@pytest.mark.parametrize("seconds", [0, 1])
def test_unavailable_monotonic_time_falls_back_to_seconds(
    tmp_path: Path, unavailable_at: str, seconds: int
) -> None:
    uptime = tmp_path / "uptime"
    if unavailable_at == "end":
        uptime.write_text("10.00 0.00\n")
    bash_env = tmp_path / "bash-env"
    bash_env.write_text(f"unset SECONDS\nreadonly SECONDS={seconds}\n")
    done = run_bounded(
        "1",
        sys.executable,
        "-c",
        "import pathlib, sys; pathlib.Path(sys.argv[1]).write_text(sys.argv[2]); sys.exit(124)",
        str(uptime),
        "11.00 0.00\n" if unavailable_at == "start" else "invalid\n",
        env={"BOUND_UPTIME_PATH": str(uptime), "BASH_ENV": str(bash_env)},
    )
    assert done.returncode == 124, done.stderr
    expected = [f"{LINE.format(status=124)}; RUN_TIMEOUT sets it"] if seconds else []
    assert bound_lines(done.stderr) == expected, done.stderr


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
