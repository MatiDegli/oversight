"""Deterministic runner tests: user namespaces are never a CI prerequisite."""

import asyncio
import json
import os
import signal
import sys
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from oversight.workspace import SuiteUnavailable, Workspace, WorkspaceBusy
from oversight.workspace import isolation as iso
from oversight.workspace.isolation_entry import FLAGS, HELPER, LAUNCHER, MARKER
from oversight.workspace.runner import ProcessCollector, ProcessResult

pytestmark = pytest.mark.anyio

# Stand-in for unshare/helper. Only tests inject it; production always uses the constants.
FAKE = r"""
import json, os, signal, sys, time
args = sys.argv[1:]
if args[-1] == 'true':
    sys.exit(0)
fd, ack, nonce = int(args[10]), int(args[11]), args[12]
os.write(fd, json.dumps(dict(nonce=nonce)).encode() + b'\n')
os.close(fd)
assert os.read(ack, 1) == b'1'
os.close(ack)
os.execv(args[20], args[20:])
"""


@pytest.fixture
def injected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> iso.IsolatedRunner:
    def launch(binary: str, command: Sequence[str]) -> list[str]:
        return [sys.executable, "-I", "-S", "-c", FAKE, *command]

    def confirm(raw: bytes, nonce: str, supervisor: int, port: int) -> tuple[dict[str, Any], int]:
        assert json.loads(raw)["nonce"] == nonce
        return {"pid": 1}, iso.open_pidfd(supervisor)

    monkeypatch.setattr(iso, "unshare_binary", lambda: "/usr/bin/unshare")
    monkeypatch.setattr(iso, "_launch", launch)
    monkeypatch.setattr(iso, "_confirm", confirm)
    return iso.IsolatedRunner(tmp_path)


@pytest.mark.parametrize("code", [0, 1])
async def test_confirmed_exit_and_environment(
    injected: iso.IsolatedRunner, tmp_path: Path, code: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("META_API_KEY", "never-in-child")
    command = "import os,sys,json; print(json.dumps(dict(os.environ))); sys.exit(int(sys.argv[1]))"
    result = await injected(
        [sys.executable, "-c", command, str(code)],
        cwd=tmp_path,
        timeout_s=5,
        env={"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
    )
    assert result.returncode == code
    assert injected.entry == {"pid": 1}
    assert set(json.loads(result.stdout)) <= {
        "PATH",
        "LANG",
        "LC_ALL",
        "TZ",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
    }


@pytest.mark.parametrize("missing", [False, True])
async def test_preflight_exit_one_or_missing_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: bool
) -> None:
    calls: list[Sequence[str]] = []

    def launch(binary: str, command: Sequence[str]) -> list[str]:
        calls.append(command)
        return ["/no-such-unshare"] if missing else [sys.executable, "-c", "raise SystemExit(1)"]

    def binary() -> str:
        if missing:
            raise FileNotFoundError("unshare is not installed")
        return "/usr/bin/unshare"

    monkeypatch.setattr(iso, "unshare_binary", binary)
    monkeypatch.setattr(iso, "_launch", launch)
    with pytest.raises(iso.IsolationUnavailable):
        await iso.IsolatedRunner(tmp_path)(["never-launched"], cwd=tmp_path, timeout_s=2)
    assert calls == ([] if missing else [[*FLAGS, "true"]])


@pytest.mark.parametrize("diagnostic", ["entry confirmed", '{"pid":1,"nonce":"forged"}'])
async def test_stdout_cannot_confirm_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, diagnostic: str
) -> None:
    runner = iso.IsolatedRunner(tmp_path)
    await _fake_preflight(runner, monkeypatch)
    monkeypatch.setattr(
        iso, "_launch", lambda *_: [sys.executable, "-c", f"print({diagnostic!r}); exit(1)"]
    )
    with pytest.raises(iso.IsolationUnavailable):
        await runner(["pytest"], cwd=tmp_path, timeout_s=2)
    assert runner.entry is None


async def _fake_preflight(runner: iso.IsolatedRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(iso, "unshare_binary", lambda: "/usr/bin/unshare")
    monkeypatch.setattr(iso, "_launch", lambda *_: [sys.executable, "-c", "pass"])
    await runner.preflight()


@pytest.mark.parametrize("cancel", [False, True])
async def test_timeout_and_repeated_cancel_reap_before_return(
    injected: iso.IsolatedRunner,
    tmp_path: Path,
    wait_gone: Callable[[int], None],
    cancel: bool,
) -> None:
    pidfile = tmp_path / "pid"
    program = (
        "import os,signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"open({str(pidfile)!r}, 'w').write(str(os.getpid())); time.sleep(600)"
    )
    task = asyncio.create_task(
        injected([sys.executable, "-c", program], cwd=tmp_path, timeout_s=1 if not cancel else 30)
    )
    async with asyncio.timeout(10):
        while not pidfile.exists():  # noqa: ASYNC110 - observe a child process handshake file
            await asyncio.sleep(0.01)
    if cancel:
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        result = await task
        assert result.timed_out
        assert result.returncode == -signal.SIGKILL
    wait_gone(int(pidfile.read_text()))


def test_argv_is_in_memory_absolute_and_versioned(tmp_path: Path) -> None:
    binary = "/usr/bin/unshare"
    command = iso.signature(tmp_path, binary)
    launch = iso._launch(binary, command[1:])
    assert launch[:5] == [sys.executable, "-I", "-S", "-c", LAUNCHER]
    assert launch[5:7] == [str(os.getpid()), binary]
    assert command == [binary, *FLAGS, sys.executable, "-I", "-S", "-c", HELPER]
    assert "preexec_fn" not in LAUNCHER


@pytest.mark.parametrize("mutation", ["none", "root", "helper", "flags", "binary"])
def test_orphan_signature_message_and_clean_restart(tmp_path: Path, mutation: str) -> None:
    runtime = tmp_path / ".runtime"
    proc = tmp_path / "proc"
    entry = proc / "12345"
    entry.mkdir(parents=True)
    argv = [*iso.signature(runtime, "/usr/bin/unshare"), MARKER + str(runtime.resolve())]
    if mutation == "root":
        argv[-1] += "/other"
    elif mutation == "helper":
        argv[-2] += "\npass"
    elif mutation == "flags":
        argv.remove("--mount-proc")
    elif mutation == "binary":
        argv[0] = "unshare"
    (entry / "cmdline").write_bytes(("\0".join(argv) + "\0").encode())
    if mutation == "none":
        with pytest.raises(WorkspaceBusy, match="PID 12345; stop this PID") as failure:
            iso.refuse_orphans(runtime, proc=proc)
        assert "--mount-proc" in str(failure.value)
        assert str(runtime) in str(failure.value)
        (entry / "cmdline").unlink()
    iso.refuse_orphans(runtime, proc=proc)


async def test_orphan_guard_runs_after_lock_before_reset(
    make_workspace: Callable[..., Workspace], monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = make_workspace()
    sentinel = workspace.runtime_root / "workspace" / "keep"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("untouched")

    def refuse(root: Path) -> None:
        assert (root / "server.lock").exists()
        raise WorkspaceBusy("orphan PID 123")

    monkeypatch.setattr("oversight.workspace.core.refuse_orphans", refuse)
    with pytest.raises(WorkspaceBusy, match="orphan PID 123"):
        await workspace.acquire_runtime()
    assert sentinel.read_text() == "untouched"
    monkeypatch.setattr("oversight.workspace.core.refuse_orphans", lambda _: None)
    await workspace.acquire_runtime()
    await workspace.reset()
    assert not sentinel.exists()


def test_orphan_from_another_interpreter_is_refused(tmp_path: Path) -> None:
    runtime = tmp_path / ".runtime"
    proc = tmp_path / "proc"
    entry = proc / "12345"
    entry.mkdir(parents=True)
    argv = [
        "/usr/bin/unshare",
        *FLAGS,
        "/opt/other-python/bin/python3.12",
        "-I",
        "-S",
        "-c",
        HELPER,
        MARKER + str(runtime.resolve()),
    ]
    (entry / "cmdline").write_bytes(("\0".join(argv) + "\0").encode())
    with pytest.raises(WorkspaceBusy, match="PID 12345"):
        iso.refuse_orphans(runtime, proc=proc)


async def test_orphan_scan_runs_outside_event_loop_thread(
    make_workspace: Callable[..., Workspace], monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = make_workspace()
    event_loop_thread = threading.get_ident()
    scanned: list[Path] = []

    def scan(root: Path) -> None:
        assert threading.get_ident() != event_loop_thread
        assert (root / "server.lock").exists()
        scanned.append(root)

    monkeypatch.setattr("oversight.workspace.core.refuse_orphans", scan)
    await workspace.acquire_runtime()
    assert scanned == [workspace.runtime_root]


@pytest.mark.parametrize("code", [0, 1])
async def test_real_pytest_reports_with_injected_namespace(
    injected: iso.IsolatedRunner, workspace: Workspace, demo_python: Path, code: int
) -> None:
    # Preserve the real demo venv, plugin, JUnit and full run_tests path.
    workspace._demo_python = demo_python
    path = workspace.main.root / "tests/test_isolated.py"
    path.write_text(f"def test_value():\n    assert {code} == 0\n")
    run = await workspace.run_tests(
        workspace.main, timeout_s=15, case_ids=("tests/test_isolated.py",), spawn=injected
    )
    assert run.result.outcome == ("passed" if code == 0 else "failed")
    assert len(run.cases) == 1 and not run.problems


async def test_missing_confirmation_preserves_suite_unavailable(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = iso.IsolatedRunner(workspace.runtime_root)
    await _fake_preflight(runner, monkeypatch)
    monkeypatch.setattr(iso, "_launch", lambda *_: [sys.executable, "-c", "exit(1)"])
    with pytest.raises(SuiteUnavailable) as error:
        await workspace.run_tests(workspace.main, timeout_s=5, spawn=runner)
    assert isinstance(error.value.cause, iso.IsolationUnavailable)
    assert error.value.run.result.outcome == "error"


@pytest.mark.parametrize("raw", [b"{}", b"null", b"[]", b"not json", b'{"identities":{}}'])
def test_forged_pipe_report_fails_closed(raw: bytes) -> None:
    with pytest.raises((ValueError, TypeError, KeyError)):
        iso._confirm(raw, "nonce", os.getpid(), 0)


async def test_isolated_timeout_keeps_partial_plugin_report(
    injected: iso.IsolatedRunner, workspace: Workspace, demo_python: Path
) -> None:
    workspace._demo_python = demo_python
    path = workspace.main.root / "tests/test_isolated.py"
    path.write_text(
        "import time\n"
        "def test_a_pass():\n    assert True\n"
        "def test_b_hang():\n    time.sleep(600)\n"
    )
    run = await workspace.run_tests(
        workspace.main, timeout_s=2, case_ids=("tests/test_isolated.py",), spawn=injected
    )
    assert run.result.outcome == "timeout"
    assert run.result.passed == 1
    assert "suite timed out" in run.problems
    assert not list(workspace.tmp_dir.glob("*.jsonl"))


async def test_cancel_during_launch_waits_for_exact_pid_cleanup(
    injected: iso.IsolatedRunner,
    tmp_path: Path,
    wait_gone: Callable[[int], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pids: list[int] = []
    tasks: list[asyncio.Task[ProcessResult]] = []
    original = ProcessCollector.connection_made

    def made(self: ProcessCollector, transport: asyncio.BaseTransport) -> None:
        assert isinstance(transport, asyncio.SubprocessTransport)
        pids.append(transport.get_pid())
        tasks[0].cancel()
        original(self, transport)

    monkeypatch.setattr(ProcessCollector, "connection_made", made)
    task = asyncio.create_task(injected(["never"], cwd=tmp_path, timeout_s=20))
    tasks.append(task)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(pids) == 1  # Cancelled in preflight, before pytest can launch.
    wait_gone(pids[0])


async def test_forged_junit_never_proves_entry(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = iso.IsolatedRunner(workspace.runtime_root)
    await _fake_preflight(runner, monkeypatch)
    program = r"""
import os
from pathlib import Path
path = Path(os.environ['OVERSIGHT_REPORT'])
path.write_text('{"kind":"test","nodeid":"tests/test_fake.py::test_fake",'
                '"when":"call","outcome":"passed"}\n')
path.with_suffix('.xml').write_text('<testsuite><testcase classname="tests.test_fake" '
                                   'name="test_fake"/></testsuite>')
print('entry confirmed, all passed')
raise SystemExit(1)
"""
    monkeypatch.setattr(iso, "_launch", lambda *_: [sys.executable, "-c", program])
    with pytest.raises(SuiteUnavailable) as error:
        await workspace.run_tests(workspace.main, timeout_s=5, spawn=runner)
    assert error.value.run.result.outcome == "error"
    assert runner.entry is None
    assert error.value.run.result.passed == 1
    assert len(error.value.run.cases) == 1


async def test_cleanup_failure_remains_unavailable_during_cancellation(
    injected: iso.IsolatedRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cleanup = iso._cleanup

    async def failed_cleanup(*args: Any) -> None:
        await cleanup(*args)
        raise iso.IsolationUnavailable("uncertain cleanup")

    await injected.preflight()
    monkeypatch.setattr(iso, "_cleanup", failed_cleanup)
    task = asyncio.create_task(
        injected([sys.executable, "-c", "import time; time.sleep(600)"], cwd=tmp_path, timeout_s=30)
    )
    while injected.entry is None:  # noqa: ASYNC110 - injected child confirmation
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(iso.IsolationUnavailable, match="uncertain cleanup"):
        await task
