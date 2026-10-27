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


def make_dry_run(*variables: str, target: str = "check-python") -> list[str]:
    """The commands `make <target>` would run, one per line, as `make -n` prints them."""
    # Isolate the dry run from the caller's make: an outer `make check WORKERS=0` exports
    # WORKERS and carries it in MAKEFLAGS/MAKEOVERRIDES, which would otherwise override the
    # defaults these tests pin. Only `variables` given here may set them.
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES", "WORKERS", "RUN_TIMEOUT"}
    }
    env["MAKEFLAGS"] = ""
    env["MAKEOVERRIDES"] = ""
    done = subprocess.run(
        ["make", "-n", "--no-print-directory", "-C", str(ROOT), target, *variables],
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
        r"bash tools/bounded\.sh 900 bash tools/capped\.sh 10G env .* uv run --frozen pytest "
        r"-n 8 --max-worker-restart=0 --ignore=tools/tests/test_eslint_boundaries\.py",
        lines[0],
    ), lines[0]


def test_the_dry_run_ignores_the_callers_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Running the suite through `make check WORKERS=0` (or 4) exports that value to pytest;
    the dry run must still pin the default 8 unless a test passes WORKERS itself."""
    monkeypatch.setenv("WORKERS", "0")
    monkeypatch.setenv("RUN_TIMEOUT", "7")
    monkeypatch.setenv("MAKEFLAGS", "w -- WORKERS=0 RUN_TIMEOUT=7")
    monkeypatch.setenv("MAKEOVERRIDES", "WORKERS=0")
    (line,) = [line for line in make_dry_run(target="check") if "--ignore=" in line]
    assert " -n 8 --max-worker-restart=0 " in line, line
    assert line.startswith("bash tools/bounded.sh 900 "), line


def test_the_outer_bound_can_be_set_for_one_run() -> None:
    lines = [line for line in make_dry_run("RUN_TIMEOUT=7") if " pytest" in line]
    assert len(lines) == 1, lines
    assert lines[0].startswith("bash tools/bounded.sh 7 bash tools/capped.sh 10G "), lines[0]


def test_the_workers_are_one_variable_and_a_crashed_worker_ends_the_run() -> None:
    """WORKERS=0 is one process, as before xdist. --max-worker-restart=0 is what turns a worker
    that memwatch ended into the end of the run (tools/tests/test_memwatch.py runs that)."""
    for workers in ("0", "3"):
        (line,) = [line for line in make_dry_run(f"WORKERS={workers}") if " pytest" in line]
        assert f" -n {workers} --max-worker-restart=0 " in line, line


BOUNDARIES_FILE = "tools/tests/test_eslint_boundaries.py"


def test_the_boundaries_test_runs_alone_in_the_frontend_group_not_the_python_one() -> None:
    """It writes probe files into frontend/src while tsc, ESLint and Prettier read it, and `make
    check` runs the groups at once: it runs after those gates, in one process, and once."""
    python = make_dry_run()
    named = [line for line in python if BOUNDARIES_FILE in line]
    assert len(named) == 1 and f"--ignore={BOUNDARIES_FILE}" in named[0], named
    frontend = make_dry_run(target="check-frontend")
    (run,) = [line for line in frontend if BOUNDARIES_FILE in line]
    assert re.fullmatch(
        rf"bash tools/bounded\.sh 900 bash tools/capped\.sh 10G env .* uv run --frozen pytest "
        rf"{re.escape(BOUNDARIES_FILE)}",
        run,
    ), run
    assert frontend.index(run) > max(
        index for index, line in enumerate(frontend) if "pnpm" in line
    ), "it runs before a frontend gate has finished reading src/"


@pytest.mark.parametrize(
    ("target", "with_secrets"), [("check", True), ("check-ci", False)], ids=["check", "check-ci"]
)
def test_check_runs_every_group_and_only_check_scans_for_secrets(
    target: str, with_secrets: bool
) -> None:
    lines = "\n".join(make_dry_run(target=target))
    for command in (
        "ruff check .",
        "pyright",
        "lint-imports",
        "uv run --frozen pytest -n 8",
        "generate.py --check",
        "typecheck",
        "format:check",
        " test",
        f"pytest {BOUNDARIES_FILE}",
    ):
        assert command in lines, f"{target} does not run {command!r}"
    assert ("gitleaks git" in lines) is with_secrets


def test_check_and_check_ci_derive_from_the_one_gates_list() -> None:
    """A gate added to GATES lands in both: overriding GATES to one gate runs only that gate
    in both, and check-ci only ever filters check-secrets out of the same list."""
    for target in ("check", "check-ci"):
        lines = "\n".join(make_dry_run("GATES=check-contracts", target=target))
        assert "generate.py --check" in lines, target
        assert "ruff check ." not in lines, target
        assert "gitleaks git" not in lines, target
    check = "\n".join(make_dry_run("GATES=check-secrets", target="check"))
    check_ci = "\n".join(make_dry_run("GATES=check-secrets", target="check-ci"))
    assert "gitleaks git" in check
    assert "gitleaks git" not in check_ci


def test_check_runs_its_groups_in_parallel_with_their_output_kept_together() -> None:
    text = (ROOT / "Makefile").read_text()
    assert "-j4 --output-sync=target" in text
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "run: make check-ci" in ci, "CI does not run the groups the way make check does"
