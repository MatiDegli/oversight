"""frontend/e2e/run.sh of `make e2e`: the preview server it starts never outlives it.

On 2026-10-27 a `vite preview` from an earlier e2e run stayed alive for minutes after the run
ended: Playwright's own webServer starts it in a process group of its own, so a run that
`timeout` ended left it behind. The script now owns the server, in its own process group, and
stops it by PID, only when the PID still has the start time recorded at launch (a reused PID is
someone else's), and the kernel kills it with the script even when only the script is SIGKILLed
(`setpriv --pdeathsig`). Each test runs the real script over a fake frontend folder (a vite that
builds nothing and a preview that only listens, a playwright that fails or hangs) and checks
that the server's process is gone after the end the test chose. It needs a node, as the script does.
"""

import contextlib
import os
import shutil
import signal
import socket
import subprocess
import textwrap
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
E2E = ROOT / "frontend" / "e2e"
RUN_SH = E2E / "run.sh"
PROCS_SH = E2E / "procs.sh"
BASH = shutil.which("bash") or "/usr/bin/bash"
DEADLINE_S = 30
# A kernel that ties the server to its script ends it at once; this is all the tests give it.
TIED_S = 10
# The only tools run.sh and the fakes need, so that a test decides whether setpriv is among them.
TOOLS = ("node", "seq", "sleep", "dirname")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="run.sh needs node")

# What the fake preview does: listen on the port it is given and say which process it is.
PREVIEW_JS = textwrap.dedent(
    """\
    const http = require("node:http");
    const fs = require("node:fs");
    const port = Number(process.argv[process.argv.indexOf("--port") + 1]);
    http.createServer().listen(port, "127.0.0.1", () => {
      fs.writeFileSync(process.env.FAKE_DIR + "/server.port", String(port));
      fs.writeFileSync(process.env.FAKE_DIR + "/server.pid", String(process.pid));
    });
    """
)
# `build` builds nothing. The fake playwright says it started, then fails or waits to be ended.
FAKE_VITE = "#!/bin/sh\nexit 0\n"
FAKE_PLAYWRIGHT = textwrap.dedent(
    """\
    #!/bin/bash
    while [ ! -s "$FAKE_DIR/server.pid" ]; do sleep 0.02; done
    echo $$ > "$FAKE_DIR/playwright.pid"
    if [ "$FAKE_MODE" = fail ]; then exit 3; fi
    if [ "$FAKE_MODE" = reuse ]; then
      # The server dies on its own; once it is reaped, a stranger takes over its PID.
      server=$(<"$FAKE_DIR/server.pid")
      kill -KILL "$server"
      while kill -0 "$server" 2>/dev/null; do sleep 0.02; done
      echo $((server - 1)) > /proc/sys/kernel/ns_last_pid
      sleep 300 &
      echo $! > "$FAKE_DIR/stranger.pid"
      [ "$!" = "$server" ] || exit 4
      exit 0
    fi
    while true; do sleep 0.05; done
    """
)
# A setpriv that does not tie the child to its parent: only run.sh's own trap can stop the server.
FAKE_SETPRIV = '#!/bin/sh\nshift 2\nexec "$@"\n'


def alive(pid: int) -> bool:
    """True for a running process; a zombie that only waits to be reaped is not."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return state != "Z"


def until(condition: object, what: str, deadline_s: float = DEADLINE_S) -> None:
    """Wait for `condition()` for at most `deadline_s` seconds; the failure names it."""
    stop = time.monotonic() + deadline_s
    while not condition():  # type: ignore[operator]
        if time.monotonic() > stop:
            pytest.fail(f"gave up waiting for {what}")
        time.sleep(0.02)


def listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def fake_frontend(tmp_path: Path) -> Path:
    frontend = tmp_path / "frontend"
    (frontend / "e2e").mkdir(parents=True)
    shutil.copy(RUN_SH, frontend / "e2e" / "run.sh")
    shutil.copy(PROCS_SH, frontend / "e2e" / "procs.sh")
    (frontend / "node_modules" / "vite" / "bin").mkdir(parents=True)
    (frontend / "node_modules" / "vite" / "bin" / "vite.js").write_text(PREVIEW_JS)
    (frontend / "node_modules" / ".bin").mkdir()
    for name, text in (("vite", FAKE_VITE), ("playwright", FAKE_PLAYWRIGHT)):
        script = frontend / "node_modules" / ".bin" / name
        script.write_text(text)
        script.chmod(0o755)
    return frontend


def tool_path(tmp_path: Path, setpriv: str) -> str:
    """A PATH holding only TOOLS and the setpriv the test picks: real, plain (no tie) or none."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in TOOLS:
        (bin_dir / name).symlink_to(shutil.which(name) or name)
    if setpriv == "real":
        (bin_dir / "setpriv").symlink_to(shutil.which("setpriv") or "setpriv")
    elif setpriv == "plain":
        (bin_dir / "setpriv").write_text(FAKE_SETPRIV)
        (bin_dir / "setpriv").chmod(0o755)
    return str(bin_dir)


def run_until(tmp_path: Path, mode: str, how: str, setpriv: str = "plain") -> tuple[int, int, str]:
    """Runs run.sh, ends it as `how` says, and returns its status, the server's pid and its stderr.

    With `setpriv` "plain" the kernel does not tie the server to the script, so what stops the
    server is run.sh's own work: its trap or the group kill. "real" is the machine's setpriv.
    """
    frontend = fake_frontend(tmp_path)
    env = {
        **os.environ,
        "PATH": tool_path(tmp_path, setpriv),
        "FAKE_DIR": str(tmp_path),
        "FAKE_MODE": mode,
    }
    stderr_path = tmp_path / "stderr.txt"
    # Its own session, as under `timeout`, which makes the command a process group of its own.
    with stderr_path.open("w") as stderr:
        run = subprocess.Popen(
            [BASH, str(frontend / "e2e" / "run.sh")],
            env=env,
            stderr=stderr,
            start_new_session=True,
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
        elif how == "kill-script":  # the script alone: no trap runs, and nothing signals the group
            os.kill(run.pid, signal.SIGKILL)
        status = run.wait(timeout=DEADLINE_S)
        until(
            lambda: not alive(server),
            "the preview server to be gone",
            TIED_S if how == "kill-script" else DEADLINE_S,
        )
        return status, server, stderr_path.read_text()
    finally:
        with_server = tmp_path / "server.pid"
        if with_server.exists() and alive(int(with_server.read_text())):
            os.kill(int(with_server.read_text()), signal.SIGKILL)
        # Whatever of the group is left, such as the fake playwright of a killed script.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(run.pid, signal.SIGKILL)
        run.wait(timeout=DEADLINE_S)


@pytest.mark.parametrize("setpriv", ["real", "plain"])
def test_the_server_stops_when_the_tests_fail(tmp_path: Path, setpriv: str) -> None:
    status, _, _ = run_until(tmp_path, "fail", "none", setpriv)
    assert status == 3, "the tests' own exit status comes through"


@pytest.mark.parametrize("setpriv", ["real", "plain"])
def test_the_server_stops_when_the_time_bound_ends_the_run(tmp_path: Path, setpriv: str) -> None:
    status, _, _ = run_until(tmp_path, "hang", "term", setpriv)
    assert status == 143


def test_the_server_stops_when_the_run_is_killed_outright(tmp_path: Path) -> None:
    status, _, _ = run_until(tmp_path, "hang", "kill", "real")
    assert status == -signal.SIGKILL


@pytest.mark.skipif(shutil.which("setpriv") is None, reason="the tie to the script needs setpriv")
def test_the_server_dies_with_the_script_when_only_the_script_is_killed(tmp_path: Path) -> None:
    status, server, _ = run_until(tmp_path, "hang", "kill-script", "real")
    assert status == -signal.SIGKILL
    assert not alive(server)
    port = int((tmp_path / "server.port").read_text())
    until(lambda: not listening(port), "the server's port to be closed", TIED_S)


def test_a_machine_without_setpriv_is_told_so_and_still_runs(tmp_path: Path) -> None:
    status, _, stderr = run_until(tmp_path, "fail", "none", "none")
    assert status == 3
    assert "no setpriv" in stderr


# The guard against a reused PID, on its own: procs.sh is what run.sh sources.
def start_time(pid: int) -> str:
    """Field 22 of /proc/PID/stat, counted from the last ')' as the format requires."""
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


def bash_function(call: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [BASH, "-c", f'. "$1"; {call}', "bash", str(PROCS_SH)],
        capture_output=True,
        text=True,
        timeout=DEADLINE_S,
        check=False,
    )


@pytest.fixture
def stranger(tmp_path: Path) -> Iterator[subprocess.Popen[bytes]]:
    """A process whose command name holds spaces and parentheses, as a stat line may."""
    sleeper = tmp_path / "a) 9 (b"
    shutil.copy(shutil.which("sleep") or "sleep", sleeper)
    process = subprocess.Popen([str(sleeper), "300"])
    try:
        yield process
    finally:
        process.kill()
        process.wait(timeout=DEADLINE_S)


def test_proc_start_reads_the_start_time_whatever_the_command_is_called(
    stranger: subprocess.Popen[bytes],
) -> None:
    done = bash_function(f"proc_start {stranger.pid}")
    assert done.returncode == 0
    assert done.stdout.strip() == start_time(stranger.pid)
    assert bash_function("proc_start 99999999").returncode != 0


def test_kill_if_started_signals_only_the_process_with_the_recorded_start(
    stranger: subprocess.Popen[bytes],
) -> None:
    # A PID that now belongs to another process has another start time: nothing is signaled.
    assert bash_function(f"kill_if_started {stranger.pid} 1").returncode != 0
    assert bash_function(f"kill_if_started {stranger.pid} ''").returncode != 0
    assert stranger.poll() is None, "a process with another start time survives"
    assert (
        bash_function(f"kill_if_started {stranger.pid} {start_time(stranger.pid)}").returncode == 0
    )
    assert stranger.wait(timeout=DEADLINE_S) == -signal.SIGTERM


def unshare_works() -> bool:
    unshare = shutil.which("unshare")
    if unshare is None:
        return False
    try:
        probe = subprocess.run(
            [unshare, "-Urpf", "--mount-proc", "true"], capture_output=True, timeout=10, check=False
        )
    except subprocess.TimeoutExpired:
        return False
    return probe.returncode == 0


@pytest.mark.skipif(not unshare_works(), reason="forcing a PID to be reused needs a user namespace")
def test_a_pid_reused_after_the_server_exits_is_not_killed(tmp_path: Path) -> None:
    """In a PID namespace the server dies and a stranger takes its PID: the trap spares it."""
    frontend = fake_frontend(tmp_path)
    env = {**os.environ, "PATH": tool_path(tmp_path, "plain"), "FAKE_DIR": str(tmp_path)}
    env["FAKE_MODE"] = "reuse"
    # The wrapper is PID 1 of the namespace, which ends with it: it looks at the stranger after the
    # trap has run, before the namespace goes.
    wrapper = (
        '"$1" "$2"; status=$?; '
        'kill -0 "$(<"$3/stranger.pid")" 2>/dev/null && : >"$3/stranger.survived"; '
        "exit $status"
    )
    command = [
        shutil.which("unshare") or "unshare", "-Urpf", "--kill-child", "--mount-proc",
        BASH, "-c", wrapper, "wrapper", BASH, str(frontend / "e2e" / "run.sh"), str(tmp_path),
    ]  # fmt: skip
    run = subprocess.Popen(command, env=env, start_new_session=True)
    try:
        status = run.wait(timeout=DEADLINE_S)
        assert (tmp_path / "stranger.pid").exists(), "the fake playwright reached the reuse"
        assert status == 0, "the stranger took the server's PID"
        assert (tmp_path / "stranger.survived").exists(), "the trap killed a process not its own"
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(run.pid, signal.SIGKILL)
        run.wait(timeout=DEADLINE_S)
