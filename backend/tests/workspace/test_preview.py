"""Facade, zero-launch refusals, ownership and deterministic lifetime/memory admission."""

import asyncio
from dataclasses import asdict, replace
from typing import Any

import pytest

from oversight import workspace as facade
from oversight.workspace import PreviewError, PreviewProfile
from oversight.workspace.preview_types import ISOLATION_MESSAGE, PreviewStatusRecord

from .preview_fakes import FakeProcess, harness, profile

pytestmark = pytest.mark.anyio


async def test_public_interface_and_sink_excludes_output_url_and_token(workspace):
    for name in (
        "PreviewManager",
        "PreviewProfile",
        "PreviewCandidate",
        "PreviewCreatedResult",
        "PreviewStatusRecord",
        "PreviewDetailsResult",
        "PreviewStatusSink",
        "PreviewError",
    ):
        assert name in facade.__all__ and getattr(facade, name)
    h = await harness(workspace)
    created = await h.start()
    assert len(created.lease_token) == 64 and created.state == "ready"
    assert created.lease_token not in repr(created)
    url = (await h.manager.get(created.preview_id)).url
    assert url and url.startswith("http://127.0.0.1:")
    assert [r.state for r in h.sink.records] == ["starting", "ready"]
    for record in h.sink.records:
        assert set(asdict(record)) == {
            "preview_id",
            "run_id",
            "agent_id",
            "change_id",
            "revision",
            "head_commit",
            "state",
            "reason",
        }
    await h.manager.renew(created.preview_id, created.lease_token)
    assert len(h.sink.records) == 2
    stopped = await h.manager.stop(created.preview_id, created.lease_token)
    assert (stopped.state, stopped.reason) == ("stopped", "closed")
    assert (await h.manager.get(created.preview_id)).url is None
    assert h.processes[0].dead and h.manager.registry.read() is None
    await h.close()


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "partial",
        "reserved",
        "vite",
        "relative",
        "origin",
        "generic",
        "sink",
        "memory",
        "C",
        "suspect",
    ],
)
async def test_invalid_admission_never_launches(workspace, fault):
    h = await harness(workspace)
    profiles = {
        "missing": PreviewProfile(),
        "partial": replace(profile(), interpreter=None),
        "reserved": replace(profile(), ports=(8710,)),
        "vite": replace(profile(), vite_port=5710, ports=(5710,)),
        "relative": replace(profile(), interpreter=__import__("pathlib").Path("python")),
        "origin": replace(profile(), origins=("http://127.0.0.1:8710/path",)),
        "generic": replace(profile(), trusted_demo=False),
    }
    if fault in profiles:
        h.manager.profile = profiles[fault]
    elif fault == "sink":
        h.manager._sink = None
    elif fault == "memory":
        h.manager.memory_hold = lambda: "held"
    elif fault == "C":
        workspace._isolation.invalidate("fake")
    else:
        workspace.integrity.main_suspect = True
    with pytest.raises(Exception) as caught:
        await h.start()
    if fault == "C":
        assert str(caught.value) == ISOLATION_MESSAGE
    assert h.processes == [] and not h.manager.has_owner and h.sink.records == []


@pytest.mark.parametrize(
    "field,value",
    [("run_id", "other"), ("agent_id", "lead-1"), ("revision", 2), ("head_commit", "f" * 40)],
)
async def test_stale_or_wrong_identity_never_launches(workspace, field, value):
    h = await harness(workspace)
    request: dict[str, Any] = dict(run_id="run", agent_id="agent-1", revision=1, head_commit=h.head)
    request[field] = value
    with pytest.raises(PreviewError):
        await h.manager.start(**request)
    assert h.processes == []


async def test_dirty_launch_refuses_without_staging(workspace):
    h = await harness(workspace)
    path = h.tree.root / "left.txt"
    path.write_text("application leftovers")
    with pytest.raises(PreviewError):
        await h.start()
    assert h.processes == [] and path.read_text() == "application leftovers"


async def test_capacity_is_reserved_before_any_preflight_git(workspace, monkeypatch):
    h = await harness(workspace)
    created = await h.start()
    calls = []
    original = workspace.git

    async def git(*args, **kwargs):
        calls.append(args)
        return await original(*args, **kwargs)

    monkeypatch.setattr(workspace, "git", git)
    with pytest.raises(PreviewError, match="already has an owner"):
        await h.start()
    assert calls == [] and h.processes[0].live
    with pytest.raises(PreviewError):
        await h.manager.stop(created.preview_id, "cross-preview-token")
    assert h.processes[0].live
    await h.manager.stop(created.preview_id, created.lease_token)
    second = await h.start()
    with pytest.raises(PreviewError):
        await h.manager.renew(created.preview_id, second.lease_token)
    await h.close()


@pytest.mark.parametrize("expiry,reason", [(31, "lease_expired"), (901, "lifetime_expired")])
async def test_no_renewal_revives_expiry(workspace, expiry, reason):
    h = await harness(workspace)
    created = await h.start()
    h.clock.now += expiry
    with pytest.raises(PreviewError):
        await h.manager.renew(created.preview_id, created.lease_token)
    assert h.sink.records[-1].reason == reason and h.processes[0].dead
    await h.close()


async def test_renew_clamps_absolute_without_heartbeat_transition(workspace):
    h = await harness(workspace)
    h.manager.profile = replace(profile(), lifetime_s=40)
    created = await h.start()
    h.clock.now += 20
    await h.manager.renew(created.preview_id, created.lease_token)
    assert h.manager._deadline == h.manager._absolute == 140
    assert h.processes[0].sent == [b'{"renew":140.0}\n']
    assert len(h.sink.records) == 2
    await h.close()


@pytest.mark.parametrize(
    "code,reason",
    [
        (74, "memory_limit"),
        (73, "launch_failed"),
        (70, "launch_failed"),
        (72, "launch_failed"),
        (-9, "app_exited"),
        (0, "app_exited"),
    ],
)
async def test_only_private_allocation_exit_maps_memory(workspace, code, reason):
    h = await harness(workspace)
    created = await h.start()
    h.processes[0].exit_code = code
    h.processes[0].live = False
    assert (await h.manager.get(created.preview_id)).reason == reason
    if code == 73:
        with pytest.raises(PreviewError, match="disabled"):
            await h.start()
    await h.close()


async def test_two_concurrent_starts_cannot_evict_each_other(workspace):
    h = await harness(workspace)
    results = await asyncio.gather(h.start(), h.start(), return_exceptions=True)
    assert sum(isinstance(result, PreviewError) for result in results) == 1
    assert len(h.processes) == 1 and h.processes[0].live
    await h.close()


def test_status_reason_shape_is_closed():
    with pytest.raises(ValueError):
        PreviewStatusRecord("p", "r", "a", "c", 1, "head", "stopped")
    assert not profile().production_open


async def test_readiness_failure_requires_verified_cleanup_before_reuse(workspace):
    h = await harness(workspace)
    factory = h.manager.process_factory

    def failing(registry, port):
        process = factory(registry, port)
        assert isinstance(process, FakeProcess)
        process.ready_result = False
        return process

    h.manager.process_factory = failing
    with pytest.raises(PreviewError, match="did not become ready"):
        await h.start()
    assert h.processes[0].dead and not h.manager.has_owner
    assert h.sink.records[-1].state == "failed"
    assert h.sink.records[-1].reason == "readiness_timeout"
    assert h.manager.registry.read() is None
    await h.close()


async def test_awaited_starting_sink_and_memory_recheck_precede_execution(workspace):
    h = await harness(workspace)
    committed, release = asyncio.Event(), asyncio.Event()

    async def sink(record):
        await h.sink(record)
        if record.state == "starting":
            committed.set()
            await release.wait()

    h.manager.set_status_sink(sink)
    task = asyncio.create_task(h.start())
    await committed.wait()
    assert h.processes == []
    h.manager.memory_hold = lambda: "host memory hold"
    release.set()
    with pytest.raises(PreviewError, match="memory"):
        await task
    assert h.processes == [] and not h.manager.has_owner
    await h.close()


async def test_profile_disable_drains_then_refuses_execution(workspace):
    h = await harness(workspace)
    await h.start()
    await h.manager.disable_profile()
    assert h.sink.records[-1].reason == "profile_disabled" and h.processes[0].dead
    with pytest.raises(PreviewError, match="disabled"):
        await h.start()
    assert len(h.processes) == 1
    await h.close()


async def test_unavailable_absolute_executables_never_spawn(workspace):
    from pathlib import Path

    h = await harness(workspace)
    values: dict[str, Any] = asdict(profile())
    values["interpreter"] = Path("/missing-prepared-demo/bin/python")
    h.manager.profile = PreviewProfile(**values)
    with pytest.raises(PreviewError, match="disabled"):
        await h.start()
    assert h.processes == [] and h.sink.records == []


async def test_native_windows_never_enables_preview(workspace, monkeypatch):
    from types import SimpleNamespace

    from oversight.workspace import preview

    h = await harness(workspace)
    monkeypatch.setattr(preview, "sys", SimpleNamespace(platform="win32"))
    with pytest.raises(PreviewError, match="isolation C is unavailable"):
        await h.start()
    assert h.processes == []


async def test_invalidated_c_drains_existing_owner_without_open_fallback(workspace):
    h = await harness(workspace)
    created = await h.start()
    workspace._isolation.invalidate("scripted invalidation")
    record = await h.manager.get(created.preview_id)
    assert (
        record.state == "failed" and record.reason == "isolation_unavailable" and record.url is None
    )
    assert h.processes[0].dead
    with pytest.raises(PreviewError) as caught:
        await h.start()
    assert str(caught.value) == ISOLATION_MESSAGE and len(h.processes) == 1
    await h.close()
