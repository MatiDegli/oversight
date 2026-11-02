"""Live composition uses pinned provenance, and still publishes the runtime's own commit."""

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from live_helpers import live_server, live_settings

from oversight.app import create_app
from oversight.workspace import run_process
from oversight.workspace.source import BOLTONS


@pytest.mark.anyio
async def test_fake_source_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = live_settings(tmp_path, workspace_source=BOLTONS.name)
    root = BOLTONS.checkout(settings.runtime_dir)
    root.mkdir(parents=True)
    (root / "outside.py").write_text("def value():\n    return 42\n")
    for args in (
        ["init", "-q"],
        ["add", "."],
        [
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "Outside source",
        ],
    ):
        result = await run_process(["git", *args], cwd=root, timeout_s=10)
        assert result.returncode == 0, result.stderr
    result = await run_process(["git", "rev-parse", "HEAD"], cwd=root, timeout_s=10)
    pin = result.stdout.strip()
    monkeypatch.setattr("oversight.workspace.source.BOLTONS", replace(BOLTONS, commit=pin))
    async with live_server(settings) as server:
        started, snapshot = server.events()[:2]  # helpers schema-validate every event
        assert started.payload.workspace == "boltons@d31d821"
        assert started.payload.base_commit != pin
        assert snapshot.payload.commit == started.payload.base_commit
        assert any(node.id == "outside:value" for node in snapshot.payload.nodes)
        assert (root / "outside.py").read_text() == "def value():\n    return 42\n"


@pytest.mark.parametrize("source", ["unknown", BOLTONS.name])
def test_invalid_source_refuses_app_startup(tmp_path: Path, source: str) -> None:
    with (
        pytest.raises(ValueError, match="boltons-d31d821"),
        TestClient(create_app(live_settings(tmp_path, workspace_source=source))),
    ):
        pass


@pytest.mark.anyio
async def test_wrong_pin_refuses_startup_before_reset(tmp_path: Path) -> None:
    settings = live_settings(tmp_path, workspace_source=BOLTONS.name)
    root = BOLTONS.checkout(settings.runtime_dir)
    root.mkdir(parents=True)
    result = await run_process(["git", "init", "-q"], cwd=root, timeout_s=10)
    assert result.returncode == 0
    result = await run_process(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "Wrong pin",
        ],
        cwd=root,
        timeout_s=10,
    )
    assert result.returncode == 0
    app = create_app(settings)
    with pytest.raises(ValueError, match=BOLTONS.commit):
        await app.state.live.startup()
    assert not (settings.runtime_dir / "workspace").exists()


@pytest.mark.parametrize(
    "source", [r"C:\private\project", "C:/private/project", r"\\host\share\project"]
)
def test_public_nt_path_refusal_before_construction(
    tmp_path: Path, source: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = live_settings(tmp_path, workspace_source=source)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("path selector reached Workspace/EventStore/reset/source Git")

    monkeypatch.setattr("oversight.app.Workspace", forbidden)
    monkeypatch.setattr("oversight.app.EventStore", forbidden)
    monkeypatch.setattr("oversight.workspace.source.run_process", forbidden)
    monkeypatch.setattr("oversight.app.preflight_placement", lambda _: None)
    monkeypatch.setattr("oversight.app.sys.platform", "win32")
    with pytest.raises(ValueError, match="OVERSIGHT_WORKSPACE_SOURCE"):
        create_app(settings)
