"""A worker's whole-protocol dump reaches the log without changing the test's outcome."""

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

BOUNDED = Path(__file__).resolve().parents[1] / "bounded.sh"


@pytest.mark.parametrize("hang", [True, False], ids=["bounded-hang", "passing-teardown"])
def test_a_worker_names_its_slow_teardown_in_the_log(tmp_path: Path, hang: bool) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_inner.py").write_text(
        textwrap.dedent(
            f"""\
            import time
            import pytest

            @pytest.fixture
            def slow_teardown():
                yield
                time.sleep({60 if hang else 2})

            def test_inner(slow_teardown, worker_id):
                assert worker_id in {{"gw0", "gw1"}}
            """
        )
    )
    # Use the same outer process-group bound as PYTEST_RUN. capped.sh is omitted: this
    # tiny run checks stderr transport, without depending on a user systemd manager.
    # Load only xdist; no inherited addopts, repository conftest or third-party plugins.
    done = subprocess.run(
        [
            "bash",
            str(BOUNDED),
            "5",
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "xdist.plugin",
            "-p",
            "no:cacheprovider",
            "-n",
            "2",
            "--max-worker-restart=0",
            "--confcutdir",
            str(tmp_path),
            "-o",
            "faulthandler_timeout=1",
        ],
        cwd=tmp_path,
        env={
            "PATH": os.environ["PATH"],
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "BOUND_GRACE_S": "1",
        },
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=15,
    )
    assert done.returncode == (124 if hang else 0), done.stdout
    assert "Timeout (0:00:01)!" in done.stdout, done.stdout
    assert re.search(r'File ".*/test_inner\.py", line \d+ in slow_teardown', done.stdout), (
        done.stdout
    )
    bound_line = (
        "TIME BOUND: the run passed its outer bound of 5 s and was ended (exit 124); "
        "RUN_TIMEOUT sets it"
    )
    if hang:
        assert bound_line in done.stdout, done.stdout
    else:
        assert "1 passed" in done.stdout, done.stdout
        assert "TIME BOUND:" not in done.stdout, done.stdout
