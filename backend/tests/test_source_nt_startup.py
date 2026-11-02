"""Private startup composition: injected NT boundaries, never public path enablement."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from live_helpers import live_settings, two_fakes
from workspace.test_source_nt_copy import fixture, stage

from oversight import app
from oversight.workspace import InvalidPath, NtSource, OwnSource, core, source_nt
from oversight_contracts.commands import StartAgent


async def selected(tmp_path, monkeypatch, **settings):
    root = tmp_path / "source"
    root.mkdir()
    config = two_fakes(tmp_path, **settings)
    own = NtSource(root, config.runtime_dir, "C:\\private\\source", "C:\\private\\runtime")
    await fixture(own)
    monkeypatch.setattr(app, "select_source", lambda _: OwnSource(own.source_path))
    monkeypatch.setattr(OwnSource, "prepare", lambda *args: own)
    live = app.LiveRun(config)

    async def staging():
        assert live.workspace._lock_fd is not None
        return await stage(own, live.workspace._lock_fd)

    monkeypatch.setattr(live.workspace, "stage_own_source", staging)
    return live, own


@pytest.mark.parametrize("bad", ["C:relative", r"C:\private\..\source", r"\\host\share"])
def test_lexical_checks_precede_all_filesystem_and_construction(tmp_path, monkeypatch, bad):
    monkeypatch.setattr(app, "select_source", lambda _: OwnSource(bad))

    def forbidden(*args, **kwargs):
        pytest.fail("lexical refusal reached filesystem or constructor")

    monkeypatch.setattr(app, "Workspace", forbidden)
    monkeypatch.setattr(app, "EventStore", forbidden)
    monkeypatch.setattr(Path, "resolve", forbidden)
    monkeypatch.setattr(source_nt, "preflight_placement", forbidden)
    with pytest.raises((OSError, InvalidPath)):
        app.create_app(live_settings(tmp_path))


@pytest.mark.parametrize("placement", ["ide", "runtime", "source", "venv"])
def test_all_placements_precede_constructors(tmp_path, monkeypatch, placement):
    paths = dict(ide=r"C:\ide", runtime=r"C:\runtime", source=r"C:\source", venv=r"C:\source\.venv")
    called = []

    def preflight(path):
        called.append(path)
        if path == paths[placement]:
            raise OSError("placement refused")

    def prepare(self, runtime, ide):
        return source_nt.prepare_source(
            *[paths[k] for k in ("source", "runtime", "ide", "venv")], preflight=preflight
        )

    monkeypatch.setattr(app, "select_source", lambda _: OwnSource(paths["source"]))
    monkeypatch.setattr(OwnSource, "prepare", prepare)
    monkeypatch.setattr(app, "Workspace", lambda *a, **kw: pytest.fail("constructed workspace"))
    monkeypatch.setattr(app, "EventStore", lambda *a, **kw: pytest.fail("constructed store"))
    with pytest.raises(OSError, match="placement refused"):
        app.LiveRun(live_settings(tmp_path))
    assert called[-1] == paths[placement]


@pytest.mark.anyio
@pytest.mark.parametrize("refusal", ["lock/job", "prior", "trust", "stage", "reset", None])
async def test_startup_order_and_refusal_preserves_previous(tmp_path, monkeypatch, refusal):
    live, own = await selected(tmp_path, monkeypatch)
    workspace = live.workspace
    own.runtime.mkdir()
    previous = own.runtime / "workspace"
    previous.mkdir()
    (previous / "preserve").write_bytes(b"prior runtime")
    calls = []

    def step(name):
        async def call(*args, **kwargs):
            calls.append(name)
            if name == refusal:
                raise OSError(name)
            if name == "stage":
                return object()
            if name == "reset":
                assert "_nt_stage" in kwargs
                return "base"

        return call

    for name, target in (
        ("lock/job", "acquire_runtime"),
        ("prior", "guard_previous_runtime"),
        ("stage", "stage_own_source"),
        ("reset", "reset"),
    ):
        monkeypatch.setattr(workspace, target, step(name))
    monkeypatch.setattr(live, "_disclose_source", step("trust"))
    # Staging's type/metadata handoff is exercised with real staging in the next tests.
    from types import SimpleNamespace

    monkeypatch.setattr(
        workspace,
        "stage_own_source",
        AsyncMock(side_effect=OSError("stage"))
        if refusal == "stage"
        else AsyncMock(
            return_value=SimpleNamespace(
                selection=SimpleNamespace(inventory=SimpleNamespace(skipped_roots=2))
            )
        ),
    )
    staging = workspace.stage_own_source

    async def record_stage():
        calls.append("stage")
        return await staging()

    monkeypatch.setattr(workspace, "stage_own_source", record_stage)
    if refusal:
        with pytest.raises(OSError, match=refusal):
            await live._reset_workspace()
    else:
        assert await live._reset_workspace() == "base"
    order = ["lock/job", "prior", "trust", "stage", "reset"]
    assert calls == order[: order.index(refusal) + 1] if refusal else calls == order
    assert (previous / "preserve").read_bytes() == b"prior runtime"


@pytest.mark.anyio
@pytest.mark.parametrize("source", ["", "boltons-d31d821"])
async def test_selector_change_cannot_evade_previous_guard(tmp_path, monkeypatch, source):
    live = app.LiveRun(live_settings(tmp_path, workspace_source=source))
    monkeypatch.setattr(
        core,
        "guard_previous",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("preserve own runtime")),
    )
    monkeypatch.setattr(
        app, "validate_source", AsyncMock(side_effect=AssertionError("profile Git"))
    )
    monkeypatch.setattr(live.workspace, "reset", AsyncMock(side_effect=AssertionError("deletion")))
    with pytest.raises(OSError, match="preserve own"):
        await live._reset_workspace()
    assert live.workspace._lock_fd is None


@pytest.mark.anyio
@pytest.mark.parametrize("limit,wall", [(75, 600), (300, 1200)])
async def test_real_staging_readiness_limits_and_original_preservation(
    tmp_path, monkeypatch, caplog, limit, wall
):
    live, own = await selected(tmp_path, monkeypatch, test_timeout_s=limit)
    before = {
        str(p.relative_to(own.root)): p.read_bytes() for p in own.root.rglob("*") if p.is_file()
    }
    monkeypatch.setattr(
        live.workspace, "ensure_demo_venv", AsyncMock(side_effect=AssertionError("builder"))
    )
    # Missing venv is unavailable, and never a reason to lose graph/read/edit access.
    try:
        await live.startup()
        assert live._venv is None
        assert live._environment_unavailable is not None
        assert live.store.graph()[1] is not None
        assert (await live.get_file("new.py", None)).content == "new\r\n"
        assert live._limits.test_timeout_s == limit and live._limits.max_wall_clock_s == wall
        assert live._manager is not None and live._manager._revert is not None
        assert live._manager._revert.service.test_timeout_s == limit
        monkeypatch.setenv("OVERSIGHT_TEST_TIMEOUT_S", "999")
        object.__setattr__(live._settings, "test_timeout_s", 1)
        (own.root / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
        assert live._limits.test_timeout_s == limit
        for slot in ("A", "B"):
            await live.start(StartAgent(task="Inspect this copy", slot=slot))
        assert len(live._agents) == 2
        assert all(agent.task.limits is live._limits for agent in live._agents.values())
        assert (
            caplog.messages.count(
                f"Test limit: {limit} s. Agent time limit: {wall} s per round. "
                "These settings stay fixed for this run."
            )
            == 1
        )
        before.pop("pyproject.toml", None)
        after = {
            str(p.relative_to(own.root)): p.read_bytes()
            for p in own.root.rglob("*")
            if p.is_file() and p.name != "pyproject.toml"
        }
        assert before == after
        assert (own.runtime / "own-trust").is_file()
    finally:
        await live.shutdown()


@pytest.mark.anyio
async def test_staged_reset_cancellation_cleans_only_before_destruction(tmp_path, monkeypatch):
    live, own = await selected(tmp_path, monkeypatch)
    await live.workspace.acquire_runtime()
    staged = await live.workspace.stage_own_source()
    foreign = own.runtime / "source-stage-foreign"
    foreign.mkdir()
    monkeypatch.setattr(
        core, "guard_previous", lambda *a, **kw: (_ for _ in ()).throw(asyncio.CancelledError())
    )
    try:
        with pytest.raises(asyncio.CancelledError):
            await live.workspace.reset(_nt_stage=staged)
        assert not staged.root.exists() and foreign.exists()
    finally:
        live.workspace.release_runtime()


@pytest.mark.anyio
@pytest.mark.parametrize("stop", ["inventory", "validate", "stage", None])
async def test_workspace_stage_adapter_order_and_refusals(tmp_path, monkeypatch, stop):
    live, _ = await selected(tmp_path, monkeypatch)
    calls = []

    def action(name):
        def call(*args, **kwargs):
            calls.append(name)
            assert live.workspace._lock_fd is not None
            if stop == name:
                raise OSError(name)
            return name

        return call

    monkeypatch.setattr(core, "executable", lambda _: "C:/Git/git.exe")
    monkeypatch.setattr(core, "inventory", AsyncMock(side_effect=action("inventory")))
    monkeypatch.setattr(core, "validate_selection", action("validate"))
    monkeypatch.setattr(core, "stage_source", AsyncMock(side_effect=action("stage")))
    await live.workspace.acquire_runtime()
    try:
        if stop:
            with pytest.raises(OSError, match=stop):
                await core.Workspace.stage_own_source(live.workspace)
        else:
            assert await core.Workspace.stage_own_source(live.workspace) == "stage"
        sequence = ["inventory", "validate", "stage"]
        assert calls == sequence[: sequence.index(stop) + 1] if stop else calls == sequence
    finally:
        live.workspace.release_runtime()
