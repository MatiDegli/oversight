"""Injected NT policy runs on Linux; kernel/console proofs are explicitly native only."""

import asyncio
import errno
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
