"""Facade, zero-launch refusals, ownership and deterministic lifetime/memory admission."""

import asyncio
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest

from oversight import workspace as facade
from oversight.workspace import PreviewError, PreviewProfile
from oversight.workspace.preview_types import ISOLATION_MESSAGE, PreviewStatusRecord

from .preview_fakes import FakeProcess, harness, profile

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("ending", ["closed", "mutation", "lease_expired"])
async def test_authenticated_stop_retries_return_final_record_without_drain(
    workspace, monkeypatch, ending
):
    from unittest.mock import AsyncMock

    h = await harness(workspace)
    created = await h.start()
    if ending == "closed":
        final = await h.manager.stop(created.preview_id, created.lease_token)
    else:
        if ending == "mutation":
            await h.tree.head()
            await h.manager.barrier.finish_pending()
        else:
            h.clock.now += h.manager.profile.lease_s
            await h.manager.get(created.preview_id)
        final = h.sink.records[-1]
    assert final.state == "stopped" and final.reason == ending
    assert await h.manager.stop(created.preview_id, created.lease_token) is final
    assert h.manager._token is None
    assert created.lease_token not in repr(h.manager._terminal_tokens)
    replacement = await h.start()
    records = list(h.sink.records)
    with monkeypatch.context() as patch:
        drain = AsyncMock(side_effect=AssertionError("terminal retry drained a new owner"))
        patch.setattr(h.manager, "drain", drain)
        assert await h.manager.stop(created.preview_id, created.lease_token) is final
        with pytest.raises(PreviewError, match="expired"):
            await h.manager.renew(created.preview_id, created.lease_token)
        for token in ("A" * 43, replacement.lease_token):
            with pytest.raises(PreviewError) as caught:
                await h.manager.stop(created.preview_id, token)
            assert caught.value.code == "forbidden"
        drain.assert_not_awaited()
    assert h.sink.records == records and not h.processes[-1].dead
    await h.close()


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
    assert len(created.lease_token) == 43 and created.state == "starting"
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
    if fault in profiles:
        assert_wire_error(caught.value, "not_configured")
    assert h.processes == [] and not h.manager.has_owner and h.sink.records == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "run-20261103-000001"),
        ("agent_id", "agent-2"),
        ("revision", 2),
        ("head_commit", "f" * 40),
    ],
)
async def test_stale_or_wrong_identity_never_launches(workspace, field, value):
    h = await harness(workspace)
    request: dict[str, Any] = dict(
        run_id="run-20261103-000000", agent_id="agent-1", revision=1, head_commit=h.head
    )
    request[field] = value
    with pytest.raises(PreviewError) as caught:
        await h.manager.start(**request)
    assert_wire_error(caught.value, "wrong_state")
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


@pytest.mark.parametrize("expiry,reason", [(601, "lease_expired"), (901, "lifetime_expired")])
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
    assert not PreviewProfile().production_open


@pytest.mark.parametrize("phase", ["admission", "starting", "handoff"])
async def test_switch_checks_admission_and_execution(workspace, phase):
    h = await harness(workspace)
    release, entered = asyncio.Event(), asyncio.Event()
    factory = h.manager.process_factory

    async def sink(record):
        await h.sink(record)
        if record.state == "starting" and phase == "starting":
            entered.set()
            await release.wait()

    def gated(registry, port):
        process = factory(registry, port)
        assert isinstance(process, FakeProcess)
        process.launch_gate = release
        entered.set()
        return process

    h.manager.set_status_sink(sink)
    if phase == "handoff":
        h.manager.process_factory = gated
    try:
        if phase == "admission":
            h.manager.profile = replace(h.manager.profile, enabled=False)
            with pytest.raises(PreviewError) as caught:
                await h.start()
            assert str(caught.value) == "Live preview profile is disabled."
            assert_wire_error(caught.value, "not_configured")
            assert h.processes == [] and h.sink.records == []
        else:
            async with asyncio.timeout(5):
                starting = asyncio.create_task(h.start(wait_ready=False))
                await entered.wait()
                if phase == "handoff":
                    await h.processes[0].launch_started.wait()
                    assert h.manager.lifecycle.profile.enabled
                owner = h.manager.barrier.owner
                assert owner is not None
                h.manager.profile = replace(h.manager.profile, enabled=False)
                release.set()
                created = await starting
                await h.sink.wait("failed", created.preview_id)
                assert h.manager._watcher is not None
                await h.manager._watcher
                assert owner.launch_task is not None
                error = owner.launch_task.exception()
                assert isinstance(error, PreviewError)
                assert str(error) == "Live preview profile is disabled."
                assert_wire_error(error, "not_configured")
            assert h.sink.records[-1].reason == "launch_failed"
            assert all("execute" not in process.order for process in h.processes)
            assert all(process.dead for process in h.processes)
            assert h.manager.registry.read() is None
            if phase == "starting":
                assert h.processes == []
        assert not h.manager.has_owner
    finally:
        release.set()
        await h.close()


@pytest.mark.parametrize(
    "fault,message",
    [
        ("source", "preview profile is disabled"),
        ("adapter", "preview adapter must be the trusted absolute adapter"),
        ("relative_adapter", "preview adapter must be the trusted absolute adapter"),
        ("interpreter", "preview interpreter must be absolute"),
        ("missing", "preview executables are unavailable"),
        ("backend_port", "invalid preview port pool"),
        ("vite_port", "invalid preview port pool"),
    ],
)
async def test_enabled_does_not_grant_source_or_adapter_trust(workspace, fault, message):
    from oversight.settings import Settings

    h = await harness(workspace)
    missing: dict[str, Any] = asdict(profile())
    missing["interpreter"] = Path("/missing-preview/bin/python")
    profiles = {
        "adapter": replace(profile(), adapter=Path("/untrusted/adapter.py")),
        "relative_adapter": replace(profile(), adapter=Path("preview_adapter.py")),
        "interpreter": replace(profile(), interpreter=Path("python")),
        "missing": PreviewProfile(**missing),
    }
    if fault == "source":
        selected = Settings(workspace_source="boltons-d31d821", preview_profile=profile())
        h.manager.profile = selected.preview_profile
    elif fault in {"backend_port", "vite_port"}:
        port = h.manager.profile.ports[0]
        selected = Settings(
            port=port if fault == "backend_port" else 8710,
            vite_port=port if fault == "vite_port" else None,
            preview_profile=profile(),
        )
        h.manager.profile = selected.preview_profile
    else:
        h.manager.profile = profiles[fault]
    assert h.manager.profile.production_open
    with pytest.raises(ValueError, match=f"^{message}$"):
        h.manager.profile.validate()
    with pytest.raises(PreviewError) as caught:
        await h.start()
    assert str(caught.value) == "Live preview profile is disabled."
    assert_wire_error(caught.value, "not_configured")
    assert h.processes == [] and h.sink.records == [] and not h.manager.has_owner


async def test_readiness_failure_requires_verified_cleanup_before_reuse(workspace):
    h = await harness(workspace)
    factory = h.manager.process_factory

    def failing(registry, port):
        process = factory(registry, port)
        assert isinstance(process, FakeProcess)
        process.ready_result = False
        return process

    h.manager.process_factory = failing
    created = await h.start(wait_ready=False)
    await h.sink.wait("failed", created.preview_id)
    assert h.manager._watcher is not None
    await h.manager._watcher
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
    task = asyncio.create_task(h.start(wait_ready=False))
    await committed.wait()
    assert h.processes == []
    h.manager.memory_hold = lambda: "host memory hold"
    release.set()
    created = await task
    await h.sink.wait("failed", created.preview_id)
    assert h.manager._watcher is not None
    await h.manager._watcher
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


@pytest.mark.parametrize(
    "code,reported,expected",
    [
        (0, "app_exited", "app_exited"),
        (-9, "app_exited", "app_exited"),
        (74, "app_exited", "memory_limit"),
        (73, "app_exited", "launch_failed"),
        (None, "lease_expired", "lease_expired"),
        (None, "lifetime_expired", "lifetime_expired"),
        (None, "shutdown", "shutdown"),
    ],
)
async def test_exit_before_readiness_preserves_confirmed_supervisor_reason(
    workspace, code, reported, expected
):
    h = await harness(workspace)
    factory = h.manager.process_factory

    def exited(registry, port):
        process = factory(registry, port)
        assert isinstance(process, FakeProcess)
        process.ready_result = False
        process.live = False
        process.exit_code = code
        process.reason = reported
        return process

    h.manager.process_factory = exited
    created = await h.start(wait_ready=False)
    await h.sink.wait("stopped" if code is None else "failed", created.preview_id)
    assert h.manager._watcher is not None
    await h.manager._watcher
    assert h.sink.records[-1].reason == expected
    assert h.processes[0].dead and not h.manager.has_owner
    if code == 73:
        with pytest.raises(PreviewError, match="disabled"):
            await h.start()
    await h.close()


def assert_wire_error(error, code):
    import json

    from oversight.api.errors import error_response
    from oversight_contracts.commands import ERROR_STATUS, ErrorResponse

    assert error.code == code
    assert error.missing == ([] if code == "not_configured" else None)
    response = error_response(
        error.code, str(error), missing=[] if code == "not_configured" else None
    )
    wire = json.loads(bytes(response.body))
    assert response.status_code == ERROR_STATUS[code]
    ErrorResponse.model_validate(wire)
    assert ("missing" in wire["error"]) == (code == "not_configured")
    return str(wire)


@pytest.mark.parametrize("operation", ["get", "renew", "stop"])
async def test_unknown_and_malformed_ids_have_distinct_safe_errors(workspace, operation):
    h = await harness(workspace)
    method = getattr(h.manager, operation)
    for preview_id, code in [
        ("preview-" + "0" * 32, "not_found"),
        *[
            (value, "invalid_request")
            for value in (
                "0" * 32,
                "preview-" + "0" * 33,
                "preview-" + "0" * 32 + "\n",
                "preview-" + "0" * 10000,
                7,
                None,
            )
        ],
    ]:
        args = (preview_id,) if operation == "get" else (preview_id, "A" * 43)
        with pytest.raises(PreviewError) as caught:
            await method(*args)
        assert_wire_error(caught.value, code)
    assert h.processes == [] and h.sink.records == []


@pytest.mark.parametrize("operation", ["renew", "stop"])
async def test_tokens_match_literal_regex_and_invalid_inputs_have_no_effect(workspace, operation):
    h = await harness(workspace)
    created = await h.start()
    before = list(h.sink.records)
    deadline = h.manager._deadline
    for token, code in [
        ("A" * 43, "forbidden"),
        *[(value, "invalid_request") for value in ("bad", "a" * 64, "A" * 43 + "\n", None, 7)],
    ]:
        with pytest.raises(PreviewError) as caught:
            await getattr(h.manager, operation)(created.preview_id, token)
        assert created.lease_token not in assert_wire_error(caught.value, code)
    assert h.sink.records == before and h.manager._deadline == deadline
    assert h.processes[0].live and h.processes[0].sent == []
    await h.close()


async def test_generation_requests_32_bytes_and_accepts_noncanonical_regex_token(
    workspace, monkeypatch
):
    import secrets

    h = await harness(workspace)
    requested = []

    def token_urlsafe(size):
        requested.append(size)
        return "_" * 43  # Contract-valid even though not a canonical encoding of 32 bytes.

    monkeypatch.setattr(secrets, "token_urlsafe", token_urlsafe)
    created = await h.start()
    assert requested == [32]
    await h.manager.renew(created.preview_id, "_" * 43)
    assert (await h.manager.stop(created.preview_id, "_" * 43)).state == "stopped"
    await h.close()


async def test_stop_retry_queued_before_termination_cannot_drain_replacement(workspace):
    h = await harness(workspace)
    created = await h.start()
    entered = asyncio.Event()

    async def retry():
        entered.set()
        return await h.manager.stop(created.preview_id, created.lease_token)

    async with workspace.locks(main=True):
        queued = asyncio.create_task(retry())
        async with asyncio.timeout(5):
            await entered.wait()
        await h.manager.stop(created.preview_id, created.lease_token)
        replacement = await h.start(wait_ready=False)
    async with asyncio.timeout(5):
        assert (await queued).state == "stopped"
    assert (await h.manager.get(replacement.preview_id)).state in {"starting", "ready"}
    await h.sink.wait("ready", replacement.preview_id)
    assert h.processes[-1].live
    await h.close()
