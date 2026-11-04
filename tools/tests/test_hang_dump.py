"""A worker's whole-protocol dump reaches the log without changing the test's outcome."""

import asyncio
import os
import re
import signal
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

BOUNDED = Path(__file__).resolve().parents[1] / "bounded.sh"
HANG_BOUND = 8
HANG_SLEEP = 20
PASS_BOUND = 90
PASS_SLEEP = 2
OUTER_MARGIN_S = 10


async def _run_worker(command: list[str], **kwargs: Any) -> int:
    timeout = kwargs.pop("timeout")
    process = await asyncio.create_subprocess_exec(*command, **kwargs)
    try:
        return await asyncio.wait_for(process.wait(), timeout)
    except TimeoutError:
        process.kill()
        await asyncio.wait_for(process.wait(), OUTER_MARGIN_S)
        raise


@pytest.mark.parametrize("hang", [True, False], ids=["bounded-hang", "passing-teardown"])
def test_a_worker_names_its_slow_teardown_in_the_log(tmp_path: Path, hang: bool) -> None:
    bound = HANG_BOUND if hang else PASS_BOUND
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "conftest.py").write_text(
        "import faulthandler\nimport signal\n\nfaulthandler.register(signal.SIGTERM, chain=True)\n"
    )
    (tmp_path / "test_inner.py").write_text(
        textwrap.dedent(
            f"""\
            import time
            import pytest

            @pytest.fixture
            def slow_teardown():
                yield
                time.sleep({HANG_SLEEP if hang else PASS_SLEEP})

            def test_inner(slow_teardown, worker_id):
                assert worker_id in {{"gw0", "gw1"}}
            """
        )
    )
    # Use the same outer process-group bound as PYTEST_RUN. capped.sh is omitted: this
    # tiny run checks stderr transport, without depending on a user systemd manager.
    # Load only xdist; no inherited addopts, repository conftest or third-party plugins.
    # Workers inherit the diagnostic FD. A regular file avoids pipe backpressure and
    # waiting for EOF from a surviving worker when the bounded controller exits.
    # This does not resolve the observed post-100% hang; SIGTERM dumps diagnose it.
    with (tmp_path / "inner.log").open("w") as log:
        code = asyncio.run(
            _run_worker(
                [
                    "bash",
                    str(BOUNDED),
                    str(bound),
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
                stdin=asyncio.subprocess.DEVNULL,
                stdout=log,
                stderr=asyncio.subprocess.STDOUT,
                timeout=bound + OUTER_MARGIN_S,
            )
        )
    output = (tmp_path / "inner.log").read_text()
    assert code == (124 if hang else 0), output
    assert "Timeout (0:00:01)!" in output, output
    assert re.search(r'File ".*/test_inner\.py", line \d+ in slow_teardown', output), output
    bound_line = (
        f"TIME BOUND: the run passed its outer bound of {bound} s and was ended (exit 124); "
        "RUN_TIMEOUT sets it"
    )
    if hang:
        assert bound_line in output, output
    else:
        assert "1 passed" in output, output
        assert "TIME BOUND:" not in output, output


@pytest.mark.parametrize("hang", [True, False], ids=["bounded-hang", "passing-teardown"])
def test_worker_probe_allows_startup_and_bounds_an_orphaned_teardown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hang: bool
) -> None:
    # Pin the timing budget without relying on a loaded runner to reproduce a slow startup.
    # The integration test above still runs real xdist workers and checks their stderr.
    run_worker = _run_worker

    async def run_probe(command: list[str], **kwargs: Any) -> int:
        bound = int(command[2])
        assert bound == (HANG_BOUND if hang else PASS_BOUND)
        assert isinstance(kwargs["timeout"], int)
        assert kwargs["timeout"] >= bound + OUTER_MARGIN_S
        source = (tmp_path / "test_inner.py").read_text()
        assert f"time.sleep({HANG_SLEEP if hang else PASS_SLEEP})" in source
        # Execute the generated diagnostic hook, then deliver a real SIGTERM. The
        # dump must reach stderr and chain=True must retain signal termination.
        with (tmp_path / "term.log").open("w") as log:
            code = await run_worker(
                [
                    sys.executable,
                    "-c",
                    "import os, runpy, signal; runpy.run_path('conftest.py'); "
                    "os.kill(os.getpid(), signal.SIGTERM)",
                ],
                cwd=tmp_path,
                stdout=log,
                stderr=asyncio.subprocess.STDOUT,
                timeout=OUTER_MARGIN_S,
            )
        assert code == -signal.SIGTERM
        assert 'File "<string>", line 1 in <module>' in (tmp_path / "term.log").read_text()
        output = 'Timeout (0:00:01)!\nFile "/probe/test_inner.py", line 8 in slow_teardown\n'
        output += (
            f"TIME BOUND: the run passed its outer bound of {bound} s and was ended "
            "(exit 124); RUN_TIMEOUT sets it"
            if hang
            else "1 passed"
        )
        kwargs["stdout"].write(output)
        return 124 if hang else 0

    monkeypatch.setattr(sys.modules[__name__], "_run_worker", run_probe)
    test_a_worker_names_its_slow_teardown_in_the_log(tmp_path, hang)
