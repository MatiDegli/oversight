"""Injected NT policy runs on Linux; kernel/console proofs are explicitly native only."""

import asyncio
import errno
import json
import os
import sys
from pathlib import Path
from typing import Any, cast

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
async def test_empty_job_poll_yields_and_times_out_without_closing() -> None:
    jobs = FakeJobs()
    jobs.members = (10, 20, 30)  # launcher, Git, and an unrelated-session descendant
    ticks = 0

    async def ticking() -> None:
        nonlocal ticks
        for _ in range(4):
            await asyncio.sleep(0.005)
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
        (nt.NT_GRACEFUL_EXITS[0], False, 1),
        (None, True, 0),
        (nt.NT_GRACEFUL_EXITS[0], True, 1),
    ],
)
async def test_shutdown_verdict(tmp_path: Path, code: int | None, log: bool, deadline_s: float):
    jobs, child = FakeJobs(), Child()
    path = tmp_path / "server.log"
    path.write_text("Application shutdown complete" if log else "startup only")
    server = nt.Server(77, path, jobs.api, cast(Any, child))
    sent: list[tuple[int, int]] = []

    def send(pid: int, event: int) -> None:
        sent.append((pid, event))
        child.returncode = code

    if code == nt.NT_GRACEFUL_EXITS[0] and log:
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
        assert cleaned == [runtimes[0].parent]
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
    monkeypatch.setattr(child, "kill", kill)
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
    subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(120)'])
    Path({str(marker)!r}).write_text(json.dumps(nt.JOBS.pids(outer)))
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
        members = set(json.loads(marker.read_text())) - {driver.pid}
        assert len(members) >= 2  # real backend (including any launcher) and sleeper
        for pid in members:
            handle = api.OpenProcess(0x1000, False, pid)  # process only, no outer job handle
            assert handle
            handles.append(handle)
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
    native_console()
    if os.environ.get("OVERSIGHT_NT_FULL_DEMO") != "1":
        pytest.skip("full demo staged until both W2a and W2b land; set OVERSIGHT_NT_FULL_DEMO=1")
    await nt.run_checks(hard_kill=True)
