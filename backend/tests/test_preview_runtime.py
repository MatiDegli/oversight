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


def test_default_configuration_is_frozen_and_production_opening_stays_disabled(monkeypatch):
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
    with pytest.raises(ValueError, match=r"^preview profile is disabled$"):
        frozen.validate()
    values["OVERSIGHT_PREVIEW_PORTS"] = "8710"
    assert frozen.ports == (19681, 19682) and not frozen.production_open
    values["OVERSIGHT_PREVIEW_ENABLED"] = "true"
    with pytest.raises(ValueError, match=r"^invalid preview port pool$"):
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
    assert run.previews.profile.production_open
    if source:
        with pytest.raises(ValueError, match=r"^preview profile is disabled$"):
            run.previews.profile.validate()
    with pytest.raises(PreviewError):
        await run.previews.start(run_id="wrong", agent_id="agent-1", revision=1, head_commit="head")


async def test_composition_cannot_promote_untrusted_profile(tmp_path):
    from dataclasses import replace

    run = LiveRun(
        Settings(
            runtime_dir=tmp_path / ".runtime",
            preview_profile=replace(profile(), trusted_demo=False),
        )
    )
    assert run.previews is run.workspace.previews
    assert run.previews.profile.enabled and not run.previews.profile.trusted_demo
    with pytest.raises(ValueError, match=r"^preview profile is disabled$"):
        run.previews.profile.validate()


async def prepare_run(tmp_path):
    run = LiveRun(Settings(runtime_dir=tmp_path / ".runtime", preview_profile=profile()))
    await run.workspace.acquire_runtime()
    await run.workspace.reset()
    await run.workspace.check_isolation()
    tree = await run.workspace.create_worktree("agent-1")
    head = await tree.head()

    async def candidate(*args):
        return PreviewCandidate("run-20261103-000000", "agent-1", "change-1", 1, head, tree)

    run.previews.resolve_candidate = candidate
    processes = []

    def factory(registry, port):
        process = FakeProcess(registry, port)
        processes.append(process)
        return process

    run.previews.process_factory = factory
    return run, head, processes


@pytest.mark.parametrize("failed", [False, True])
async def test_enabled_shutdown_retains_drain_order(tmp_path, monkeypatch, failed):
    run, head, processes = await prepare_run(tmp_path)
    order = []

    ready = asyncio.Event()

    async def sink(record):
        order.append(record.state)
        if record.state == "ready":
            ready.set()

    run.previews.set_status_sink(sink)
    await run.previews.start(
        run_id="run-20261103-000000", agent_id="agent-1", revision=1, head_commit=head
    )
    async with asyncio.timeout(5):
        await ready.wait()
    process = processes[0]
    process.stop_failure = failed
    finish = finish_snapshot

    def snapshot(*args):
        assert process.dead and process.order[-1] == "namespace_dead"
        order.append("namespace_dead")
        order.append("snapshot_finished")
        finish(*args)

    async def manager_close():
        order.append("manager_close")

    async def store_close():
        assert run.previews._watcher is not None and run.previews._watcher.done()
        if not failed:
            assert process.dead and order.index("namespace_dead") < order.index("stopped")
        order.append("store_close")

    monkeypatch.setattr(barriers, "finish_snapshot", snapshot)
    monkeypatch.setattr(run, "_close_manager", manager_close)
    monkeypatch.setattr(run.store, "close", store_close)
    if failed:
        with pytest.raises(PreviewError):
            await run.shutdown()
        assert "stopped" not in order and "snapshot_finished" not in order
        assert run.workspace.runtime_owned and run.workspace.integrity.main_suspect
        assert run.previews.has_owner and run.previews.registry.read() is not None
        assert not process.dead
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
    run.previews.profile = profile()
    with pytest.raises(PreviewError, match=r"^Live preview admission is closed\.$"):
        await run.previews.start(
            run_id="run-20261103-000000", agent_id="agent-1", revision=1, head_commit=head
        )


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


@pytest.mark.parametrize("phase", ["launch", "ready"])
async def test_shutdown_starting_settles_tasks_before_store_close(tmp_path, monkeypatch, phase):
    from oversight.workspace.preview_types import FakeStatusSink

    run, head, processes = await prepare_run(tmp_path)
    sink, available = FakeStatusSink(), asyncio.Event()
    run.previews.set_status_sink(sink)
    factory = run.previews.process_factory

    def gated(registry, port):
        process = factory(registry, port)
        getattr(process, phase + "_gate").clear()
        available.set()
        return process

    run.previews.process_factory = gated
    created = await run.previews.start(
        run_id="run-20261103-000000", agent_id="agent-1", revision=1, head_commit=head
    )
    assert created.state == "starting"
    async with asyncio.timeout(5):
        await available.wait()
        process = processes[0]
        await getattr(process, phase + "_started").wait()
    owner, watcher = run.previews.barrier.owner, run.previews._watcher
    closed = []

    async def close_store():
        assert owner is not None and owner.launch_task is not None and watcher is not None
        assert owner.launch_task.done() and watcher.done()
        assert process.dead and run.previews.registry.read() is None
        assert sink.records[-1].state == "stopped"
        closed.append(True)

    monkeypatch.setattr(run.store, "close", close_store)
    async with asyncio.timeout(5):
        await run.shutdown()
    getattr(process, phase + "_gate").set()
    assert closed == [True] and not run.workspace.runtime_owned
    assert [r.state for r in sink.records] == ["starting", "stopping", "stopped"]
    assert not run.previews.has_owner


async def test_shutdown_restored_metadata_finalizes_before_store_close(tmp_path, monkeypatch):
    from oversight.workspace.preview_types import FakeStatusSink, PreviewRestoredError

    run, head, processes = await prepare_run(tmp_path)
    sink, order = FakeStatusSink(), []

    async def publish(record):
        await sink(record)
        order.append((record.state, record.reason))

    run.previews.set_status_sink(publish)
    created = await run.previews.start(
        run_id="run-20261103-000000", agent_id="agent-1", revision=1, head_commit=head
    )
    await sink.wait("ready", created.preview_id)
    config = run.workspace.main.git_identity.common / "config"
    original = config.read_bytes()
    config.write_bytes(original + b"\n[preview-tamper]\n marker = changed\n")

    async def store_close():
        order.append("store_close")

    monkeypatch.setattr(run.store, "close", store_close)
    try:
        async with asyncio.timeout(5):
            with pytest.raises(PreviewRestoredError):
                await run.shutdown()
        assert order.index(("failed", "worktree_changed")) < order.index("store_close")
        assert config.read_bytes() == original and processes[0].dead
        assert run.previews.registry.read() is None and not run.previews.has_owner
        finalizer, watcher = run.previews.barrier.finalizer, run.previews._watcher
        assert finalizer is not None and finalizer.done()
        assert watcher is not None and watcher.done()
        assert run.previews.barrier.owner is None and not run.workspace.runtime_owned
        assert run.workspace.integrity.rules_suspended
        assert not run.workspace.integrity.main_suspect and not run.workspace._cleanup_unverified
    finally:
        finalizer = run.previews.barrier.finalizer
        if finalizer is not None:
            async with asyncio.timeout(5):
                await finalizer


async def test_reviewed_self_app_stays_refused(tmp_path, monkeypatch):
    import sys
    from dataclasses import replace
    from pathlib import Path
    from tempfile import TemporaryDirectory

    from workspace.test_self_profile import self_checkout

    from oversight.preview_runtime import compose
    from oversight.preview_startup import reviewed_startup_report
    from oversight.workspace import (
        ReviewedAppAuthorization,
        Workspace,
        authorize_reviewed_app,
        load_preview_app,
    )
    from oversight.workspace.source import self_inventory

    # Keep this valid-disclosure case independent of the runner's potentially long TMPDIR.
    short = TemporaryDirectory(prefix="op-", dir="/tmp" if sys.platform == "linux" else None)
    tmp_path = Path(short.name)
    runtime, original, source, resources = await self_checkout(tmp_path, monkeypatch)
    inventory = await self_inventory(source, runtime)
    launch = load_preview_app(original, {}, **resources)
    assert launch is not None
    authority = authorize_reviewed_app(inventory, launch)
    frozen = replace(
        profile(), trusted_demo=False, reviewed_app=authority, interpreter=resources["python"]
    )
    frozen.validate()
    assert not frozen.trusted_demo
    with pytest.raises(ValueError, match="preview app is not authorized"):
        ReviewedAppAuthorization(inventory, launch).validate()
    with pytest.raises(ValueError, match="preview app is not authorized"):
        replace(authority, launch=replace(launch, mode="forged")).validate()
    for selected in ("", "boltons-d31d821", "C:/own"):
        with pytest.raises(ValueError, match="preview app is not authorized"):
            Settings(workspace_source=selected, preview_app="explicit override")
        with pytest.raises(ValueError, match="preview app is not authorized"):
            Settings(workspace_source=selected, preview_profile=frozen)
    workspace = Workspace(
        runtime,
        original,
        pytest_pin="9.1.1",
        source_inventory=inventory,
        demo_python=resources["python"],
    )

    async def candidate(*args):
        raise AssertionError("candidate resolution must not run")

    manager = compose(
        workspace,
        frozen,
        trusted_demo=False,
        resolve_candidate=candidate,
        memory_hold=lambda: None,
    )
    calls = []

    def forbidden_process(*args):
        calls.append(args)
        raise AssertionError("reviewed scaffold must not spawn")

    manager.process_factory = forbidden_process

    async def refused(message):
        with pytest.raises(PreviewError, match=message) as caught:
            manager._admit()
        assert caught.value.code == "not_configured"

    await refused("Reviewed-app startup disclosure is unavailable")
    manager.startup_report = "invalid"
    await refused("Reviewed-app startup disclosure is unavailable")
    manager.startup_report = reviewed_startup_report(frozen, "/r/previews/id/logs/build.log")
    await refused("Reviewed-app preview execution is not available")
    with pytest.raises(PreviewError, match="Reviewed-app preview execution is not available"):
        await manager._launch(None)  # pyright: ignore[reportArgumentType]
    assert calls == [] and not manager.has_owner
    resources["node"].write_text("stale executable")
    with pytest.raises(ValueError, match="preview app is not authorized"):
        authority.validate()
    await refused("Live preview profile is disabled")
