"""Native acceptance only; invoke with -o timeout_method=thread in a private NTFS clone."""

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
