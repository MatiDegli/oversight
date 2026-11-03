"""Durability, exact birth ownership, ambiguity quarantine and recovery before reset."""

import os
from dataclasses import replace

import pytest

from oversight.workspace import PreviewError, core
from oversight.workspace import preview_registry as registries
from oversight.workspace.preview_registry import PreviewRegistry, ProcessIdentity

from .preview_fakes import harness

pytestmark = pytest.mark.anyio


async def test_snapshot_survives_new_manager_and_restores_before_any_git(workspace, monkeypatch):
    h = await harness(workspace)
    await h.start()
    data = h.manager.registry.read()
    assert data is not None
    assert data["phase"] == "intent" and data["snapshot"]
    assert not any(name in str(data) for name in ("lease_token", "application output"))
    config = workspace.main.git_identity.common / "config"
    before = config.read_bytes()
    config.write_bytes(before + b'\n[filter "x"]\n clean=never\n')
    # Simulate verified fake death followed by backend crash before metadata restitution.
    await h.processes[0].stop()
    h.manager.barrier.owner = None
    h.manager._reserved = False
    workspace._reset_done = False
    await h.manager.recover()
    assert config.read_bytes() == before
    assert workspace.integrity.rules_suspended and not workspace.integrity.main_suspect
    assert h.manager.registry.read() is None
    h.manager.barrier.owner = None
    h.manager._reserved = False
    await h.close()


@pytest.mark.parametrize(
    "fault",
    [
        "partial",
        "truncated",
        "profile",
        "runtime",
        "worktree",
        "identity",
        "namespace",
        "symlink",
        "snapshot",
    ],
)
async def test_ambiguous_registry_retains_quarantine_and_runtime_lock(workspace, fault):
    h = await harness(workspace)
    await h.start()
    registry = h.manager.registry
    data = registry.read()
    assert data is not None
    if fault == "partial":
        registry.pending.write_text("incomplete")
    elif fault == "truncated":
        registry.path.write_text("{")
    elif fault == "symlink":
        registry.path.unlink()
        registry.path.symlink_to(workspace.main.root / "pyproject.toml")
    else:
        if fault == "profile":
            data["profile"] = "different-session-profile"
        elif fault == "runtime":
            data["runtime"] = "/another/runtime"
        elif fault == "worktree":
            data["worktree"] = "/unregistered/tree"
        elif fault == "identity":
            data["supervisor"] = {"pid": os.getpid(), "birth": "wrong", "boot": "wrong"}
        elif fault == "snapshot":
            data["snapshot"]["anchors"] = {}
        else:
            identity = registries.identity_data(os.getpid())
            data.update(namespace=identity, unshare=identity, namespaces={"net": "wrong"})
        registry.write(data)
    with pytest.raises(PreviewError, match="recovery is unverified"):
        await h.manager.recover()
    assert workspace.integrity.main_suspect
    assert registry.path.exists() or registry.path.is_symlink()
    workspace.release_runtime()
    assert workspace.runtime_owned
    await h.close()


async def test_birth_mismatch_never_signals_a_reused_pid(monkeypatch):
    actual = ProcessIdentity.read(os.getpid())
    with pytest.raises(PreviewError, match="ambiguous"):
        replace(actual, birth="reused").alive()
    sent = []
    monkeypatch.setattr(registries, "kill_pinned", lambda *args: sent.append(args))
    with pytest.raises(PreviewError):
        await registries.reclaim_registered(
            {"supervisor": {"pid": actual.pid, "birth": "other", "boot": actual.boot}}
        )
    assert sent == []


async def test_only_pinned_registered_ownership_is_reclaimed(monkeypatch):
    births = {"pid": 101, "birth": "10", "boot": "boot"}
    sent, deaths, closed = [], [], []
    monkeypatch.setattr(ProcessIdentity, "alive", lambda _: True)
    monkeypatch.setattr(registries, "open_pidfd", lambda pid: pid + 1000)
    monkeypatch.setattr(
        registries, "namespace_links", lambda pid: dict.fromkeys(("net", "pid", "mnt"), "private")
    )
    monkeypatch.setattr(registries, "kill_pinned", lambda fd: sent.append(fd))

    async def death(fd):
        deaths.append(fd)

    monkeypatch.setattr(registries, "verify_preview_namespace_death", death)
    await registries.reclaim_registered(
        {
            "supervisor": births,
            "unshare": births | {"pid": 102},
            "namespace": births | {"pid": 103},
            "namespaces": dict.fromkeys(("net", "pid", "mnt"), "private"),
        }
    )
    assert sent == [1101, 1102, 1103]
    assert deaths == [1103, 1101, 1102] and closed == []


async def test_unknown_listeners_remove_capacity_without_killing(monkeypatch):
    import socket

    from oversight.workspace.preview_process import bind_listener

    unknown = socket.socket()
    unknown.bind(("127.0.0.1", 0))
    unknown.listen(1)
    kills = []
    monkeypatch.setattr(registries, "kill_pinned", lambda *args: kills.append(args))
    try:
        with pytest.raises(PreviewError, match="no available"):
            bind_listener((unknown.getsockname()[1],))
    finally:
        unknown.close()
    assert kills == []


async def test_recovery_runs_under_runtime_lock_before_orphans_and_reset(
    make_workspace, monkeypatch
):
    workspace = make_workspace()
    from oversight.workspace import PreviewManager

    manager = PreviewManager(workspace)
    order = []

    async def recover():
        assert workspace.runtime_owned
        order.append("recover")

    def orphans(root):
        assert order == ["recover"]
        order.append("orphans")

    monkeypatch.setattr(manager, "recover", recover)
    monkeypatch.setattr(core, "refuse_orphans", orphans)
    await workspace.acquire_runtime()
    assert order == ["recover", "orphans"]
    await workspace.reset()


async def test_registry_write_is_atomic_private_and_syncs_intent(tmp_path, monkeypatch):
    registry = PreviewRegistry(tmp_path)
    registry.prepare()
    order = []
    sync, replace_file = os.fsync, os.replace

    def fsync(fd):
        order.append("fsync")
        sync(fd)

    def replace(source, destination):
        assert registry.pending.exists() and order == ["fsync"]
        order.append("replace")
        replace_file(source, destination)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "replace", replace)
    registry.write({"version": 1, "runtime": str(tmp_path), "phase": "intent"})
    assert order == ["fsync", "replace", "fsync"]
    assert registry.path.stat().st_mode & 0o777 == 0o600
    assert registry.directory.stat().st_mode & 0o777 == 0o700


async def test_crash_recovery_preserves_contamination_and_refuses_reset(workspace):
    h = await harness(workspace)
    await h.start()
    path = h.tree.root / "app-written.txt"
    path.write_text("preserve after crash")
    await h.processes[0].stop()
    h.manager.barrier.owner = None
    h.manager._reserved = False
    workspace._reset_done = False
    with pytest.raises(PreviewError, match="recovery is unverified"):
        await h.manager.recover()
    assert workspace.integrity.main_suspect and h.manager.registry.read() is not None
    with pytest.raises(Exception, match="main_suspect"):
        await workspace.reset()
    assert path.read_text() == "preserve after crash" and workspace.runtime_owned
    await h.close()


async def test_invalid_phase_is_quarantined_before_any_registered_signal(workspace, monkeypatch):
    h = await harness(workspace)
    await h.start()
    data = h.manager.registry.read()
    assert data is not None
    data["phase"] = "unrecognized"
    data["supervisor"] = registries.identity_data(os.getpid())
    h.manager.registry.write(data)
    sent = []
    monkeypatch.setattr(registries, "kill_pinned", sent.append)
    with pytest.raises(PreviewError):
        await h.manager.recover()
    assert sent == [] and workspace.integrity.main_suspect
    await h.close()


@pytest.mark.parametrize("fault", ["owned", "entering", "partial", "profile", "boot", "files"])
async def test_manual_release_after_recovery_refusal_allows_fresh_startup(
    workspace, make_workspace, fault
):
    from oversight.workspace import IntegrityError, PreviewManager
    from oversight.workspace.preview_registry import identity_data

    h = await harness(workspace)
    await h.start()
    await h.processes[0].stop()  # Fake namespace/process death has been verified.
    data = h.manager.registry.read()
    assert data is not None
    if fault in {"owned", "entering"}:
        data["phase"] = fault
    elif fault == "profile":
        data["profile"] = "previous-profile"
    elif fault == "boot":
        data["supervisor"] = identity_data(os.getpid()) | {"boot": "previous-boot"}
    elif fault == "files":
        (h.tree.root / "app-written.txt").write_text("preserve me")
    h.manager.registry.write(data)
    if fault == "partial":
        h.manager.registry.pending.write_text("incomplete")
    h.manager.barrier.owner = None
    h.manager._reserved = False
    with pytest.raises(PreviewError) as caught:
        await h.manager.recover()
    message = str(caught.value)
    assert "inspect worktrees/<agent>" in message
    assert "remove preview-owner/owner.json" in message and "owner.pending" in message
    assert workspace._cleanup_unverified and workspace.runtime_owned
    with pytest.raises(IntegrityError, match="main_suspect"):
        await h.tree.write_text("blocked.txt", "no effect", expected_hash=None)
    assert not (h.tree.root / "blocked.txt").exists()
    await h.close()
    # Simulate process exit (releasing its lock) and the documented manual recovery.
    workspace._cleanup_unverified = False
    workspace.release_runtime()
    if fault == "files":
        assert (h.tree.root / "app-written.txt").read_text() == "preserve me"
    h.manager.registry.path.unlink()
    h.manager.registry.pending.unlink(missing_ok=True)
    fresh = make_workspace()
    PreviewManager(fresh)
    await fresh.acquire_runtime()
    assert fresh.runtime_owned and not fresh.integrity.main_suspect
    assert await fresh.reset()
