"""Frozen configuration, live composition and terminal publication before store closure."""

import asyncio

import pytest
from workspace.preview_fakes import FakeProcess, profile

from oversight.app import LiveRun
from oversight.settings import Settings, resolve_preview_profile
from oversight.workspace import PreviewCandidate, PreviewError
from oversight.workspace import preview_barrier as barriers
from oversight.workspace.snapshot import finish_snapshot

pytestmark = pytest.mark.anyio


def test_explicit_configuration_is_frozen_and_production_opening_stays_disabled(monkeypatch):
    from oversight.workspace import PreviewProfile

    monkeypatch.setattr(PreviewProfile, "validate_files", lambda self: None)
    base = profile()
    values = {
        "OVERSIGHT_PREVIEW_ADAPTER": str(base.adapter),
        "OVERSIGHT_PREVIEW_PYTHON": str(base.interpreter),
        "OVERSIGHT_PREVIEW_PORTS": "19681,19682",
        "OVERSIGHT_PREVIEW_ORIGINS": "http://127.0.0.1:8710",
    }
    frozen = resolve_preview_profile(values, 8710, None)
    frozen.validate()
    values["OVERSIGHT_PREVIEW_PORTS"] = "8710"
    assert frozen.ports == (19681, 19682) and not frozen.production_open
    with pytest.raises(ValueError):
        resolve_preview_profile(values, 8710, None).validate()
    assert not resolve_preview_profile({}, 8710, None).configured


@pytest.mark.parametrize("source", ["", "boltons-d31d821"])
async def test_composition_has_one_manager_and_generic_profiles_remain_disabled(tmp_path, source):
    run = LiveRun(
        Settings(
            runtime_dir=tmp_path / ".runtime", workspace_source=source, preview_profile=profile()
        )
    )
    assert run.previews is run.workspace.previews
    assert run.previews.profile.trusted_demo is (not source)
    assert not run.previews.profile.production_open
    with pytest.raises(PreviewError):
        await run.previews.start(run_id="wrong", agent_id="agent-1", revision=1, head_commit="head")


async def prepare_run(tmp_path):
    run = LiveRun(Settings(runtime_dir=tmp_path / ".runtime", preview_profile=profile()))
    await run.workspace.acquire_runtime()
    await run.workspace.reset()
    await run.workspace.check_isolation()
    tree = await run.workspace.create_worktree("agent-1")
    head = await tree.head()

    async def candidate(*args):
        return PreviewCandidate("run", "agent-1", "change-1", 1, head, tree)

    run.previews.resolve_candidate = candidate
    processes = []

    def factory(registry, port):
        process = FakeProcess(registry, port)
        processes.append(process)
        return process

    run.previews.process_factory = factory
    return run, head, processes


@pytest.mark.parametrize("failed", [False, True])
async def test_shutdown_keeps_sink_and_store_until_verified_terminal(tmp_path, monkeypatch, failed):
    run, head, processes = await prepare_run(tmp_path)
    order = []

    async def sink(record):
        order.append(record.state)

    run.previews.set_status_sink(sink)
    await run.previews.start(run_id="run", agent_id="agent-1", revision=1, head_commit=head)
    process = processes[0]
    process.stop_failure = failed
    finish = finish_snapshot

    def snapshot(*args):
        assert process.dead
        order.append("snapshot_finished")
        finish(*args)

    async def manager_close():
        order.append("manager_close")

    async def store_close():
        order.append("store_close")

    monkeypatch.setattr(barriers, "finish_snapshot", snapshot)
    monkeypatch.setattr(run, "_close_manager", manager_close)
    monkeypatch.setattr(run.store, "close", store_close)
    if failed:
        with pytest.raises(PreviewError):
            await run.shutdown()
        assert "stopped" not in order and "snapshot_finished" not in order
        assert run.workspace.runtime_owned and run.workspace.integrity.main_suspect
        assert run.previews._watcher is not None
        run.previews._watcher.cancel()
        await asyncio.gather(run.previews._watcher, return_exceptions=True)
    else:
        await run.shutdown()
        assert (
            order.index("snapshot_finished") < order.index("stopped") < order.index("manager_close")
        )
        assert not run.workspace.runtime_owned
    assert order.index("manager_close") < order.index("store_close")
    with pytest.raises(PreviewError):
        await run.previews.start(run_id="run", agent_id="agent-1", revision=1, head_commit=head)


async def test_source_startup_lease_does_not_require_uncreated_main_byte_override(
    tmp_path, monkeypatch
):

    from oversight.workspace import ProcessResult, core

    run = LiveRun(Settings(runtime_dir=tmp_path / ".runtime"))
    await run.workspace.acquire_runtime()
    run.workspace.nt_source_bytes = True
    calls = []

    def byte_policy(*args):
        pytest.fail("source Git attempted to verify the uncreated disposable main")

    async def spawn(*args, **kwargs):
        assert run.workspace.operations.holds(main=True)
        calls.append("source")
        return ProcessResult(0, "git version", "", False, False, 1)

    monkeypatch.setattr(core, "verify_byte_override", byte_policy)
    monkeypatch.setattr(core, "run_process", spawn)
    result = await run.workspace.startup_git(["git", "--version"], cwd=tmp_path, timeout_s=2)
    assert result.returncode == 0 and calls == ["source"]
    run.workspace.release_runtime()


def test_explicit_settings_always_exclude_actual_server_ports():
    settings = Settings(port=19681, vite_port=19682, preview_profile=profile())
    assert settings.preview_profile.backend_port == 19681
    assert settings.preview_profile.vite_port == 19682
    with pytest.raises(ValueError, match="port pool"):
        settings.preview_profile.validate()
