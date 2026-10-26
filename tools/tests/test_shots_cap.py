"""frontend/shots/capped.sh, the one helper that puts Chrome and the detector of `make shots` under
a memory cap, and its two callers. Each test runs it with a fake systemd-run on PATH, or none."""

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CAPPED = ROOT / "frontend" / "shots" / "capped.sh"
BASH = shutil.which("bash") or "/usr/bin/bash"
CAP_OPTIONS = "--user --scope -q -p MemoryMax=6G -p MemorySwapMax=0"

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
    tmp_path: Path, bin_dir: Path, *command: str, probe: int = 0
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run capped.sh with PATH holding only `bin_dir`; return the result and the fake's log."""
    log = tmp_path / "systemd-run.log"
    env = {"PATH": str(bin_dir), "FAKE_LOG": str(log), "FAKE_PROBE": str(probe)}
    done = subprocess.run(
        [BASH, str(CAPPED), *command],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return done, log.read_text().splitlines() if log.exists() else []


def test_it_runs_the_command_under_the_cap_when_systemd_can_make_one(tmp_path: Path) -> None:
    done, log = run_capped(
        tmp_path,
        fake_bin(tmp_path, with_systemd_run=True),
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


def test_it_falls_back_when_the_manager_refuses_the_scope(tmp_path: Path) -> None:
    done, log = run_capped(
        tmp_path,
        fake_bin(tmp_path, with_systemd_run=True),
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
        sys.executable,
        "-c",
        "print('ran without systemd')",
    )
    assert done.returncode == 0, done.stderr
    assert "ran without systemd" in done.stdout
    assert log == []


def test_it_needs_a_command(tmp_path: Path) -> None:
    done, _ = run_capped(tmp_path, fake_bin(tmp_path, with_systemd_run=False))
    assert done.returncode == 64
    assert "usage" in done.stderr


def test_the_detector_runs_under_the_cap(tmp_path: Path) -> None:
    """run_detect puts the Impeccable detector under capped.sh: the fake systemd-run sees it."""
    bin_dir = fake_bin(tmp_path, with_systemd_run=True)
    detector = tmp_path / "impeccable"
    detector.write_text("#!/bin/sh\necho '[]'\n")
    detector.chmod(0o755)
    out = tmp_path / "out"
    out.mkdir()
    # PATH: the fake first, then the real one, which `setsid` and `timeout` need
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_LOG": str(tmp_path / "log"),
        "IMPECCABLE_BROWSER": "unused",
    }
    command = f'source frontend/shots/detect.sh; run_detect "{detector}" http://x/ "{out}" 30'
    done = subprocess.run(
        [BASH, "-c", command], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 0, done.stderr
    assert (out / "detect.json").read_text() == "[]\n"
    log = (tmp_path / "log").read_text().splitlines()
    assert any(line.startswith(CAP_OPTIONS) and f"-- {detector} detect" in line for line in log)


def test_the_captures_run_under_the_cap() -> None:
    """The capture's node process, which holds Chrome, starts through capped.sh."""
    text = (ROOT / "frontend" / "shots" / "run.sh").read_text()
    assert "bash frontend/shots/capped.sh \\\n  node frontend/shots/capture.ts" in text
