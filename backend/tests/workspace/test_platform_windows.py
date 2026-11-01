"""Native acceptance only; invoke with -o timeout_method=thread in a private NTFS clone."""

import asyncio
import sys
from pathlib import Path

import pytest

from oversight.workspace import Workspace, run_process

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="native Windows proof"),
    pytest.mark.anyio,
]


async def test_runtime_lock_processes(tmp_path: Path, template: Path):
    workspace = Workspace(tmp_path / ".runtime", template, pytest_pin="9.1.1")
    await workspace.acquire_runtime()
    code = (
        "import os,msvcrt,sys; f=os.open(sys.argv[1],os.O_RDWR|os.O_BINARY);"
        "os.lseek(f,0,0);msvcrt.locking(f,msvcrt.LK_NBLCK,1)"
    )
    try:
        result = await run_process(
            [sys.executable, "-c", code, str(tmp_path / ".runtime/server.lock")],
            cwd=tmp_path,
            timeout_s=5,
        )
        assert result.returncode != 0
    finally:
        workspace.release_runtime()
    result = await run_process(
        [sys.executable, "-c", code, str(tmp_path / ".runtime/server.lock")],
        cwd=tmp_path,
        timeout_s=5,
    )
    assert result.returncode == 0


async def test_static_no_unc(tmp_path: Path, monkeypatch):
    from unittest.mock import Mock

    import httpx
    from fastapi import FastAPI

    from oversight.api import add_static, static

    (tmp_path / "index.html").write_text("UI")
    app = FastAPI()
    assert add_static(app, tmp_path)
    lookup = Mock(side_effect=AssertionError("UNC reached filesystem"))
    monkeypatch.setattr(static, "_file_under", lookup)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://local") as c:
        for url in ("/%5C%5Chost%5Cshare%5Cx", "http://local///host/share/x"):
            assert (await c.get(url)).status_code == 404
    lookup.assert_not_called()


def _open_process(pid):
    import ctypes

    if sys.platform != "win32":
        pytest.skip("Windows process handles")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.restype = ctypes.c_void_p
    api.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    api.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = api.OpenProcess(0x1000, False, pid)
    assert handle, ctypes.get_last_error()
    return api, handle


async def _grandchild(tmp_path, demo_python, mode):
    if sys.platform != "win32":
        pytest.skip("native Windows process proof")
    import ctypes
    import time

    marker, go = tmp_path / "child.pid", tmp_path / "go"
    script = (
        "import subprocess,sys,time,pathlib; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']); "
        f"pathlib.Path({str(marker)!r}).write_text(str(p.pid)); "
        f"go=pathlib.Path({str(go)!r}); "
        "exec('while not go.exists(): time.sleep(0.01)')"
    )
    task = asyncio.create_task(
        run_process([str(demo_python), "-c", script], cwd=tmp_path, timeout_s=5)
    )
    deadline = time.monotonic() + 4
    while not marker.exists():
        assert time.monotonic() < deadline and not task.done()
        await asyncio.sleep(0.01)
    api, handle = _open_process(int(marker.read_text()))
    try:
        if mode == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            if mode == "normal":
                go.touch()
            result = await task
            assert result.timed_out == (mode == "timeout")
            assert (result.returncode == 0) == (mode == "normal")
        code = ctypes.c_uint32()
        assert api.GetExitCodeProcess(handle, ctypes.byref(code))
        assert code.value != 259  # STILL_ACTIVE: must be dead before run_process returns.
    finally:
        api.CloseHandle(handle)
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_venv_grandchild_timeout(tmp_path, demo_python):
    await _grandchild(tmp_path, demo_python, "timeout")


async def test_venv_grandchild_cancel(tmp_path, demo_python):
    await _grandchild(tmp_path, demo_python, "cancel")


async def test_venv_grandchild_normal_exit(tmp_path, demo_python):
    await _grandchild(tmp_path, demo_python, "normal")


async def test_job_failure_cleanup(tmp_path, monkeypatch):
    from oversight.workspace import runner

    marker = tmp_path / "must-not-execute"

    def refuse(job, pid):
        raise OSError("injected assignment refusal")

    monkeypatch.setattr(runner, "assign_job", refuse)
    with pytest.raises(OSError, match="assignment refusal"):
        await run_process(
            [sys.executable, "-c", f"open({str(marker)!r},'w').close()"], cwd=tmp_path, timeout_s=3
        )
    assert not marker.exists()


async def test_reacquire_and_ledger_survive(tmp_path, template):
    runtime = tmp_path / ".runtime"
    for _ in range(2):
        workspace = Workspace(runtime, template, pytest_pin="9.1.1")
        await workspace.acquire_runtime()
        try:
            if not (runtime / "ledger.json").exists():
                (runtime / "ledger.json").write_text('{"spent_usd": 3, "unknown": []}')
            await workspace.reset()
            assert '"spent_usd": 3' in (runtime / "ledger.json").read_text()
        finally:
            workspace.release_runtime()


async def test_console_shutdown(tmp_path, monkeypatch):
    import os
    import signal

    from oversight.workspace.runner import ProcessCollector, child_env, wait_done

    if sys.platform != "win32":
        pytest.skip("native console control")
    import ctypes

    if not ctypes.WinDLL("kernel32").GetConsoleWindow():
        pytest.skip("run from non-elevated PowerShell with an attached console")
    runtime = tmp_path / ".runtime"
    monkeypatch.setenv("OVERSIGHT_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("OVERSIGHT_AGENT_MODEL", "fake")
    monkeypatch.setenv("OVERSIGHT_WORKSPACE_SOURCE", "")
    collector = ProcessCollector(262144)
    transport, _ = await asyncio.get_running_loop().subprocess_exec(
        lambda: collector,
        sys.executable,
        "-m",
        "oversight",
        "serve",
        env=child_env({k: v for k, v in os.environ.items() if k.startswith("OVERSIGHT_")}),
        creationflags=0x200,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(30):
            while "Application startup complete" not in collector.stderr.text():
                assert not collector.exited.done(), collector.stderr.text()
                await asyncio.sleep(0.05)
        os.kill(transport.get_pid(), signal.CTRL_BREAK_EVENT)
        assert await wait_done(collector.exited, 10), collector.stderr.text()
        assert await wait_done(collector.closed, 2)
        assert "Application shutdown complete" in collector.stderr.text()
        assert transport.get_returncode() == 0
    finally:
        if not collector.exited.done():
            transport.kill()
            await wait_done(collector.exited, 5)
        transport.close()
