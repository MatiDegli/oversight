"""Contract conformance and owned asynchronous startup, using only fake processes."""

import base64
import re
from typing import Any

import pytest

from oversight.workspace import PreviewError

from .preview_fakes import harness

pytestmark = pytest.mark.anyio


async def test_generated_wire_identity_and_immediate_starting(workspace):
    h = await harness(workspace)
    try:
        created = await h.start(wait_ready=False)
        assert re.fullmatch(r"preview-[0-9a-f]{32}", created.preview_id)
        assert re.fullmatch(r"[A-Za-z0-9_-]{43}", created.lease_token)
        assert len(base64.urlsafe_b64decode(created.lease_token + "=")) == 32
        assert (
            base64.urlsafe_b64encode(base64.urlsafe_b64decode(created.lease_token + "="))
            .decode()
            .rstrip("=")
            == created.lease_token
        )
        import uuid

        assert uuid.UUID(created.preview_id.removeprefix("preview-")).version == 4
        assert created.state == "starting"
    finally:
        await h.close()


async def test_unknown_valid_id_is_not_found(workspace):
    h = await harness(workspace)
    with pytest.raises(PreviewError) as caught:
        await h.manager.renew("preview-" + "0" * 32, "A" * 43)
    assert caught.value.code == "not_found"


async def test_absent_profile_has_literal_setup_error(workspace):
    from oversight.workspace import PreviewProfile

    h = await harness(workspace)
    h.manager.profile = PreviewProfile()
    with pytest.raises(PreviewError) as caught:
        await h.start()
    assert caught.value.code == "not_configured"
    assert caught.value.missing == []


async def gated(h, phase):
    """Park launch or readiness and return only after that exact boundary is entered."""
    import asyncio

    available = asyncio.Event()
    factory = h.manager.process_factory

    def process(registry, port):
        instance = factory(registry, port)
        getattr(instance, phase + "_gate").clear()
        available.set()
        return instance

    h.manager.process_factory = process
    async with asyncio.timeout(5):
        created = await h.start(wait_ready=False)
        await available.wait()
        instance = h.processes[-1]
        await getattr(instance, phase + "_started").wait()
    return created, instance


@pytest.mark.parametrize("phase", ["launch", "ready"])
async def test_start_returns_before_launch_and_readiness_and_projects_contracts(workspace, phase):
    from dataclasses import asdict

    from oversight_contracts.previews import PreviewCreated, PreviewDetails, PreviewStatus

    h = await harness(workspace)
    try:
        created, process = await gated(h, phase)
        assert created.state == "starting"
        PreviewCreated.model_validate(
            dict(preview_id=created.preview_id, lease_token=created.lease_token)
        )
        details = asdict(await h.manager.get(created.preview_id))
        assert details.pop("url") is None
        PreviewDetails(status=PreviewStatus.model_validate(details), url=None)
        assert [r.state for r in h.sink.records] == ["starting"]
        getattr(process, phase + "_gate").set()
        await h.sink.wait("ready", created.preview_id)
        details = asdict(await h.manager.get(created.preview_id))
        url = details.pop("url")
        assert url and details["reason"] is None
        PreviewDetails(status=PreviewStatus.model_validate(details), url=url)
        for record in h.sink.records:
            PreviewStatus.model_validate(asdict(record))
        observable = repr(
            (
                h.sink.records,
                h.manager._records,
                details,
                url,
                h.manager.registry.read(),
                process.sent,
                process.diagnostic,
            )
        )
        assert created.lease_token not in observable and created.lease_token not in repr(created)
    finally:
        await h.close()


@pytest.mark.parametrize("phase", ["launch", "ready"])
async def test_starting_renewal_is_deferred_until_handoff_and_clamped(workspace, phase):
    from dataclasses import replace

    h = await harness(workspace)
    h.manager.profile = replace(h.manager.profile, lifetime_s=40)
    try:
        created, process = await gated(h, phase)
        h.clock.now += 20
        before = list(h.sink.records)
        assert (await h.manager.renew(created.preview_id, created.lease_token)).state == "starting"
        assert h.manager._deadline == h.manager._absolute == 140
        assert h.sink.records == before
        assert process.sent == ([] if phase == "launch" else [b'{"renew":140.0}\n'])
        getattr(process, phase + "_gate").set()
        await h.sink.wait("ready", created.preview_id)
        assert process.sent == [b'{"renew":140.0}\n']
        assert process.effective_deadline == process.absolute_deadline == 140
        h.clock.now += 1
        await h.manager.renew(created.preview_id, created.lease_token)
        assert h.manager._absolute == h.manager._deadline == 140
        assert process.sent[-1] == b'{"renew":140.0}\n'
    finally:
        await h.close()


@pytest.mark.parametrize("operation", ["get", "renew"])
async def test_unfinished_launch_is_not_a_confirmed_app_exit(workspace, operation):
    h = await harness(workspace)
    try:
        created, process = await gated(h, "launch")
        assert not await process.confirmed_live()
        before = list(h.sink.records)
        deadline = h.manager._deadline
        h.clock.now += 5
        status = (
            await h.manager.get(created.preview_id)
            if operation == "get"
            else await h.manager.renew(created.preview_id, created.lease_token)
        )
        assert status.state == "starting" and status.reason is None
        assert h.sink.records == before
        assert h.manager.has_owner and h.manager.registry.read() is not None
        assert not process.dead and process.sent == []
        assert h.manager._deadline == deadline + (5 if operation == "renew" else 0)
        process.launch_gate.set()
        await h.sink.wait("ready", created.preview_id)
        assert await process.confirmed_live()
    finally:
        await h.close()


@pytest.mark.parametrize("phase", ["launch", "ready"])
@pytest.mark.parametrize("operation", ["stop", "git", "disable", "shutdown"])
async def test_drain_at_both_gates_cannot_resurrect_or_affect_replacement(
    workspace, phase, operation
):
    import asyncio

    h = await harness(workspace)
    other = await workspace.create_worktree("agent-2")
    try:
        created, process = await gated(h, phase)
        owner, watcher = h.manager.barrier.owner, h.manager._watcher
        assert watcher is not None
        assert owner is not None and owner.launch_task is not None
        process.stop_gate.clear()
        if operation == "stop":
            work = h.manager.stop(created.preview_id, created.lease_token)
        elif operation == "git":
            work = other.head()
        elif operation == "disable":
            work = h.manager.disable_profile()
        else:
            h.manager.close_admission()
            work = h.manager.drain("shutdown")
        waiting = asyncio.create_task(work)
        async with asyncio.timeout(5):
            await process.stop_started.wait()
        assert h.manager.has_owner and h.manager.registry.read() is not None
        with pytest.raises(PreviewError):
            await h.manager.renew(created.preview_id, created.lease_token)
        assert not waiting.done() and owner.launch_task.done()
        process.stop_gate.set()
        async with asyncio.timeout(5):
            await waiting
            if h.manager.barrier.finalizer is not None:
                await h.manager.barrier.finalizer
            await watcher
        assert process.dead and h.manager.registry.read() is None
        assert not h.manager.has_owner
        assert [r.state for r in h.sink.records] == ["starting", "stopping", "stopped"]
        if phase == "launch":
            assert "execute" not in process.order
        # Releasing a stale boundary cannot publish ready or touch a replacement.
        getattr(process, phase + "_gate").set()
        if operation in {"stop", "git"}:
            h.manager.process_factory = lambda registry, port: type(process)(registry, port)
            replacement = await h.start()
            records = list(h.sink.records)
            assert (
                await h.manager.stop(created.preview_id, created.lease_token)
            ).state == "stopped"
            assert h.manager._record is not None
            assert h.manager._record.preview_id == replacement.preview_id
            assert h.sink.records == records
        assert owner.launch_task.done() and watcher.done()
    finally:
        await h.close()


@pytest.mark.parametrize("phase", ["launch", "ready"])
async def test_starting_occupies_capacity_before_preflight_git(workspace, monkeypatch, phase):
    h = await harness(workspace)
    try:
        created, process = await gated(h, phase)

        async def forbidden(*args, **kwargs):
            pytest.fail("occupied admission attempted Git")

        with monkeypatch.context() as patch:
            patch.setattr(workspace, "git", forbidden)
            with pytest.raises(PreviewError) as caught:
                await h.start(wait_ready=False)
            assert caught.value.code == "wrong_state"
        assert h.manager._record is not None
        assert process.live and h.manager._record.preview_id == created.preview_id
    finally:
        await h.close()


@pytest.mark.parametrize("phase", ["launch", "ready"])
async def test_one_startup_deadline_and_cleanup_before_capacity_release(workspace, phase):
    import asyncio
    from dataclasses import replace

    h = await harness(workspace)
    h.manager.profile = replace(h.manager.profile, readiness_s=0.1)
    try:
        created, process = await gated(h, phase)
        process.stop_gate.clear()
        async with asyncio.timeout(5):
            await process.stop_started.wait()
        assert h.manager.has_owner and h.manager.registry.read() is not None
        assert h.sink.records[-1].state == "stopping"
        process.stop_gate.set()
        await h.sink.wait("failed", created.preview_id)
        assert h.manager._watcher is not None
        await h.manager._watcher
        assert h.sink.records[-1].reason == "readiness_timeout"
        assert not h.manager.has_owner and process.dead and h.manager.registry.read() is None
    finally:
        await h.close()


async def test_post_admission_launch_failure_is_observed_and_sanitized(workspace):
    h = await harness(workspace)
    created, process = await gated(h, "launch")
    process.launch_failure = True
    process.launch_gate.set()
    await h.sink.wait("failed", created.preview_id)
    assert h.manager._watcher is not None
    await h.manager._watcher
    assert h.sink.records[-1].reason == "launch_failed"
    assert "raw child" not in repr(h.sink.records)
    assert process.dead and not h.manager.has_owner and h.manager.registry.read() is None
    await h.close()


@pytest.mark.parametrize("phase", ["launch", "ready"])
@pytest.mark.parametrize("boundary", ["lease", "absolute"])
async def test_exact_starting_expiry_never_renews(workspace, phase, boundary):
    h = await harness(workspace)
    try:
        created, process = await gated(h, phase)
        h.clock.now = h.manager._deadline if boundary == "lease" else h.manager._absolute
        with pytest.raises(PreviewError) as caught:
            await h.manager.renew(created.preview_id, created.lease_token)
        assert caught.value.code == "wrong_state" and process.dead
        assert h.sink.records[-1].reason == boundary.replace("absolute", "lifetime") + "_expired"
    finally:
        await h.close()


@pytest.mark.parametrize("phase", ["launch", "ready"])
async def test_failed_starting_drain_retains_quarantine_and_all_git_refusal(workspace, phase):
    from oversight.workspace import IntegrityError

    h = await harness(workspace)
    created, process = await gated(h, phase)
    process.stop_failure = True
    with pytest.raises(PreviewError):
        await h.manager.stop(created.preview_id, created.lease_token)
    assert h.manager.has_owner and h.manager.registry.read() is not None
    assert h.sink.records[-1].reason == "cleanup_unverified"
    assert workspace.runtime_owned and workspace._cleanup_unverified
    with pytest.raises(IntegrityError):
        await h.tree.head()
    await h.close()


async def test_shutdown_and_disabled_profile_refusals_are_distinct(workspace):
    from .test_preview import assert_wire_error

    h = await harness(workspace)
    await h.manager.disable_profile()
    with pytest.raises(PreviewError) as caught:
        await h.start(wait_ready=False)
    assert_wire_error(caught.value, "not_configured")
    h.manager.close_admission()
    with pytest.raises(PreviewError) as caught:
        await h.start(wait_ready=False)
    assert_wire_error(caught.value, "wrong_state")
    assert h.processes == []


@pytest.mark.parametrize(
    "field,value",
    [("run_id", 2), ("agent_id", None), ("revision", True), ("revision", 0), ("head_commit", "")],
)
async def test_malformed_start_body_refuses_without_effect(workspace, field, value):
    from .test_preview import assert_wire_error

    h = await harness(workspace)
    request: dict[str, Any] = dict(
        run_id="run-20261103-000000", agent_id="agent-1", revision=1, head_commit=h.head
    )
    request[field] = value
    with pytest.raises(PreviewError) as caught:
        await h.manager.start(**request)
    assert_wire_error(caught.value, "invalid_request")
    assert h.processes == [] and h.sink.records == []


async def test_failed_terminal_cannot_renew_or_drain_replacement(workspace):
    h = await harness(workspace)
    created, process = await gated(h, "launch")
    process.launch_failure = True
    process.launch_gate.set()
    await h.sink.wait("failed", created.preview_id)
    assert h.manager._watcher is not None
    await h.manager._watcher
    h.manager.process_factory = lambda registry, port: type(process)(registry, port)
    replacement = await h.start()
    records = list(h.sink.records)
    deadline, absolute = h.manager._deadline, h.manager._absolute
    with pytest.raises(PreviewError):
        await h.manager.renew(created.preview_id, created.lease_token)
    assert (await h.manager.stop(created.preview_id, created.lease_token)).state == "failed"
    assert h.manager._record is not None
    assert h.manager._record.preview_id == replacement.preview_id
    assert (h.manager._deadline, h.manager._absolute) == (deadline, absolute)
    assert h.sink.records == records
    await h.close()


@pytest.mark.parametrize("operation", ["get", "renew"])
async def test_stale_liveness_completion_cannot_drain_replacement(workspace, operation):
    import asyncio

    h = await harness(workspace)
    created = await h.start()
    process = h.processes[0]
    entered, release = asyncio.Event(), asyncio.Event()

    async def old_liveness():
        entered.set()
        await release.wait()
        return False

    process.confirmed_live = old_liveness
    call = (
        h.manager.get(created.preview_id)
        if operation == "get"
        else h.manager.renew(created.preview_id, created.lease_token)
    )
    waiting = asyncio.create_task(call)
    async with asyncio.timeout(5):
        await entered.wait()
    await h.manager.stop(created.preview_id, created.lease_token)
    replacement = await h.start()
    records = list(h.sink.records)
    deadlines = (h.manager._deadline, h.manager._absolute)
    release.set()
    async with asyncio.timeout(5):
        if operation == "renew":
            with pytest.raises(PreviewError):
                await waiting
        else:
            assert (await waiting).state == "stopped"
    assert h.manager._record is not None and h.manager._record.preview_id == replacement.preview_id
    assert h.sink.records == records
    assert (h.manager._deadline, h.manager._absolute) == deadlines
    assert h.processes[-1].live
    await h.close()


async def test_readiness_budget_includes_time_already_spent_launching(workspace, monkeypatch):
    from types import SimpleNamespace

    from oversight.workspace import preview_lifecycle

    h = await harness(workspace)
    monkeypatch.setattr(preview_lifecycle, "time", SimpleNamespace(monotonic=h.clock))
    created, process = await gated(h, "launch")
    h.clock.now += 7
    process.launch_gate.set()
    await h.sink.wait("ready", created.preview_id)
    assert process.ready_budget == h.manager.profile.readiness_s - 7
    await h.close()


@pytest.mark.parametrize("interrupt", [False, True])
async def test_queued_old_finalizer_cannot_drain_a_replacement(workspace, interrupt):
    import asyncio

    h = await harness(workspace)
    created = await h.start()
    owner = h.manager.barrier.owner
    assert owner is not None
    entered = asyncio.Event()

    async def finalize_old():
        entered.set()
        await h.manager.barrier.finish_pending(owner)

    async with workspace.locks(main=True):
        stale = asyncio.create_task(finalize_old())
        async with asyncio.timeout(5):
            await entered.wait()
        await h.manager.stop(created.preview_id, created.lease_token)
        replacement = await h.start(wait_ready=False)
        if interrupt:
            await h.tree.head()
    async with asyncio.timeout(5):
        await stale
        await h.sink.wait("stopped" if interrupt else "ready", replacement.preview_id)
        if interrupt:
            assert h.manager.barrier.finalizer is not None
            await h.manager.barrier.finalizer
            assert not h.manager.has_owner
    states = [r.state for r in h.sink.records if r.preview_id == replacement.preview_id]
    assert states == (["starting", "stopping", "stopped"] if interrupt else ["starting", "ready"])
    await h.close()


async def test_renewal_during_queued_drain_does_not_extend_authenticated_deadline(workspace):
    import asyncio

    h = await harness(workspace)
    created = await h.start()
    entered = asyncio.Event()

    async def stop():
        entered.set()
        await h.manager.stop(created.preview_id, created.lease_token)

    async with workspace.locks(main=True):
        stopping = asyncio.create_task(stop())
        async with asyncio.timeout(5):
            await entered.wait()
        records, deadline = list(h.sink.records), h.manager._deadline
        h.clock.now += 20
        with pytest.raises(PreviewError):
            await h.manager.renew(created.preview_id, created.lease_token)
        assert h.manager._deadline == deadline and h.sink.records == records
        assert h.processes[0].sent == []
    async with asyncio.timeout(5):
        await stopping
    await h.close()
