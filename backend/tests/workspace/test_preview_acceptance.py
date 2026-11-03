"""Opt-in kernel acceptance: inherited socket, network effect, descendants and crash teardown."""

import asyncio
import os
import signal
import socket

import httpx
import pytest

from oversight.workspace import PreviewError

from .preview_acceptance_support import (
    attack,
    dead,
    descendant_identities,
    listener_closed,
    real_preview,
    report_file,
)
from .preview_acceptance_support import (
    no_kernel_startup_probe as no_kernel_startup_probe,
)

# isort: split
from tools.preview_acceptance_checks import free_port, pin_namespace, released, server, settings
from tools.preview_scenario import document

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        os.environ.get("OVERSIGHT_ISOLATION_KERNEL_TESTS") != "1",
        reason="optional real-C preview acceptance",
    ),
]


def external_address():
    # UDP connect selects the local interface without sending a datagram to the reserved address.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        connection.connect(("192.0.2.1", 9))
        return connection.getsockname()[0]


async def test_namespace_pin_retains_identity_through_death_and_closes_descriptor(tmp_path):
    async with real_preview(tmp_path) as h:
        receipt, data = await h.start()
        with pin_namespace(data) as descriptor:
            inode = os.fstat(descriptor).st_ino
            assert data["namespaces"]["pid"] == f"pid:[{inode}]"
            await h.manager.stop(receipt.preview_id, receipt.lease_token)
            await dead(data)
            assert os.fstat(descriptor).st_ino == inode
            assert os.readlink(f"/proc/self/fd/{descriptor}") == data["namespaces"]["pid"]
        with pytest.raises(OSError):
            os.fstat(descriptor)


async def test_inherited_http_forms_network_refusal_and_setsid_death(tmp_path, capsys):
    accepted = []

    async def connected(reader, writer):
        accepted.append(True)
        writer.close()
        await writer.wait_closed()

    host = await asyncio.start_server(connected, "127.0.0.1", 0)
    outside = await asyncio.start_server(connected, "0.0.0.0", 0)
    config = settings(tmp_path / "backend" / ".runtime", free_port())
    try:
        async with server(config) as running:
            targets = (
                ("127.0.0.1", config.port),
                ("127.0.0.1", host.sockets[0].getsockname()[1]),
                (external_address(), outside.sockets[0].getsockname()[1]),
            )
            for target in targets:
                _, writer = await asyncio.wait_for(asyncio.open_connection(*target), 2)
                writer.close()
                await writer.wait_closed()
            report = tmp_path / "report.json"
            async with real_preview(tmp_path, code=attack(report, targets)) as h:
                receipt, data = await h.start()
                details = await h.manager.get(receipt.preview_id)
                assert details.url is not None
                evidence = await report_file(report)
                assert evidence["refused"] == [True, True, True]
                assert evidence["namespace"] == data["namespaces"]["pid"]
                descendants = descendant_identities(data)
                assert len(descendants) >= 2
                async with httpx.AsyncClient(
                    timeout=5, trust_env=False, follow_redirects=False
                ) as client:
                    initial = document(await client.get(details.url), "http://127.0.0.1:8710")
                    assert initial.total == 1500
                    post = document(
                        await client.post(
                            str(details.url).rstrip("/") + "/cart",
                            data={
                                "quantity_1": "2",
                                "quantity_2": "1",
                                "coupon": "SPRING10",
                                "nonce": initial.nonce,
                            },
                            headers={"Origin": "null"},
                        ),
                        "http://127.0.0.1:8710",
                    )
                    assert post.total == 2250 and post.nonce != initial.nonce
                result = await h.manager.stop(receipt.preview_id, receipt.lease_token)
                assert result.state == "stopped"
                await dead(data, descendants=descendants)
                await listener_closed(data["port"])
                assert not h.manager.has_owner and h.manager.registry.read() is None
                assert not h.workspace.integrity.main_suspect
            assert len(accepted) == 2  # Only the two outside controls, never the application.
        await released(running.app.state.live.workspace, config.preview_profile.ports)
    finally:
        for listener in (host, outside):
            listener.close()
            await listener.wait_closed()
    assert "PRIVATE_PREVIEW_OUTPUT_MUST_NOT_ESCAPE" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "trigger,reason",
    [("lease", "lease_expired"), ("lifetime", "lifetime_expired"), ("eof", "shutdown")],
)
async def test_real_deadline_or_backend_control_eof_verifies_namespace_and_listener(
    tmp_path, trigger, reason
):
    report = tmp_path / "report.json"
    async with real_preview(
        tmp_path,
        code=attack(report, ()),
        lease=30 if trigger == "eof" else 2,
        lifetime=90 if trigger == "eof" else 3,
    ) as h:
        receipt, data = await h.start()
        descendants = descendant_identities(data)
        owner = h.manager.barrier.owner
        assert owner is not None and owner.process is not None
        if trigger == "eof":
            os.close(owner.process.control)
            owner.process.control = -1
        elif trigger == "lifetime":
            async with asyncio.timeout(8):
                while not any(r.state == "stopped" for r in h.sink.records):
                    try:
                        await h.manager.renew(receipt.preview_id, receipt.lease_token)
                    except PreviewError:
                        break
                    await asyncio.sleep(0.2)
        terminal = await h.sink.wait("stopped", receipt.preview_id)
        assert terminal.reason == reason
        await dead(data, descendants=descendants)
        await listener_closed(data["port"])
        async with asyncio.timeout(5):
            while h.manager.has_owner:  # noqa: ASYNC110 - owner task finalization
                await asyncio.sleep(0.01)
        assert h.manager.registry.read() is None
        repeated = await h.manager.stop(receipt.preview_id, receipt.lease_token)
        assert repeated == terminal


async def test_supervisor_sigkill_parent_death_never_claims_verified_stop(tmp_path):
    report = tmp_path / "report.json"
    async with real_preview(tmp_path, code=attack(report, ())) as h:
        receipt, data = await h.start()
        descendants = descendant_identities(data)
        # Authenticated registered identity, never a name/port lookup.
        os.kill(data["supervisor"]["pid"], signal.SIGKILL)
        await dead(data, descendants=descendants)
        await listener_closed(data["port"])
        with pytest.raises(PreviewError):
            await h.manager.stop(receipt.preview_id, receipt.lease_token)
        assert h.workspace.integrity.main_suspect and h.manager.has_owner
        assert h.manager.registry.read() is not None
        assert h.sink.records[-1].reason == "cleanup_unverified"
        assert not any(r.state == "stopped" for r in h.sink.records)
        h.manager.close_admission()
        if h.manager._watcher:
            h.manager._watcher.cancel()
            await asyncio.gather(h.manager._watcher, return_exceptions=True)
        # Retained ownership is deliberate; a second runtime facade must be excluded.
        from oversight.workspace import Workspace, WorkspaceBusy

        replacement = Workspace(h.workspace.runtime_root, h.workspace._template, pytest_pin="9.1.1")
        with pytest.raises(WorkspaceBusy):
            await replacement.acquire_runtime()


@pytest.mark.parametrize("lifetime", [False, True])
async def test_actual_routes_renew_expire_and_authenticate_replacement(
    tmp_path, monkeypatch, lifetime
):
    from tools.preview_acceptance_checks import candidate

    from oversight_contracts.previews import PreviewDetails

    config = settings(tmp_path / "routes" / ".runtime", free_port())
    from dataclasses import replace

    config = replace(
        config,
        preview_profile=replace(config.preview_profile, heartbeat_s=0.1, lease_s=2, lifetime_s=4),
    )
    async with server(config) as running:
        target = await candidate(running)
        response = await running.client.post("/previews", json=target.opening())
        assert response.status_code == 202
        receipt = response.json()
        path = f"/previews/{receipt['preview_id']}"
        async with asyncio.timeout(15):
            while True:
                details = PreviewDetails.model_validate((await running.client.get(path)).json())
                if details.status.state == "ready":
                    break
                assert details.status.state == "starting"
                await asyncio.sleep(0.02)
        assert (await running.client.post(path + "/lease", json={})).status_code == 422
        assert (
            await running.client.post(path + "/stop", json={"lease_token": "B" * 43})
        ).status_code == 403
        if lifetime:
            async with asyncio.timeout(12):
                while details.status.state in {"starting", "ready"}:
                    renewal = await running.client.post(
                        path + "/lease", json={"lease_token": receipt["lease_token"]}
                    )
                    assert renewal.status_code in {200, 409}
                    await asyncio.sleep(0.3)
                    details = PreviewDetails.model_validate((await running.client.get(path)).json())
        async with asyncio.timeout(15):
            while details.status.state not in {"stopped", "failed"}:
                await asyncio.sleep(0.05)
                details = PreviewDetails.model_validate((await running.client.get(path)).json())
        assert details.status.state == "stopped" and details.url is None
        assert details.status.reason == ("lifetime_expired" if lifetime else "lease_expired")
        assert (
            await running.client.post(path + "/lease", json={"lease_token": receipt["lease_token"]})
        ).status_code == 409
        for _ in range(2):
            stopped = await running.client.post(
                path + "/stop", json={"lease_token": receipt["lease_token"]}
            )
            assert stopped.status_code == 200 and stopped.json()["state"] == "stopped"
        new = await running.client.post("/previews", json=target.opening())
        assert new.status_code == 202
        next_receipt = new.json()
        assert next_receipt["preview_id"] != receipt["preview_id"]
        assert (
            await running.client.post(
                f"/previews/{next_receipt['preview_id']}/stop",
                json={"lease_token": receipt["lease_token"]},
            )
        ).status_code == 403
        live = running.app.state.live
        close = live.store.close
        committed = []

        async def close_store():
            assert all(w.dead for w in running.witnesses)
            last = [e for e in live.store.committed_events() if e.type == "preview.status"][-1]
            assert last.payload.state == "stopped" and last.payload.reason == "shutdown"
            committed.append(last.seq)
            await close()

        monkeypatch.setattr(live.store, "close", close_store)
    assert committed
    await released(live.workspace, config.preview_profile.ports)


async def test_real_config_filter_restitution_refuses_triggering_git_without_sentinel(
    tmp_path, monkeypatch
):
    from oversight.workspace import IntegrityError, core, run_process

    async with real_preview(tmp_path) as h:
        other = await h.workspace.create_worktree("agent-2")
        config = h.workspace.main.git_identity.common / "config"
        original = config.read_bytes()
        sentinel = tmp_path / "filter-ran"
        attributes = h.workspace.main.git_identity.common / "info/attributes"
        attributes.write_text("shop/pricing.py filter=evil\n")
        evil = original + f'\n[filter "evil"]\n clean = "touch {sentinel}; cat"\n'.encode()
        path = other.root / "shop/pricing.py"
        content = path.read_text()
        config.write_bytes(evil)
        path.write_text(content.replace('"SPRING10": 10', '"SPRING10": 11'))
        async with h.workspace.locks(agent_id="agent-2"):
            await h.workspace.git_ok(other, "status", "--porcelain")
        assert sentinel.exists()  # Effective outside control, not an inert filter string.
        sentinel.unlink()
        config.write_bytes(original)
        receipt, data = await h.start()
        config.write_bytes(evil)
        path.write_text(content.replace('"SPRING10": 10', '"SPRING10": 12'))
        candidate_path = h.workspace.worktree("agent-1").root / "shop/pricing.py"
        candidate_path.write_text(candidate_path.read_text() + "\n")
        calls = []
        original_spawn = run_process

        async def spawn(argv, **kwargs):
            assert not sentinel.exists() and config.read_bytes() == original
            calls.append(argv)
            return await original_spawn(argv, **kwargs)

        monkeypatch.setattr(core, "run_process", spawn)
        async with h.workspace.locks(main=True, agent_id="agent-2"):
            with pytest.raises(IntegrityError, match="restored Git metadata"):
                await h.workspace.git_ok(other, "status", "--porcelain")
            assert calls == [] and not sentinel.exists()
            await dead(data)
            assert config.read_bytes() == original and h.workspace.integrity.rules_suspended
            assert not h.workspace.integrity.main_suspect and not h.workspace._cleanup_unverified
        await h.manager.barrier.finish_pending()
        assert h.sink.records[-1].reason == "worktree_changed"
        assert h.sink.records[-1].preview_id == receipt.preview_id
        async with h.workspace.locks(agent_id="agent-2"):
            assert await other.head()
        assert not sentinel.exists()


@pytest.mark.parametrize("suspend", [False, True])
async def test_backend_sigkill_and_locked_authenticated_recovery_before_orphans_git_reset(
    tmp_path, monkeypatch, suspend
):
    from oversight.settings import REPO_ROOT
    from oversight.workspace import PreviewManager, Workspace, core, run_process
    from oversight.workspace import preview as previews
    from oversight.workspace.isolation import refuse_orphans
    from oversight.workspace.preview_registry import reclaim_registered, registered_identity
    from oversight.workspace.snapshot import finish_snapshot

    from .preview_acceptance_support import preflight, recovery_profile, spawn_backend

    await preflight(tmp_path / "preflight")
    transport, collector, data = await spawn_backend(tmp_path)
    descendants = [registered_identity(raw) for raw in data["descendants"]]
    replacement = None
    try:
        if suspend:
            assert registered_identity(data["supervisor"]).alive()
            os.kill(data["supervisor"]["pid"], signal.SIGSTOP)
        transport.kill()  # Only the child backend whose birth this test owns.
        await asyncio.wait_for(collector.exited, 10)
        assert transport.get_returncode() == -signal.SIGKILL
        if not suspend:
            await dead(data, descendants=descendants)
            await listener_closed(data["port"])
        else:
            assert registered_identity(data["namespace"]).alive()
        replacement = Workspace(tmp_path / ".runtime", REPO_ROOT / "demo-repo", pytest_pin="9.1.1")
        profile = recovery_profile(data)
        manager = PreviewManager(replacement, profile)
        original_finish, original_orphans, original_spawn = (
            finish_snapshot,
            refuse_orphans,
            run_process,
        )
        order = []

        def finish(snapshot, state):
            assert replacement.runtime_owned
            assert all(not identity.alive() for identity in descendants)
            order.append("death")
            original_finish(snapshot, state)
            order.append("snapshot")

        def orphans(*args, **kwargs):
            assert order[:2] == ["death", "snapshot"] and manager.registry.read() is None
            order.append("orphans")
            return original_orphans(*args, **kwargs)

        async def spawn(argv, **kwargs):
            assert "snapshot" in order
            order.append("git")
            return await original_spawn(argv, **kwargs)

        monkeypatch.setattr(previews, "finish_snapshot", finish)
        monkeypatch.setattr(core, "refuse_orphans", orphans)
        monkeypatch.setattr(core, "run_process", spawn)
        await replacement.acquire_runtime()
        assert order[:2] == ["death", "snapshot"] and "orphans" in order
        await replacement.reset()
        assert manager.registry.read() is None
        await dead(data, descendants=descendants)
        await listener_closed(data["port"])
    finally:
        if transport.get_returncode() is None:
            transport.kill()
            await asyncio.wait_for(collector.exited, 10)
        transport.close()
        await reclaim_registered(data)
        if replacement:
            replacement.release_runtime()


async def test_ambiguous_real_startup_retains_lock_and_never_signals_before_validation(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    from oversight.settings import REPO_ROOT
    from oversight.workspace import PreviewManager, Workspace, WorkspaceBusy, core
    from oversight.workspace.preview_registry import reclaim_registered, registered_identity

    from .preview_acceptance_support import preflight, recovery_profile, spawn_backend

    await preflight(tmp_path / "preflight")
    transport, collector, data = await spawn_backend(tmp_path)
    try:
        os.kill(data["supervisor"]["pid"], signal.SIGSTOP)
        transport.kill()
        await asyncio.wait_for(collector.exited, 10)
        replacement = Workspace(tmp_path / ".runtime", REPO_ROOT / "demo-repo", pytest_pin="9.1.1")
        profile = recovery_profile(data)
        manager = PreviewManager(
            replacement, replace(profile, memory_limit_bytes=profile.memory_limit_bytes + 1)
        )
        before = manager.registry.path.read_bytes()

        def forbidden(*args, **kwargs):
            pytest.fail("ambiguous recovery reached orphan/source Git/reset before validation")

        monkeypatch.setattr(core, "refuse_orphans", forbidden)
        monkeypatch.setattr(core, "run_process", forbidden)
        with pytest.raises(PreviewError, match="recovery is unverified"):
            await replacement.acquire_runtime()
        assert replacement.runtime_owned and replacement.integrity.main_suspect
        assert manager.registry.path.read_bytes() == before
        assert registered_identity(data["namespace"]).alive()
        replacement.release_runtime()
        assert replacement.runtime_owned
        third = Workspace(tmp_path / ".runtime", REPO_ROOT / "demo-repo", pytest_pin="9.1.1")
        with pytest.raises(WorkspaceBusy):
            await third.acquire_runtime()
    finally:
        if transport.get_returncode() is None:
            transport.kill()
            await asyncio.wait_for(collector.exited, 10)
        transport.close()
        await reclaim_registered(data)
        await dead(data)
