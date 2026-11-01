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
    handles = []

    def refuse(job, pid):
        handles.append(_open_process(pid))
        raise OSError("injected assignment refusal")

    monkeypatch.setattr(runner, "assign_job", refuse)
    with pytest.raises(OSError, match="assignment refusal"):
        await run_process(
            [sys.executable, "-c", f"open({str(marker)!r},'w').close()"], cwd=tmp_path, timeout_s=3
        )
    assert not marker.exists()
    import ctypes

    api, handle = handles[0]
    try:
        code = ctypes.c_uint32()
        assert api.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value != 259
    finally:
        api.CloseHandle(handle)


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
        assert transport.get_returncode() is not None  # Uvicorn re-emits the shutdown signal.
    finally:
        if not collector.exited.done():
            transport.kill()
            await wait_done(collector.exited, 5)
        transport.close()


@pytest.mark.parametrize("shadow", ["worktree/nul", "drive/dev/null"])
async def test_hooks_disabled(tmp_path, template, shadow):
    from oversight.workspace import WorkspaceError
    from oversight.workspace.test_tree import capture_test_tree

    workspace = Workspace(tmp_path / ".runtime", template, pytest_pin="9.1.1")
    await workspace.acquire_runtime()
    owned = None
    try:
        await workspace.reset()
        marker = tmp_path / "hook-ran"
        hook = f"#!/bin/sh\nprintf bad > '{marker.as_posix()}'\nexit 1\n"
        normal = workspace.main.root / ".git/hooks"
        (normal / "pre-commit").write_text(hook)
        (workspace.main.root / "changed.py").write_text("value = 1\n")
        assert await workspace.main.commit_all("Plain hooks fixture", workspace.user_author())
        assert not marker.exists()
        if shadow == "drive/dev/null":
            owned = Path("C:/dev/null")
            if owned.exists():
                owned = None
                pytest.skip("drive-root subcase: existing dev/null must remain untouched")
            try:
                owned.mkdir(parents=True)
            except PermissionError:
                owned = None
                pytest.skip("drive-root subcase: drive root is not writable")
            (owned / "pre-commit").write_text(hook)
        else:
            extended = Path("\\\\?\\" + str(workspace.main.root)) / "nul"
            extended.mkdir()
            (extended / "pre-commit").write_text(hook)
        (workspace.main.root / "changed.py").write_text("value = 2\n")
        if shadow == "worktree/nul":
            with pytest.raises(WorkspaceError):
                await workspace.main.commit_all("Refuse reserved path", workspace.user_author())
            assert not marker.exists()
            return
        assert await workspace.main.commit_all("Native fixture", workspace.user_author())
        async with workspace.locks(main=True), workspace.git_lease():
            await capture_test_tree(
                workspace.main.root,
                workspace.tmp_dir,
                env=workspace._git_env(workspace.main, None, None),
                flags=workspace.git_flags,
                timeout_s=10,
            )
        assert not marker.exists()
    finally:
        workspace.release_runtime()
        if owned is not None:
            (owned / "pre-commit").unlink(missing_ok=True)
            owned.rmdir()


async def test_reduced_env_restart(tmp_path, template, monkeypatch):
    import json

    monkeypatch.setenv("META_API_KEY", "must-not-reach-child")
    monkeypatch.setenv("BASETEN_API_KEY", "must-not-reach-child")
    for _ in range(2):
        workspace = Workspace(tmp_path / ".runtime", template, pytest_pin="9.1.1")
        await workspace.acquire_runtime()
        try:
            await workspace.reset()
            home = str(workspace.tmp_dir / "home")
            result = await run_process(
                [sys.executable, "-c", "import os,json;print(json.dumps(dict(os.environ)))"],
                cwd=workspace.main.root,
                env={"HOME": home},
                timeout_s=5,
            )
            env = json.loads(result.stdout)
            assert result.returncode == 0
            assert env["TEMP"] == env["TMP"] == env["USERPROFILE"] == home
            assert env["SYSTEMROOT"] and env["PYTHONUTF8"] == "1"
            assert "META_API_KEY" not in env and "BASETEN_API_KEY" not in env
            (workspace.main.root / "readonly").write_text("disposable")
            (workspace.main.root / "readonly").chmod(0o400)
        finally:
            workspace.release_runtime()


async def test_lstat_fstat_identity(tmp_path):
    import os

    from oversight.workspace.files import open_file

    target = tmp_path / "identity.py"
    target.write_bytes(b"x\r\n\x1a")
    with open_file(tmp_path, target.name) as handle:
        fresh, opened = target.lstat(), os.fstat(handle.fileno())
        assert fresh.st_ino != 0 and (fresh.st_dev, fresh.st_ino) == (opened.st_dev, opened.st_ino)
        assert handle.read() == b"x\r\n\x1a"


async def test_binary_write_hash(tmp_path):
    from oversight.workspace.files import content_hash, read_file, write_file

    temp = tmp_path / "temp"
    temp.mkdir()
    digest = write_file(tmp_path, temp, "a.py", "x\n\r\n\x1aé", None)
    assert (tmp_path / "a.py").read_bytes() == "x\n\r\n\x1aé".encode()
    assert (
        read_file(tmp_path, "a.py").hash == digest == content_hash((tmp_path / "a.py").read_bytes())
    )
    assert list(temp.iterdir()) == []


async def test_gitdir_roundtrip(workspace):
    from oversight.workspace.files import registered_worktree

    tree = await workspace.create_worktree("agent-1")
    pointer = (tree.root / ".git").read_bytes()
    assert pointer.startswith(b"gitdir: ") and pointer.endswith(b"\n")
    tree.git_identity.verify_registration()
    assert registered_worktree(workspace.runtime_root, workspace.main.root, "agent-1") == tree.root


@pytest.mark.parametrize(
    "path",
    [
        ".GIT",
        ".g\u0131t",
        ".git.",
        ".git ",
        "GIT~1",
        "a.py:s",
        "CON",
        "nul.txt",
        "COM1",
        "C:x",
        "a/../b",
        "CON/x.py",
        "NUL/a.py",
        "COM1/b/",
        "sub/CON",
        "CONIN$",
        "CONOUT$.txt",
        "LPT1.txt",
        "COM¹",
    ],
)
async def test_nt_path_refusals(tmp_path, path):
    from oversight.workspace import InvalidPath
    from oversight.workspace.files import open_file

    with pytest.raises(InvalidPath):
        open_file(tmp_path, path)


async def test_ads_and_short_names(tmp_path):
    from oversight.workspace import InvalidPath
    from oversight.workspace.files import open_file
    from oversight.workspace.secret_paths import is_secret_path

    target = tmp_path / "privatecredential.pem"
    target.write_bytes(b"private")
    stream = Path(str(target) + ":secret")
    await asyncio.to_thread(stream.write_bytes, b"ADS canary")
    assert is_secret_path(target.name + ":secret")
    with pytest.raises(InvalidPath):
        open_file(tmp_path, target.name + ":secret")


async def test_short_names_support(tmp_path):
    if sys.platform != "win32":
        pytest.skip("native Windows proof")
    import ctypes

    from oversight.workspace import InvalidPath
    from oversight.workspace.files import open_file

    target = tmp_path / "longprivatecredential.txt"
    target.write_bytes(b"canary")
    result = ctypes.create_unicode_buffer(32768)
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.GetShortPathNameW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    assert api.GetShortPathNameW(str(target), result, len(result)), ctypes.get_last_error()
    alias = Path(result.value).name
    if alias.casefold() == target.name.casefold():
        pytest.skip("8.3 names disabled on native test volume")
    with pytest.raises(InvalidPath):
        open_file(tmp_path, alias)


async def test_junction_site_matrix(tmp_path):
    import time

    from oversight.graph import WorkspaceGraph
    from oversight.graph._completeness import graph_completeness
    from oversight.workspace import InvalidPath
    from oversight.workspace.files import (
        copy_template,
        list_files,
        open_file,
        resolve_in,
        write_file,
    )
    from oversight.workspace.integrity import GitIdentity, scan_nested_git, veto_gitmodules

    root, outside, temp = tmp_path / "root", tmp_path / "outside", tmp_path / "tmp"
    for directory in (root, outside, temp):
        directory.mkdir()
    (root / ".git").mkdir()
    (outside / "canary.py").write_bytes(b"secret = True\n")
    (outside / ".gitmodules").touch()
    junction = root / "out"
    result = await run_process(
        ["cmd.exe", "/c", "mklink", "/J", str(junction), str(outside)], cwd=tmp_path, timeout_s=5
    )
    assert result.returncode == 0, result.stderr
    before = (outside / "canary.py").read_bytes()
    try:
        for operation in (
            lambda: resolve_in(root, "out/canary.py"),
            lambda: open_file(root, "out/canary.py"),
            lambda: write_file(root, temp, "out/new.py", "bad", None),
            lambda: copy_template(junction, tmp_path / "copy"),
        ):
            with pytest.raises(InvalidPath):
                operation()
        assert list_files(root) == []
        identity = GitIdentity(root, root / ".git", root / ".git")
        scan_nested_git(identity, time.monotonic() + 5)
        veto_gitmodules(root, time.monotonic() + 5)
        assert WorkspaceGraph(root).scan("main").nodes == []
        assert graph_completeness(root) == ()
    finally:
        junction.rmdir()
    assert (outside / "canary.py").read_bytes() == before
