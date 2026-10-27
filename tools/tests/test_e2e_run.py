"""frontend/e2e/run.sh of `make e2e`: the preview server it starts never outlives it.

On 2026-10-27 a `vite preview` from an earlier e2e run stayed alive for minutes after the run
ended: Playwright's own webServer starts it in a process group of its own, so a run that
`timeout` ended left it behind. The script now owns the server, in its own process group, and
stops it by PID. Each test runs the real script over a fake frontend folder (a vite that
builds nothing and a preview that only listens, a playwright that fails or hangs) and checks
that the server's process is gone after the end the test chose. It needs a node, as the script does.
"""

import os
import shutil
import signal
import subprocess
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUN_SH = ROOT / "frontend" / "e2e" / "run.sh"
BASH = shutil.which("bash") or "/usr/bin/bash"
DEADLINE_S = 30

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="run.sh needs node")

# What the fake preview does: listen on the port it is given and say which process it is.
PREVIEW_JS = textwrap.dedent(
    """\
    const http = require("node:http");
    const fs = require("node:fs");
    const port = Number(process.argv[process.argv.indexOf("--port") + 1]);
    http.createServer().listen(port, "127.0.0.1", () => {
      fs.writeFileSync(process.env.FAKE_DIR + "/server.pid", String(process.pid));
    });
    """
)
# `build` builds nothing. The fake playwright says it started, then fails or waits to be ended.
FAKE_VITE = "#!/bin/sh\nexit 0\n"
FAKE_PLAYWRIGHT = textwrap.dedent(
    """\
    #!/bin/bash
    echo $$ > "$FAKE_DIR/playwright.pid"
    if [ "$FAKE_MODE" = fail ]; then exit 3; fi
    while true; do sleep 0.05; done
    """
)


def alive(pid: int) -> bool:
    """True for a running process; a zombie that only waits to be reaped is not."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return state != "Z"


def until(condition: object, what: str) -> None:
    """Wait for `condition()` for at most DEADLINE_S seconds; the failure names what never came."""
    stop = time.monotonic() + DEADLINE_S
    while not condition():  # type: ignore[operator]
        if time.monotonic() > stop:
            pytest.fail(f"gave up waiting for {what}")
        time.sleep(0.02)


def fake_frontend(tmp_path: Path) -> Path:
    frontend = tmp_path / "frontend"
    (frontend / "e2e").mkdir(parents=True)
    shutil.copy(RUN_SH, frontend / "e2e" / "run.sh")
    (frontend / "node_modules" / "vite" / "bin").mkdir(parents=True)
    (frontend / "node_modules" / "vite" / "bin" / "vite.js").write_text(PREVIEW_JS)
    (frontend / "node_modules" / ".bin").mkdir()
    for name, text in (("vite", FAKE_VITE), ("playwright", FAKE_PLAYWRIGHT)):
        script = frontend / "node_modules" / ".bin" / name
        script.write_text(text)
        script.chmod(0o755)
    return frontend


def run_until(tmp_path: Path, mode: str, how: str) -> tuple[int, int]:
    """Runs run.sh, ends it as `how` says, and returns its exit status and the server's pid."""
    frontend = fake_frontend(tmp_path)
    env = {**os.environ, "FAKE_DIR": str(tmp_path), "FAKE_MODE": mode}
    # Its own session, as under `timeout`, which makes the command a process group of its own.
    run = subprocess.Popen(
        [BASH, str(frontend / "e2e" / "run.sh")], env=env, start_new_session=True
    )
    try:
        until((tmp_path / "playwright.pid").exists, "the fake playwright to start")
        server = int((tmp_path / "server.pid").read_text())
        if mode == "hang":  # a failing run may already be over, and the server with it
            assert alive(server), "the server was up while playwright ran"
        if how == "term":  # what `timeout` sends the group when the bound passes
            os.killpg(run.pid, signal.SIGTERM)
        elif how == "kill":  # and what it sends 10 s later
            os.killpg(run.pid, signal.SIGKILL)
        status = run.wait(timeout=DEADLINE_S)
        until(lambda: not alive(server), "the preview server to be gone")
        return status, server
    finally:
        with_server = tmp_path / "server.pid"
        if with_server.exists() and alive(int(with_server.read_text())):
            os.kill(int(with_server.read_text()), signal.SIGKILL)
        if run.poll() is None:
            os.killpg(run.pid, signal.SIGKILL)
            run.wait(timeout=DEADLINE_S)


def test_the_server_stops_when_the_tests_fail(tmp_path: Path) -> None:
    status, _ = run_until(tmp_path, "fail", "none")
    assert status == 3, "the tests' own exit status comes through"


def test_the_server_stops_when_the_time_bound_ends_the_run(tmp_path: Path) -> None:
    status, _ = run_until(tmp_path, "hang", "term")
    assert status == 143


def test_the_server_stops_when_the_run_is_killed_outright(tmp_path: Path) -> None:
    status, _ = run_until(tmp_path, "hang", "kill")
    assert status == -signal.SIGKILL
