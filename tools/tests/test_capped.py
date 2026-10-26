"""The hard layer of the test caps: tools/capped.sh, which puts a command in a systemd scope with a
kernel memory limit when this machine can make one, and the Makefile line that runs pytest through
it under an outer time bound (tools/bounded.sh, in test_bounded.py). Each test runs the helper with
a fake systemd-run on PATH, or none."""

import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CAPPED = ROOT / "tools" / "capped.sh"
BASH = shutil.which("bash") or "/usr/bin/bash"
CAP_OPTIONS = "--user --scope -q -p MemoryMax=10G -p MemorySwapMax=0"

# A systemd-run that logs its arguments. It exits with $FAKE_PROBE for the probe (the call that ends
# in `true`), and otherwise runs the command after `--`, as the real one does in place.
FAKE_SYSTEMD_RUN = textwrap.dedent(
    """\
    #!/bin/bash
    echo "$*" >> "$FAKE_LOG"
    if [ "${!#}" = "true" ]; then exit "${FAKE_PROBE:-0}"; fi
    while [ "$1" != "--" ]; do shift; done
    shift
    exec "$@"
    """
)


def fake_bin(tmp_path: Path, *, with_systemd_run: bool) -> Path:
    directory = tmp_path / "bin"
    directory.mkdir()
    if with_systemd_run:
        script = directory / "systemd-run"
        script.write_text(FAKE_SYSTEMD_RUN)
        script.chmod(0o755)
    return directory


def run_capped(
    tmp_path: Path, bin_dir: Path, *arguments: str, probe: int = 0
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run capped.sh with PATH holding only `bin_dir`; return the result and the fake's log."""
    log = tmp_path / "systemd-run.log"
    env = {"PATH": str(bin_dir), "FAKE_LOG": str(log), "FAKE_PROBE": str(probe)}
    done = subprocess.run(
        [BASH, str(CAPPED), *arguments],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return done, log.read_text().splitlines() if log.exists() else []


def test_it_runs_the_command_under_the_limit_it_was_given(tmp_path: Path) -> None:
    done, log = run_capped(
        tmp_path,
        fake_bin(tmp_path, with_systemd_run=True),
        "10G",
        sys.executable,
        "-c",
        "import sys; print('ran', sys.argv[1:]); sys.exit(7)",
        "a b",
    )
    assert done.returncode == 7, "the command's own exit status comes through"
    assert "ran ['a b']" in done.stdout
    assert log == [
        f"{CAP_OPTIONS} true",
        f"{CAP_OPTIONS} -- {sys.executable} -c import sys; print('ran', sys.argv[1:]); "
        "sys.exit(7) a b",
    ]


def test_another_limit_is_passed_on_as_given(tmp_path: Path) -> None:
    done, log = run_capped(
        tmp_path, fake_bin(tmp_path, with_systemd_run=True), "512M", sys.executable, "-c", "pass"
    )
    assert done.returncode == 0, done.stderr
    assert log[0] == "--user --scope -q -p MemoryMax=512M -p MemorySwapMax=0 true"


def test_it_falls_back_when_the_manager_refuses_the_scope(tmp_path: Path) -> None:
    done, log = run_capped(
        tmp_path,
        fake_bin(tmp_path, with_systemd_run=True),
        "10G",
        sys.executable,
        "-c",
        "print('ran without a cap')",
        probe=1,
    )
    assert done.returncode == 0, done.stderr
    assert "ran without a cap" in done.stdout
    assert log == [f"{CAP_OPTIONS} true"], "only the probe went to systemd-run"


def test_it_falls_back_when_systemd_run_is_missing(tmp_path: Path) -> None:
    done, log = run_capped(
        tmp_path,
        fake_bin(tmp_path, with_systemd_run=False),
        "10G",
        sys.executable,
        "-c",
        "print('ran without systemd')",
    )
    assert done.returncode == 0, done.stderr
    assert "ran without systemd" in done.stdout
    assert log == []


@pytest.mark.parametrize("arguments", [(), ("10G",)])
def test_it_needs_a_limit_and_a_command(tmp_path: Path, arguments: tuple[str, ...]) -> None:
    done, _ = run_capped(tmp_path, fake_bin(tmp_path, with_systemd_run=False), *arguments)
    assert done.returncode == 64
    assert "usage" in done.stderr


def make_dry_run(*variables: str) -> list[str]:
    """The commands `make check-python` would run, one per line, as `make -n` prints them."""
    env = {k: v for k, v in os.environ.items() if k not in {"MAKEFLAGS", "MFLAGS", "MAKELEVEL"}}
    done = subprocess.run(
        ["make", "-n", "--no-print-directory", "-C", str(ROOT), "check-python", *variables],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.splitlines()


def test_make_runs_pytest_through_the_helper_under_the_outer_bound() -> None:
    """The whole run, setup and teardown included, goes through bounded.sh, which says so when its
    bound ends it, and runs in the scope: every line that runs pytest says both. The bound is 900 s
    because GitHub cancels the CI job at 20 minutes, with install and lint before the tests."""
    lines = [line for line in make_dry_run() if " pytest" in line]
    assert len(lines) == 1, lines
    assert re.fullmatch(
        r"bash tools/bounded\.sh 900 bash tools/capped\.sh 10G env .* uv run --frozen pytest",
        lines[0],
    ), lines[0]


def test_the_outer_bound_can_be_set_for_one_run() -> None:
    lines = [line for line in make_dry_run("RUN_TIMEOUT=7") if " pytest" in line]
    assert len(lines) == 1, lines
    assert lines[0].startswith("bash tools/bounded.sh 7 bash tools/capped.sh 10G "), lines[0]
