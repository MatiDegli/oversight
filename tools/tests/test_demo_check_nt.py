"""Injected NT policy runs on Linux; kernel/console proofs are explicitly native only."""

import asyncio
import ctypes
import errno
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import demo_check
import demo_check_nt as nt
import smoke

NATIVE = pytest.mark.skipif(
    sys.platform != "win32", reason="requires native Windows Job Objects and console delivery"
)


class Child:
    pid = 4321
    returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def kill(self) -> None:
        self.returncode = 1

    def wait(self, timeout: float) -> int:
        assert timeout > 0 and self.returncode is not None
        return self.returncode


class FakeJobs:
    def __init__(self, fail: str = "") -> None:
        self.fail = fail
        self.calls: list[str] = []
        self.members: tuple[int, ...] = ()
        self.api = nt.Jobs(
            self.create, self.assign, self.resume, self.pids, self.terminate, self.close
        )

    def create(self) -> int:
        self.calls.append("create")
        if self.fail == "create":
            raise OSError("create")
        return 77

    def assign(self, job: int, pid: int) -> None:
        self.calls.append("assign")
        if self.fail == "assign":
            raise OSError("assign")
        self.members = (pid,)

    def resume(self, pid: int) -> None:
        self.calls.append("resume")
        if self.fail == "resume":
            raise OSError("resume")

    def pids(self, job: int) -> tuple[int, ...]:
        self.calls.append("pids")
        return self.members

    def terminate(self, job: int) -> None:
        self.calls.append("terminate")
        self.members = ()

    def close(self, job: int) -> None:
        assert not self.members
        self.calls.append("close")


@pytest.mark.anyio
@pytest.mark.parametrize("fail", ["create", "launch", "assign", "resume", ""])
async def test_suspended_failure_matrix(tmp_path: Path, fail: str) -> None:
    jobs, child = FakeJobs(fail), Child()

    def spawn(*args: Any, **kwargs: Any) -> Any:
        jobs.calls.append("launch")
        assert kwargs["creationflags"] == 0x204
        assert kwargs["creationflags"] & 0x08000000 == 0
        assert "start_new_session" not in kwargs and "preexec_fn" not in kwargs
        if fail == "launch":
            raise OSError("launch")
        return child

    async def launch() -> nt.Server:
        return await nt.start_server([], {}, tmp_path / "server.log", jobs=jobs.api, spawn=spawn)

    if fail:
        with pytest.raises(OSError, match=fail):
            await launch()
        if "launch" in jobs.calls and fail != "launch":
            assert child.returncode is not None
        assert (jobs.calls[-1] == "close") == (fail != "create")
    else:
        server = await launch()
        assert jobs.calls == ["create", "launch", "assign", "resume"]
        await server.dispose()
        assert jobs.calls[-2:] == ["pids", "close"]


def test_outer_assignment_is_before_execution_and_handle_is_retained(
    monkeypatch: pytest.MonkeyPatch,
):
    jobs = FakeJobs()
    monkeypatch.setattr(nt, "_outer", None)
    assert nt.retain_outer(jobs.api) == nt.retain_outer(jobs.api) == 77
    assert jobs.calls == ["create", "assign"] and jobs.members == (os.getpid(),)
    monkeypatch.setattr(nt, "_outer", None)
    refused = FakeJobs("assign")
    with pytest.raises(OSError, match="assign"):
        nt.retain_outer(refused.api)
    assert refused.calls == ["create", "assign", "close"]


@pytest.mark.anyio
async def test_empty_job_poll_yields_and_times_out_without_closing(monkeypatch) -> None:
    jobs = FakeJobs()
    jobs.members = (10, 20, 30)  # launcher, Git, and an unrelated-session descendant
    ticks = 0
    # Advance the deadline only after all ticks: native timer resolution is not 5 ms.
    monkeypatch.setattr(nt, "time", SimpleNamespace(monotonic=lambda: 0.04 if ticks == 4 else 0))

    async def ticking() -> None:
        nonlocal ticks
        for _ in range(4):
            await asyncio.sleep(0)
            ticks += 1

    with pytest.raises(smoke.SmokeError, match="still contains"):
        await asyncio.gather(nt.wait_members(77, jobs.api, deadline_s=0.04), ticking())
    assert ticks == 4 and "close" not in jobs.calls
    jobs.members = ()
    await nt.wait_members(77, jobs.api)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "code,log,deadline_s",
    [
        (0, True, 1),
        (3, False, 1),
        (None, True, 0),
        (3, True, 1),
        (3, True, 0),
        (0xC000013A, True, 1),
    ],
)
async def test_shutdown_verdict(
    tmp_path: Path, monkeypatch, code: int | None, log: bool, deadline_s: float
):
    # Two reads can share a native clock tick; an exhausted budget must still refuse.
    monkeypatch.setattr(nt, "time", SimpleNamespace(monotonic=lambda: 100))
    jobs, child = FakeJobs(), Child()
    path = tmp_path / "server.log"
    path.write_text("Application shutdown complete" if log else "startup only")
    server = nt.Server(77, path, jobs.api, cast(Any, child))
    sent: list[tuple[int, int]] = []

    def send(pid: int, event: int) -> None:
        sent.append((pid, event))
        child.returncode = code

    if code == 3 and log and deadline_s > 0:
        await server.stop(deadline_s, send)
        assert "terminate" not in jobs.calls
    else:
        with pytest.raises(smoke.SmokeError, match=r"verdict|deadline"):
            await server.stop(deadline_s, send)
        assert "terminate" in jobs.calls and child.returncode is not None
    assert sent == [(child.pid, nt.CTRL_BREAK)] and jobs.calls[-1] == "close"


@pytest.mark.anyio
async def test_cancellation_keeps_handle_until_cleanup_finishes(tmp_path: Path):
    jobs, child = FakeJobs(), Child()
    server = nt.Server(77, tmp_path / "log", jobs.api, cast(Any, child))
    task = asyncio.create_task(server.stop(send=lambda *a: None))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert child.returncode is not None and jobs.calls[-1] == "close"
    finished = asyncio.Event()

    async def slow_cleanup():
        await asyncio.sleep(0.03)
        finished.set()

    task = asyncio.create_task(nt.settled(slow_cleanup()))
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0.01)
    task.cancel()
    assert not finished.is_set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


@pytest.mark.anyio
@pytest.mark.parametrize("error", [errno.EACCES, errno.EIO, None])
async def test_lock_poll_seek_unlock_and_close(tmp_path: Path, error: int | None):
    lock = tmp_path / "server.lock"
    lock.write_bytes(b"x")
    calls: list[int] = []
    descriptors: list[int] = []

    class Lock:
        LK_NBLCK, LK_UNLCK = 1, 2

        def locking(self, fd: int, mode: int, size: int, /) -> None:
            descriptors.append(fd)
            calls.append(mode)
            assert os.lseek(fd, 0, os.SEEK_CUR) == 0 and size == 1
            os.lseek(fd, 1, os.SEEK_SET)
            if mode == 1 and error is not None:
                raise OSError(error, "injected")

    if error:
        with pytest.raises(smoke.SmokeError if error == errno.EACCES else OSError):
            await nt.probe_lock(tmp_path, deadline_s=0.02, api=Lock())
        assert 2 not in calls
    else:
        await nt.probe_lock(tmp_path, api=Lock())
        assert calls == [1, 2]
    with pytest.raises(OSError):
        os.fstat(descriptors[-1])


def test_import_preserves_linux_reexports_and_verdict():
    assert demo_check.GRACEFUL_EXITS == (0, -15)
    assert demo_check.check_level2.__module__ == "demo_check_phases"
    assert demo_check.start_agent.__module__ == "demo_check_phases"


@pytest.mark.anyio
@pytest.mark.parametrize("fail", [False, True])
async def test_three_runs_share_runtime_and_use_the_full_driver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail: bool
):
    logs = tmp_path / "logs"
    logs.mkdir()
    outer_checks: list[int] = []
    runtimes: list[Path] = []
    phases: list[str] = []
    first = object()

    async def start(argv, env, log):
        assert argv[-1] == "serve" and env["META_API_KEY"] == ""
        assert env["BASETEN_API_KEY"] == "" and env["OVERSIGHT_CROSS_REVIEW"] == "false"
        assert env["OVERSIGHT_WORKSPACE_SOURCE"] == ""
        assert env["OVERSIGHT_AGENT_MODEL_B"] == env["OVERSIGHT_AGENT_MODEL"] == "fake"
        runtimes.append(Path(env["OVERSIGHT_RUNTIME_DIR"]))
        jobs = FakeJobs()
        log.write_text("Application shutdown complete")
        return nt.Server(77, log, jobs.api, cast(Any, Child()))

    async def stop(self, *args):
        phases.append("stop")

    def decide(base, previous, clock, number, runtime):
        assert previous is (None if number == 1 else first)
        phases.append("full level2")
        if fail:
            raise smoke.SmokeError("phase failed")
        return first

    async def lock(runtime):
        assert phases[-1] == "stop"
        phases.append("lock")

    async def members(job, jobs, expected=()):
        assert expected == (os.getpid(),)
        outer_checks.append(job)

    monkeypatch.setenv("META_API_KEY", "environment-value")
    monkeypatch.setenv("BASETEN_API_KEY", "environment-value")
    monkeypatch.setenv("OVERSIGHT_WORKSPACE_SOURCE", "unwanted-source")
    monkeypatch.setenv("OVERSIGHT_CROSS_REVIEW", "true")
    monkeypatch.setattr(nt, "retain_outer", lambda: 99)
    monkeypatch.setattr(nt, "evidence_dir", lambda: logs)
    monkeypatch.setattr(nt, "start_server", start)
    monkeypatch.setattr(nt.Server, "stop", stop)
    monkeypatch.setattr(nt, "wait_members", members)
    monkeypatch.setattr(nt, "probe_lock", lock)
    monkeypatch.setattr(demo_check, "serve_and_decide", decide)
    monkeypatch.setattr(smoke, "wait_for_health", lambda *a: None)
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    cleaned: list[Path] = []
    monkeypatch.setattr(nt, "cleanup", cleaned.append)
    if fail:
        with pytest.raises(smoke.SmokeError, match="phase failed"):
            await nt.run_checks()
        assert phases == ["full level2", "stop"] and not cleaned
    else:
        await nt.run_checks()
        assert phases == ["full level2", "stop", "lock"] * 3
        assert len(set(runtimes)) == 1 and outer_checks == [99] * 4
        assert len(cleaned) == 1 and cleaned[0].resolve() == runtimes[0].parent
    # The injected cleanup deliberately kept scratch for inspection; remove it now.
    nt.shutil.rmtree(runtimes[0].parent)


def test_cleanup_exhaustion_is_visible_and_readonly_is_removed(tmp_path, monkeypatch):
    directory = tmp_path / "readonly"
    directory.mkdir()
    file = directory / "object"
    file.write_text("git object")
    file.chmod(0o400)
    nt.cleanup(directory)
    assert not directory.exists()
    calls = []

    class Locked(PermissionError):
        winerror = 32

    def locked(*a, **kw):
        calls.append(1)
        raise Locked("locked directory")

    monkeypatch.setattr(nt.shutil, "rmtree", locked)
    monkeypatch.setattr(nt.time, "sleep", lambda delay: None)
    with pytest.raises(PermissionError, match="locked directory"):
        nt.cleanup(tmp_path)
    assert len(calls) == 7


@pytest.mark.anyio
async def test_failed_termination_still_reaps_leader_and_retains_handle(tmp_path):
    jobs, child = FakeJobs(), Child()

    def broken(job):
        raise OSError("termination failed")

    api = nt.Jobs(terminate=broken, close=jobs.close)
    server = nt.Server(77, tmp_path / "log", api, cast(Any, child))
    with pytest.raises(OSError, match="termination failed"):
        await server.dispose()
    assert child.returncode is not None and "close" not in jobs.calls


@pytest.mark.anyio
async def test_cold_build_observation_precedes_backend_death_and_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    jobs, child = FakeJobs(), Child()
    jobs.members = (child.pid, 9876)
    server = nt.Server(77, tmp_path / "log", jobs.api, cast(Any, child))
    server.log.write_text(nt.BACKEND_MARKER + str(child.pid))
    observed: list[str] = []

    async def start(*a, **kw):
        return server

    def image(pid):
        observed.append("image")
        return "/uv.exe" if pid == 9876 else "/python.exe"

    def kill():
        assert "image" in observed
        child.returncode = 1
        jobs.members = ()  # simulate the server's own lifetime Job, not the driver's fallback

    async def lock(*a):
        assert jobs.calls[-1] == "close"
        observed.append("lock")

    async def members(job, api, expected=()):
        assert not jobs.members

    monkeypatch.setattr(nt, "start_server", start)
    monkeypatch.setattr(smoke, "wait_for_health", lambda *a: None)
    monkeypatch.setattr(demo_check, "start_agent", lambda *a: None)
    monkeypatch.setattr(nt, "process_image", image)
    monkeypatch.setattr(nt, "kill_backend", lambda pid: kill())
    monkeypatch.setattr(nt, "probe_lock", lock)
    monkeypatch.setattr(nt, "retain_outer", lambda: 99)
    monkeypatch.setattr(nt, "wait_members", members)
    await nt.hard_kill_check(tmp_path / ".runtime", tmp_path, nt.OverallClock())
    assert "terminate" not in jobs.calls and observed[-1] == "lock"
    (tmp_path / ".runtime/demo-venv").mkdir(parents=True)
    with pytest.raises(smoke.SmokeError, match="cold disposable"):
        await nt.hard_kill_check(tmp_path / ".runtime", tmp_path, nt.OverallClock())


def native_console() -> None:
    if sys.platform != "win32":
        pytest.skip("requires native Windows Job Objects and console delivery")
    import ctypes

    if not ctypes.WinDLL("kernel32").GetConsoleWindow():
        pytest.skip("console proof requires an attached non-elevated PowerShell console")


@NATIVE
@pytest.mark.anyio
async def test_native_nested_jobs_console_code_and_released_lock(capsys):
    native_console()
    # Server lifetime job nests inside per-server and driver jobs. No fallback may pass.
    await nt.run_checks(runs=1, demo=False)
    output = capsys.readouterr().out
    print(output)
    assert f"code={nt.NT_GRACEFUL_EXITS[0]}" in output
    assert "NT smoke: run 1: ok" in output


class ProcessEntry32(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32),
        ("cntUsage", ctypes.c_uint32),
        ("th32ProcessID", ctypes.c_uint32),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", ctypes.c_uint32),
        ("cntThreads", ctypes.c_uint32),
        ("th32ParentProcessID", ctypes.c_uint32),
        ("pcPriClassBase", ctypes.c_int32),
        ("dwFlags", ctypes.c_uint32),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def member_images(api, last_error) -> dict[int, str]:
    """Use a process snapshot so an unopenable member still has an image name."""
    api.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    api.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    for name in ("Process32FirstW", "Process32NextW"):
        operation = getattr(api, name)
        operation.restype = ctypes.c_int
        operation.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessEntry32)]
    snapshot = api.CreateToolhelp32Snapshot(2, 0)  # TH32CS_SNAPPROCESS
    assert snapshot and snapshot != ctypes.c_void_p(-1).value, f"snapshot error={last_error()}"
    try:
        entry = ProcessEntry32()
        entry.dwSize = ctypes.sizeof(entry)
        images = {}
        present = api.Process32FirstW(snapshot, ctypes.byref(entry))
        while present:
            images[entry.th32ProcessID] = entry.szExeFile
            present = api.Process32NextW(snapshot, ctypes.byref(entry))
        error = last_error()
        assert error == 18, f"process snapshot incomplete: error={error}"  # ERROR_NO_MORE_FILES
        return images
    finally:
        api.CloseHandle(snapshot)


def open_driver_members(api, members, required, handles, last_error) -> None:
    images = member_images(api, last_error)
    opened, evidence = [], []
    for pid in sorted(members):
        handle = api.OpenProcess(0x1000, False, pid)  # No outer job handle is retained.
        error = last_error() if not handle else 0
        opened.append((pid, handle, error))
        evidence.append({"pid": pid, "image": images.get(pid, "gone"), "error": error})
        if handle:
            handles.append(handle)
    assert required <= members, f"missing required members={required - members}; {evidence}"
    for pid, handle, error in opened:
        if not handle and pid not in required and error == 87:  # ERROR_INVALID_PARAMETER
            continue  # An optional member already stopped before OpenProcess.
        assert handle, evidence


@pytest.mark.parametrize(
    "errors,refused",
    [
        ({}, False),
        ({30: 87}, False),
        ({10: 87}, True),
        ({20: 87}, True),
        ({30: 5}, True),
        ({30: 87, 40: 5}, True),
    ],
)
def test_driver_member_open_evidence(errors, refused):
    images = {10: "server.exe", 20: "sleeper.exe", 30: "launcher.exe"}
    records, last = iter(images.items()), [0]

    def enumerate_processes(snapshot, pointer):
        assert snapshot == 77
        entry = ctypes.cast(pointer, ctypes.POINTER(ProcessEntry32)).contents
        assert entry.dwSize == ctypes.sizeof(ProcessEntry32)
        row = next(records, None)
        if row is None:
            last[0] = 18
            return False
        entry.th32ProcessID, entry.szExeFile = row
        return True

    def open_process(access, inherit, pid):
        assert access == 0x1000 and not inherit
        last[0] = errors.get(pid, 0)
        return None if pid in errors else pid + 1000

    api = SimpleNamespace(
        CreateToolhelp32Snapshot=Mock(return_value=77),
        Process32FirstW=Mock(side_effect=enumerate_processes),
        Process32NextW=Mock(side_effect=enumerate_processes),
        CloseHandle=Mock(),
        OpenProcess=Mock(side_effect=open_process),
    )
    handles, members = [], {10, 20, 30, 40}
    if refused:
        with pytest.raises(AssertionError) as failure:
            open_driver_members(api, members, {10, 20}, handles, lambda: last[0])
        for pid in sorted(members):
            assert str(
                {"pid": pid, "image": images.get(pid, "gone"), "error": errors.get(pid, 0)}
            ) in str(failure.value)
    else:
        open_driver_members(api, members, {10, 20}, handles, lambda: last[0])
    assert handles == [pid + 1000 for pid in sorted(members - errors.keys())]
    api.CreateToolhelp32Snapshot.assert_called_once_with(2, 0)
    api.CloseHandle.assert_called_once_with(77)
    assert api.OpenProcess.call_count == len(members)


def test_member_snapshot_error_is_not_reported_as_gone():
    api = SimpleNamespace(
        CreateToolhelp32Snapshot=Mock(return_value=77),
        Process32FirstW=Mock(return_value=False),
        Process32NextW=Mock(),
        CloseHandle=Mock(),
    )
    with pytest.raises(AssertionError, match="snapshot incomplete: error=5"):
        member_images(api, lambda: 5)
    api.CloseHandle.assert_called_once_with(77)


@NATIVE
@pytest.mark.anyio
async def test_native_driver_death_without_an_external_outer_handle(tmp_path):
    if sys.platform != "win32":
        pytest.skip("requires native Windows Job Objects and console delivery")
    import ctypes

    marker = tmp_path / "members.json"
    runtime = tmp_path / ".runtime"
    code = f"""
import asyncio, json, os, subprocess, sys
from pathlib import Path
sys.path.insert(0, {str(smoke.REPO_ROOT / "tools")!r})
import demo_check, demo_check_nt as nt, smoke
async def main():
    outer = nt.retain_outer()
    port = smoke.free_port()
    server = await nt.start_server(
        [sys.executable, '-m', 'oversight', 'serve'],
        demo_check.server_env(port, Path({str(runtime)!r})), Path({str(tmp_path / "server.log")!r}),
    )
    await asyncio.to_thread(smoke.wait_for_health, smoke.base_url(port), 30, server.child)
    sleeper = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(120)'])
    Path({str(marker)!r}).write_text(json.dumps({{
        'members': nt.JOBS.pids(outer), 'sleeper': sleeper.pid, 'server': server.child.pid,
    }}))
    await asyncio.sleep(120)
asyncio.run(main())
"""
    driver = await asyncio.create_subprocess_exec(sys.executable, "-c", code)
    handles = []
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.restype = ctypes.c_void_p
    api.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    api.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    try:
        async with asyncio.timeout(40):
            while not marker.exists():
                assert driver.returncode is None
                await asyncio.sleep(nt.POLL)
        recorded = json.loads(marker.read_text())
        members = set(recorded["members"]) - {driver.pid}
        assert len(members) >= 2  # real backend (including any launcher) and sleeper
        required = {recorded["sleeper"], recorded["server"]}
        assert len(required) == 2
        open_driver_members(api, members, required, handles, ctypes.get_last_error)
        driver.kill()
        await asyncio.wait_for(driver.wait(), nt.STOP_DEADLINE)
        async with asyncio.timeout(nt.STOP_DEADLINE):
            for handle in handles:
                while True:
                    status = ctypes.c_uint32()
                    assert api.GetExitCodeProcess(handle, ctypes.byref(status))
                    if status.value != 259:
                        break
                    await asyncio.sleep(nt.POLL)
        await nt.probe_lock(runtime)
        print(f"NT driver death: descendants={members} exited; lock released; no outer handle")
    finally:
        if driver.returncode is None:
            driver.kill()
        await asyncio.wait_for(driver.wait(), nt.STOP_DEADLINE)
        for handle in handles:
            api.CloseHandle(handle)


@NATIVE
def test_native_readonly_and_locked_directory_cleanup(tmp_path):
    if sys.platform != "win32":
        pytest.skip("requires native Windows Job Objects and console delivery")
    import ctypes

    readonly = tmp_path / "readonly"
    readonly.mkdir()
    object_file = readonly / "object"
    object_file.write_bytes(b"read-only git object")
    object_file.chmod(0o400)
    nt.cleanup(readonly)
    assert not readonly.exists()
    locked = tmp_path / "locked"
    locked.mkdir()
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.restype = ctypes.c_void_p
    api.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = api.CreateFileW(str(locked), 0x80000000, 1, None, 3, 0x02000000, None)
    assert handle and handle != ctypes.c_void_p(-1).value
    try:
        with pytest.raises(OSError):
            nt.cleanup(locked)
        assert locked.exists()
    finally:
        api.CloseHandle(handle)
    nt.cleanup(locked)


@NATIVE
@pytest.mark.anyio
async def test_native_backend_death_during_uv_then_three_clean_starts():
    if os.environ.get("OVERSIGHT_NT_FULL_DEMO") != "1":
        pytest.skip("the full demo is opt-in; set OVERSIGHT_NT_FULL_DEMO=1")
    native_console()
    await nt.run_checks(hard_kill=True)


def test_fake_environment_overrides_dotenv_and_decisions_refuse_credentials(tmp_path, monkeypatch):
    from oversight.agents import DecisionUnavailable
    from oversight.decision_config import configured_decision
    from oversight.settings import Settings

    env_file = tmp_path / ".env"
    env_file.write_text(
        "META_API_KEY=dotenv-value\nBASETEN_API_KEY=dotenv-value\n"
        "OVERSIGHT_CROSS_REVIEW=true\nOVERSIGHT_WORKSPACE_SOURCE=unwanted-source\n"
        "OVERSIGHT_AGENT_MODEL=muse-spark\nOVERSIGHT_AGENT_MODEL_B=muse-spark-open\n"
    )
    for key, value in demo_check.server_env(8000, tmp_path / ".runtime").items():
        monkeypatch.setenv(key, value)
    settings = Settings.load(env_file)
    assert settings.meta_api_key is settings.baseten_api_key is None
    assert settings.cross_review is False and settings.workspace_source == ""
    assert settings.agent_model == settings.agent_model_b == "fake"
    with pytest.raises(DecisionUnavailable):
        configured_decision(settings)  # no invented fake decision profile or provider call


@pytest.mark.parametrize("marker", ["", "invalid", "4321\nNT backend pid: 4321", "9999"])
def test_backend_pid_requires_one_marker_inside_the_retained_job(tmp_path, marker):
    jobs = FakeJobs()
    jobs.members = (4321,)
    log = tmp_path / "log"
    log.write_text(nt.BACKEND_MARKER + marker if marker else "startup only")
    with pytest.raises(smoke.SmokeError, match="PID"):
        nt.backend_pid(nt.Server(77, log, jobs.api))
    log.write_text(nt.BACKEND_MARKER + "4321")
    assert nt.backend_pid(nt.Server(77, log, jobs.api)) == 4321


def test_cold_bootstrap_records_interpreter_pid_and_keeps_serve_arguments(monkeypatch, capsys):
    import runpy

    called = []
    monkeypatch.setattr(sys, "argv", ["-c", "serve"])
    monkeypatch.setattr(runpy, "run_module", lambda *a, **kw: called.append((a, kw)))
    exec(nt.BACKEND_BOOTSTRAP, {})
    assert sys.argv == ["oversight", "serve"]
    assert called == [(("oversight",), {"run_name": "__main__"})]
    assert capsys.readouterr().out == nt.BACKEND_MARKER + str(os.getpid()) + "\n"


@pytest.mark.parametrize("mode", ["--probe-lock", "--hard-kill-check"])
def test_cli_retains_outer_before_either_mode(monkeypatch, tmp_path, mode):
    calls = []

    async def probe(runtime):
        assert calls == ["outer"] and runtime == tmp_path
        calls.append("probe")

    async def checks(*, hard_kill):
        assert calls == ["outer"] and hard_kill is True
        calls.append("hard-kill")

    monkeypatch.setattr(nt.sys, "platform", "win32")
    monkeypatch.setattr(
        nt.sys,
        "argv",
        ["demo_check_nt.py", mode] + ([str(tmp_path)] if mode == "--probe-lock" else []),
    )
    monkeypatch.setattr(nt, "retain_outer", lambda: calls.append("outer"))
    monkeypatch.setattr(nt, "probe_lock", probe)
    monkeypatch.setattr(nt, "run_checks", checks)
    assert nt.main() == 0 and len(calls) == 2


@pytest.mark.anyio
@pytest.mark.parametrize("layout", ["packages/foo/src", "backend/src"])
async def test_own_fixture_setup_env_and_script_are_disposable(tmp_path, monkeypatch, layout):
    original = nt.run_process
    setup_calls = []

    async def setup(argv, **kwargs):
        if argv[0] != "uv":
            return await original(argv, **kwargs)
        setup_calls.append(argv)
        (tmp_path / "source/.venv/Lib/site-packages").mkdir(parents=True, exist_ok=True)
        from oversight.workspace import ProcessResult

        return ProcessResult(0, "", "", False, False, 0)

    monkeypatch.setattr(nt, "run_process", setup)
    monkeypatch.setenv("OVERSIGHT_DISCARD_OWN_RUNTIME", "inherited-authority")
    fixture = await nt.prepare_own_fixture(tmp_path, layout=layout)
    assert setup_calls == [
        ["uv", "--no-config", "venv", "--python", "3.12", str(fixture.source / ".venv")],
        [
            "uv",
            "--no-config",
            "pip",
            "install",
            "--python",
            str(fixture.source / ".venv/Scripts/python.exe"),
            "pytest==9.1.1",
            "pytest-asyncio==1.3.0",
        ],
    ]
    asynchronous = (fixture.source / "test_async.py").read_text()
    assert "@pytest_asyncio.fixture\nasync def copied_module" in asynchronous
    assert "@pytest.mark.asyncio\nasync def test_async_copy" in asynchronous
    assert "await asyncio.sleep(0)" in asynchronous
    assert "copied_module.is_relative_to(Path.cwd())" in asynchronous
    env = nt.own_fixture_env(8000, fixture)
    assert env["OVERSIGHT_WORKSPACE_SOURCE"] == str(fixture.source)
    assert env["OVERSIGHT_TEST_TIMEOUT_S"] == "75"
    assert env["OVERSIGHT_DISCARD_OWN_RUNTIME"] == ""
    assert env["META_API_KEY"] == env["BASETEN_API_KEY"] == ""
    assert env["OVERSIGHT_AGENT_MODEL"] == env["OVERSIGHT_AGENT_MODEL_B"] == "fake"
    assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD" not in env
    script = json.loads(fixture.script.read_text())
    tools = [turn["tool_calls"][0] for turn in script["turns"]]
    assert [t["name"] for t in tools] == [
        "read_file",
        "edit_file",
        "run_tests",
        "read_file",
        "edit_file",
        "run_tests",
        "finish",
    ]
    assert all(t["arguments"]["path"] == fixture.module for t in tools if "path" in t["arguments"])
    old = tools[1]["arguments"]["old_text"]
    assert (fixture.source / fixture.module).read_text().count(old) == 1
    assert not list((fixture.source / ".venv").rglob("top_level.txt"))
    assert (fixture.source / "crlf.py").read_bytes() == b"line = 1\r\n"
    config = (fixture.source / ".git/config").read_text()
    assert '[filter "sentinel"]' in config and "FILTER_SENTINEL" in config
    assert "worktree = " in config and "misleading-worktree" in config
    selected = fixture.source / fixture.module
    baseline = await nt.fixture_git(
        fixture.source, "--work-tree=" + str(fixture.source), "show", "HEAD:" + fixture.module
    )
    assert selected.read_bytes() == baseline.encode() + b"\n"
    assert not (fixture.source / "FILTER_SENTINEL").exists()


@pytest.mark.anyio
async def test_stat_dirty_sentinel_preserves_export_apply_bytes(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    module = "sample.py"
    selected = source / module
    original = b"def value():\n    return 41\n"
    selected.write_bytes(original)
    await nt.fixture_git(source, "init", "-q", "--template=", "-b", "main")
    await nt.fixture_git(source, "add", module)
    await nt.fixture_git(source, "commit", "-qm", "Fixture base")
    indexed_stat = selected.stat()
    nt.prepare_fixture_git_sentinel(source, module)

    # Match the driver's raw-byte staging, then export a change against that base.
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / module).write_bytes(selected.read_bytes())
    await nt.fixture_git(runtime, "init", "-q", "--template=", "-b", "main")
    await nt.fixture_git(runtime, "add", module)
    await nt.fixture_git(runtime, "commit", "-qm", "Runtime base")
    (runtime / module).write_bytes(selected.read_bytes().replace(b"return 41", b"return 42"))
    patch = tmp_path / "own.patch"
    await nt.fixture_git(runtime, "diff", "--binary", "--full-index", f"--output={patch}", "HEAD")
    target = tmp_path / "apply-target"
    await nt.fixture_git(source, "clone", "--no-hardlinks", str(source), str(target))
    await nt.fixture_git(target, "apply", "--check", str(patch))
    await nt.fixture_git(target, "apply", str(patch))
    assert (target / module).read_bytes() == (runtime / module).read_bytes()
    assert selected.read_bytes() == original
    assert selected.stat().st_mtime_ns >= indexed_stat.st_mtime_ns + 5_000_000_000
    nt.check_fixture_filters(tmp_path)


@pytest.mark.anyio
async def test_own_recovery_never_accepts_an_arbitrary_repository(tmp_path, monkeypatch):
    fixture = nt.OwnFixture(
        tmp_path / "person", tmp_path / "runtime", "code.py", tmp_path / "script"
    )
    monkeypatch.setattr(nt, "start_server", lambda *args: pytest.fail("started arbitrary recovery"))
    with pytest.raises(smoke.SmokeError, match="restricted to disposable"):
        await nt.fixture_recovery(fixture, {}, tmp_path)


@pytest.mark.parametrize("relative", ["source", "runtime/workspace", "runtime/tmp/combined"])
def test_fixture_filter_evidence_is_never_accepted(tmp_path, relative):
    nt.check_fixture_filters(tmp_path)
    sentinel = tmp_path / relative / "FILTER_SENTINEL"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("executed")
    with pytest.raises(smoke.SmokeError, match="source clean filter executed"):
        nt.check_fixture_filters(tmp_path)


@pytest.mark.anyio
@pytest.mark.parametrize("fail", [False, True])
async def test_six_run_driver_orders_demo_then_own_and_propagates_failure(
    tmp_path, monkeypatch, fail
):
    phases = []
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setattr(nt, "retain_outer", lambda: 99)
    monkeypatch.setattr(nt, "evidence_dir", lambda: logs)
    monkeypatch.setattr(Path, "is_file", lambda _: True)

    async def demo(number, *args, **kwargs):
        phases.append(f"demo {number}")

    async def own(*args):
        phases.append("three own fixtures")
        if fail:
            raise smoke.SmokeError("own phase failed")

    async def members(*args):
        phases.append("empty job")

    cleaned = []
    monkeypatch.setattr(nt, "run_once", demo)
    monkeypatch.setattr(nt, "run_own_checks", own)
    monkeypatch.setattr(nt, "wait_members", members)
    monkeypatch.setattr(nt, "cleanup", cleaned.append)
    if fail:
        with pytest.raises(smoke.SmokeError, match="own phase failed"):
            await nt.run_checks(own_fixtures=True)
        assert not cleaned
    else:
        await nt.run_checks(own_fixtures=True)
        assert phases[-1] == "empty job" and len(cleaned) == 1
        nt.shutil.rmtree(cleaned[0])
    assert phases[:4] == ["demo 1", "demo 2", "demo 3", "three own fixtures"]


@pytest.mark.anyio
async def test_fixture_recovery_orders_refusal_export_apply_then_exact_hash(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    fixture = nt.OwnFixture(
        tmp_path / "source", tmp_path / "runtime", "code.py", tmp_path / "script"
    )
    nt._disposable_sources.add(fixture.source)
    tree = fixture.runtime / "workspace"
    tree.mkdir(parents=True)
    (tree / "code.py").write_bytes(b"repaired\r\n")
    (tree / "marker.py").write_bytes(b"advanced\n")
    calls = []

    async def git(root, *args):
        calls.append(args[0])
        if args[0] == "rev-list":
            return "a" * 40
        if args[0] == "rev-parse":
            return "b" * 40
        if args[0] == "diff":
            assert {"--binary", "--full-index", "--no-ext-diff", "--no-textconv"} <= set(args)
        if args[0] == "apply" and "--check" not in args:
            root.mkdir()
            for name in ("code.py", "marker.py", "data.bin"):
                (root / name).write_bytes((tree / name).read_bytes())
        return ""

    async def start(argv, env, log):
        calls.append("discard" if "OVERSIGHT_DISCARD_OWN_RUNTIME" in env else "refuse")
        if "OVERSIGHT_DISCARD_OWN_RUNTIME" in env:
            assert env["OVERSIGHT_DISCARD_OWN_RUNTIME"] == "b" * 40
            assert calls[-3:] == ["apply", "apply", "discard"]
        log.write_text("Windows source refused: export first")
        return nt.Server(77, log, FakeJobs().api, cast(Any, Child()))

    monkeypatch.setattr(nt, "fixture_git", git)
    monkeypatch.setattr(nt, "start_server", start)
    monkeypatch.setattr(nt.Server, "dispose", AsyncMock())
    monkeypatch.setattr(nt.Server, "stop", AsyncMock())
    monkeypatch.setattr(nt, "probe_lock", AsyncMock())
    monkeypatch.setattr(Child, "wait", lambda *args, **kwargs: 1)
    monkeypatch.setattr(smoke, "wait_for_health", lambda *args: None)
    await nt.fixture_recovery(fixture, {"OVERSIGHT_PORT": "8000"}, tmp_path)
    assert calls == [
        "refuse",
        "rev-list",
        "add",
        "commit",
        "rev-parse",
        "diff",
        "clone",
        "apply",
        "apply",
        "discard",
    ]
