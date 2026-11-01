"""Injected kernel failures and async lifetime races; no Windows host required."""

import asyncio
import ctypes
import sys
from typing import cast
from unittest.mock import Mock

import pytest

from oversight.workspace import Workspace, core, runner
from oversight.workspace import _platform as platform


class Kernel:
    def __init__(self, failure="", *, threads=(41,), resume=1):
        self.failure, self.threads, self.resume = failure, list(threads), resume
        self.calls = []
        self.error = 18
        self.offset = 0

    def __getattr__(self, name):
        def call(*args):
            self.calls.append((name, args))
            if name == "GetLastError":
                return self.error
            if name == self.failure:
                self.error = 5
                return 0xFFFFFFFF if name == "ResumeThread" else 0
            if name == "CreateJobObjectW":
                return 101
            if name == "CreateToolhelp32Snapshot":
                return 102
            if name in ("OpenProcess", "OpenThread"):
                return 103
            if name in ("Thread32First", "Thread32Next"):
                if name == "Thread32First":
                    self.offset = 0
                if self.offset == len(self.threads):
                    self.error = 18
                    return 0
                entry = ctypes.cast(args[1], ctypes.POINTER(platform._Thread)).contents
                entry.pid, entry.tid = 20, self.threads[self.offset]
                self.offset += 1
            if name == "ResumeThread":
                return self.resume
            return 1

        return call

    @property
    def api(self):
        return cast(platform.Kernel, self)


def test_job_flags_and_handle_access():
    kernel = Kernel()
    job = platform.create_kill_job(kernel=kernel.api)
    platform.assign_job(job, 20, kernel=kernel.api)
    platform.resume_suspended(20, kernel=kernel.api)
    platform.terminate_job(job, kernel=kernel.api)
    platform.close_job(job, kernel=kernel.api)
    assert kernel.calls[0] == ("CreateJobObjectW", (None, None))
    assert ("OpenProcess", (0x101, False, 20)) in kernel.calls
    assert ("OpenThread", (2, False, 41)) in kernel.calls
    assert ("TerminateJobObject", (101, 0xC000013A)) in kernel.calls
    assert [args[0] for name, args in kernel.calls if name == "CloseHandle"] == [103, 103, 102, 101]


@pytest.mark.parametrize(
    "failure",
    [
        "CreateJobObjectW",
        "SetInformationJobObject",
        "OpenProcess",
        "AssignProcessToJobObject",
        "CreateToolhelp32Snapshot",
        "Thread32First",
        "Thread32Next",
        "OpenThread",
        "ResumeThread",
        "TerminateJobObject",
        "QueryInformationJobObject",
        "CloseHandle",
    ],
)
def test_kernel_failures_refuse(failure):
    kernel = Kernel(failure)
    with pytest.raises(OSError):
        if failure in {"CreateJobObjectW", "SetInformationJobObject"}:
            platform.create_kill_job(kernel=kernel.api)
        elif failure in {"OpenProcess", "AssignProcessToJobObject"}:
            platform.assign_job(101, 20, kernel=kernel.api)
        elif failure == "TerminateJobObject":
            platform.terminate_job(101, kernel=kernel.api)
        elif failure == "QueryInformationJobObject":
            platform.job_pids(101, kernel=kernel.api)
        elif failure == "CloseHandle":
            platform.close_job(101, kernel=kernel.api)
        else:
            platform.resume_suspended(20, kernel=kernel.api)
    if failure in {"OpenThread", "ResumeThread", "Thread32First", "Thread32Next"}:
        assert ("CloseHandle", (102,)) in kernel.calls


@pytest.mark.parametrize("threads,resume", [((), 1), ((41, 42), 1), ((41,), 0), ((41,), 2)])
def test_resume_requires_one_thread_suspended_once(threads, resume):
    kernel = Kernel(threads=threads, resume=resume)
    with pytest.raises(OSError):
        platform.resume_suspended(20, kernel=kernel.api)
    assert ("CloseHandle", (102,)) in kernel.calls


class Transport(asyncio.SubprocessTransport):
    def __init__(self, collector):
        self.collector, self.code, self.was_closed = collector, None, False

    def get_pid(self):
        return 20

    def get_returncode(self):
        return self.code

    def get_extra_info(self, name, default=None):
        return default

    def close(self):
        self.was_closed = True

    def exit(self, code=0):
        self.code = code
        self.collector.process_exited()
        self.collector.connection_lost(None)


@pytest.fixture
async def launch(monkeypatch):
    started = asyncio.Event()
    state = {}
    calls = []

    async def spawn(factory, *argv, **kwargs):
        collector = factory()
        transport = Transport(collector)
        collector.pipe_data_received(1, b"partial output")
        state.update(transport=transport, kwargs=kwargs)
        calls.append("spawn")
        started.set()
        return transport, collector

    def resume(pid):
        calls.append("resume")
        if state.get("normal", True):
            state["transport"].exit(state.get("code", 0))

    def kill(job):
        calls.append("kill")
        if not state["transport"].collector.exited.done():
            state["transport"].exit(platform.NT_KILL_CODE)

    monkeypatch.setattr(asyncio.get_running_loop(), "subprocess_exec", spawn)
    monkeypatch.setattr(runner, "create_kill_job", lambda: 101)
    monkeypatch.setattr(runner, "assign_job", lambda job, pid: calls.append("assign"))
    monkeypatch.setattr(runner, "resume_suspended", resume)
    monkeypatch.setattr(runner, "terminate_job", kill)
    monkeypatch.setattr(runner, "terminate_suspended", lambda *a, **k: kill(101))
    monkeypatch.setattr(runner, "job_pids", lambda job: ())
    monkeypatch.setattr(runner, "close_job", lambda job: calls.append("close"))
    state["started"] = started
    return state, calls


@pytest.mark.anyio
@pytest.mark.parametrize("code", [0, 7])
async def test_normal_exit_still_kills_descendants(tmp_path, launch, code):
    state, calls = launch
    state["code"] = code
    result = await runner._execute_nt(["python.exe"], tmp_path, 1, None, 100, asyncio.Event())
    assert result.returncode == code and result.stdout == "partial output"
    assert calls == ["spawn", "assign", "resume", "kill", "close"]
    assert state["kwargs"]["creationflags"] == platform.NT_CREATE_FLAGS
    assert "start_new_session" not in state["kwargs"]
    assert state["transport"].was_closed


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["assign_job", "resume_suspended"])
async def test_suspended_launch_failure_kills_waits_closes(tmp_path, launch, monkeypatch, failure):
    state, calls = launch
    monkeypatch.setattr(runner, failure, Mock(side_effect=OSError("launch refused")))
    with pytest.raises(OSError, match="launch refused"):
        await runner._execute_nt(["python.exe"], tmp_path, 1, None, 100, asyncio.Event())
    assert calls[-1] == "close" and "kill" in calls
    assert state["transport"].collector.exited.done() and state["transport"].was_closed


@pytest.mark.anyio
async def test_timeout_race_never_returns_success(tmp_path, launch):
    state, _ = launch
    state["normal"] = False
    result = await runner._execute_nt(["python.exe"], tmp_path, 0, None, 100, asyncio.Event())
    assert result.returncode != 0 and result.timed_out


@pytest.mark.anyio
async def test_repeated_cancel_waits_for_responsive_empty_poll(tmp_path, launch, monkeypatch):
    state, calls = launch
    state["normal"] = False
    polls = 0

    def pids(job):
        nonlocal polls
        polls += 1
        return (99,) if polls < 8 else ()

    monkeypatch.setattr(runner, "job_pids", pids)
    monkeypatch.setattr(sys, "platform", "win32")
    task = asyncio.create_task(runner.run_process(["python.exe"], cwd=tmp_path, timeout_s=3))
    await state["started"].wait()
    task.cancel()
    for _ in range(3):
        await asyncio.sleep(0.01)
        task.cancel()
        assert not task.done()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert polls == 8 and calls[-1] == "close" and state["transport"].was_closed


@pytest.mark.anyio
async def test_cleanup_failure_refuses_output(tmp_path, launch, monkeypatch):
    state, calls = launch
    monkeypatch.setattr(runner, "job_pids", Mock(side_effect=OSError("membership unknown")))
    with pytest.raises(OSError, match="membership unknown"):
        await runner._execute_nt(["python.exe"], tmp_path, 1, None, 100, asyncio.Event())
    assert calls[-1] == "close" and state["transport"].was_closed


def test_lifetime_job_is_singleton_even_after_reacquisition(monkeypatch):
    kernel = Kernel()
    monkeypatch.setattr(platform, "_lifetime_job", None)
    monkeypatch.setattr(platform, "kernel32", lambda: kernel.api)
    for _ in range(3):
        platform.ensure_lifetime_job()
    assert [n for n, _ in kernel.calls].count("CreateJobObjectW") == 1
    assert ("AssignProcessToJobObject", (101, -1)) in kernel.calls
    assert not any(n == "CloseHandle" for n, _ in kernel.calls)


@pytest.mark.anyio
async def test_failed_job_startup_releases_runtime_without_reset(tmp_path, template, monkeypatch):
    from unittest.mock import AsyncMock

    workspace = Workspace(tmp_path / ".runtime", template, pytest_pin="9.1.1")
    # Inject only the NT branches, retaining a real Linux fd and file-lock lifecycle.
    from oversight.workspace._platform import acquire_lock, release_lock

    fd = acquire_lock(tmp_path / "lock")
    monkeypatch.setattr(workspace, "_acquire_sync", lambda: setattr(workspace, "_lock_fd", fd))
    monkeypatch.setattr(core, "release_lock", lambda fd: __import__("os").close(fd))
    monkeypatch.setattr(core, "ensure_lifetime_job", lambda: None)
    probe = AsyncMock(side_effect=OSError("incompatible ancestor job"))
    monkeypatch.setattr(core, "probe_job_support", probe)
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(OSError, match="incompatible ancestor"):
        await workspace.acquire_runtime()
    assert workspace._lock_fd is None and not workspace.main.root.exists()
    monkeypatch.setattr(sys, "platform", "linux")
    replacement = acquire_lock(tmp_path / "lock")
    release_lock(replacement)


@pytest.mark.anyio
async def test_cleanup_failure_retains_partial_suite_report(tmp_path, launch, monkeypatch):
    import json
    from pathlib import Path

    from oversight.workspace.suite import SuiteUnavailable, run_suite

    (tmp_path / "tmp").mkdir()
    original = asyncio.get_running_loop().subprocess_exec

    async def spawn(factory, *argv, **kwargs):
        report = Path(kwargs["env"]["OVERSIGHT_REPORT"])
        await asyncio.to_thread(
            report.write_text,
            json.dumps(
                {"kind": "test", "nodeid": "test_x.py::test_x", "when": "call", "outcome": "passed"}
            )
            + "\n",
        )
        return await original(factory, *argv, **kwargs)

    monkeypatch.setattr(asyncio.get_running_loop(), "subprocess_exec", spawn)
    monkeypatch.setattr(runner, "job_pids", Mock(side_effect=OSError("cleanup unverified")))
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(SuiteUnavailable) as exc:
        await run_suite(
            python=tmp_path / "python.exe",
            plugin_dir=tmp_path,
            runtime_root=tmp_path,
            tree_root=tmp_path,
            timeout_s=1,
            redact=lambda s: s,
            spawn=runner.run_process,
        )
    assert exc.value.run.result.passed == 1
    assert exc.value.run.result.outcome == "error"


@pytest.mark.anyio
async def test_job_empty_deadline_refuses_evidence(tmp_path, launch, monkeypatch):
    state, calls = launch
    monkeypatch.setattr(runner, "KILL_WAIT_S", 0.02)
    monkeypatch.setattr(runner, "job_pids", lambda job: (99,))
    with pytest.raises(OSError, match="deadline"):
        await runner._execute_nt(["python.exe"], tmp_path, 1, None, 100, asyncio.Event())
    assert calls[-1] == "close" and state["transport"].was_closed


@pytest.mark.anyio
async def test_launch_failure_still_terminates_job_if_process_termination_fails(
    tmp_path, launch, monkeypatch
):
    state, calls = launch
    monkeypatch.setattr(runner, "assign_job", Mock(side_effect=OSError("assignment failed")))
    monkeypatch.setattr(
        runner, "terminate_suspended", Mock(side_effect=OSError("termination failed"))
    )
    with pytest.raises(OSError, match="termination failed"):
        await runner._execute_nt(["python.exe"], tmp_path, 1, None, 100, asyncio.Event())
    assert calls[-2:] == ["kill", "close"]
    assert state["transport"].collector.exited.done() and state["transport"].was_closed
