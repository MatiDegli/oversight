"""Explicit prepared Python bypasses uv but must pass its bounded pytest probe."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from backend.tests.workspace.workspace_helpers import MakeWorkspace
from backend.tests.workspace.workspace_helpers import pytestmark as pytestmark

from oversight.workspace import ProcessResult, WorkspaceError, core


@pytest.mark.parametrize("given", [True, False])
async def test_windows_git_is_mandatory_and_uv_only_without_prepared_python(
    make_workspace: MakeWorkspace, monkeypatch, given
):
    workspace = make_workspace("prereqs")
    workspace._demo_python_given = given
    await workspace.acquire_runtime()
    requested = []

    def executable(name):
        requested.append(name)
        if name == "uv" or not given:
            raise FileNotFoundError(name)
        return name

    monkeypatch.setattr(core, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(core, "executable", executable)
    # Stop after prerequisite resolution, before any platform-dependent Git operation.
    monkeypatch.setattr(workspace, "_populate", lambda: (_ for _ in ()).throw(OSError("resolved")))
    with pytest.raises(OSError):
        await workspace.reset()
    assert requested == ["git"]
    workspace.release_runtime()


async def test_windows_demo_without_prepared_python_requires_uv(make_workspace, monkeypatch):
    workspace = make_workspace("missing-uv")
    await workspace.acquire_runtime()
    requested = []

    def executable(name):
        requested.append(name)
        if name == "uv":
            raise FileNotFoundError(name)
        return name

    monkeypatch.setattr(core, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(core, "executable", executable)
    with pytest.raises(FileNotFoundError, match="uv"):
        await workspace.reset()
    assert requested == ["git", "uv"]
    workspace.release_runtime()


@pytest.mark.parametrize("outcome", ["ok", "failed", "missing", "timeout"])
async def test_prepared_probe_never_installs_or_rebuilds(make_workspace, monkeypatch, outcome):
    workspace = make_workspace("probe")
    workspace._demo_python_given = True
    workspace.runtime_root.mkdir()
    result = ProcessResult(1 if outcome == "failed" else 0, "", "", outcome == "timeout", False, 1)
    runner = AsyncMock(return_value=result)
    if outcome == "missing":
        runner.side_effect = FileNotFoundError("missing")
    monkeypatch.setattr(core, "run_process", runner)
    if outcome == "ok":
        await workspace.ensure_demo_venv()
    else:
        with pytest.raises(WorkspaceError, match="prepared demo Python"):
            await workspace.ensure_demo_venv()
    assert runner.await_count == 1
    assert runner.call_args.args[0] == [str(workspace._demo_python), "-c", "import pytest"]
    assert runner.call_args.kwargs["timeout_s"] == 60
    assert not (workspace.runtime_root / "demo-venv").exists()
