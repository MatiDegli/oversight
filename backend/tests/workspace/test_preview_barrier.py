"""Every Git spawn follows namespace death AND snapshot restitution, across agent locks."""

import asyncio
import threading

import pytest

from oversight.workspace import IntegrityError, PreviewError, core
from oversight.workspace import preview_barrier as barriers
from oversight.workspace.files import write_file
from oversight.workspace.runner import run_process
from oversight.workspace.snapshot import finish_snapshot

from .preview_fakes import harness

pytestmark = pytest.mark.anyio


async def test_cross_agent_git_waits_through_death_restoration_and_cancellation(
    workspace,
    monkeypatch,
):
    h = await harness(workspace)
    other = await workspace.create_worktree("agent-2")
    await h.start()
    process = h.processes[0]
    process.stop_gate.clear()
    restore_started, restore_release = threading.Event(), threading.Event()
    restored = False
    spawns = []
    original_finish, original_spawn = finish_snapshot, run_process

    def finish(snapshot, state):
        nonlocal restored
        assert process.dead
        restore_started.set()
        assert restore_release.wait(5)
        original_finish(snapshot, state)
        restored = True

    async def spawn(argv, **kwargs):
        assert process.dead and restored
        spawns.append(argv)
        return await original_spawn(argv, **kwargs)

    monkeypatch.setattr(barriers, "finish_snapshot", finish)
    monkeypatch.setattr(core, "run_process", spawn)
    waiting = asyncio.create_task(other.head())
    await process.stop_started.wait()
    for _ in range(3):
        waiting.cancel()
        await asyncio.sleep(0)
    assert spawns == [] and not waiting.done()
    process.stop_gate.set()
    assert await asyncio.to_thread(restore_started.wait, 5)
    for _ in range(3):
        waiting.cancel()
        await asyncio.sleep(0)
    assert spawns == [] and not waiting.done()
    restore_release.set()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    # The cancelled caller never spawned; another lock may now safely use Git.
    assert await other.head()
    await h.manager.barrier.finish_pending()
    assert h.sink.records[-1].reason == "mutation"
    await h.close()


@pytest.mark.parametrize("operation", ["git", "lease", "startup", "edit", "remove"])
async def test_failed_drain_blocks_all_git_and_non_git_effects(workspace, operation):
    h = await harness(workspace)
    other = await workspace.create_worktree("agent-2")
    created = await h.start()
    h.processes[0].stop_failure = True
    before = (other.root / "shop/mod.py").read_text()
    with pytest.raises((PreviewError, IntegrityError)):
        if operation == "git":
            await other.head()
        elif operation == "lease":
            async with workspace.locks(agent_id="agent-2"), workspace.operations.git_lease():
                pytest.fail("lease escaped failed drain")
        elif operation == "startup":
            await workspace.startup_git(["git", "status"], cwd=other.root, timeout_s=2)
        elif operation == "edit":
            await other.write_text("shop/mod.py", "changed", expected_hash=None)
        else:
            await workspace.discard_worktree("agent-2", delete_branch=True)
    assert (other.root / "shop/mod.py").read_text() == before
    assert workspace.integrity.main_suspect and workspace.integrity.rules_suspended
    assert h.manager.registry.read() is not None and h.manager.has_owner
    assert h.sink.records[-1].state == "failed"
    assert not any(r.state == "stopped" for r in h.sink.records)
    workspace.release_runtime()
    assert workspace.runtime_owned
    with pytest.raises(IntegrityError):
        await h.tree.head()
    with pytest.raises(PreviewError):
        await h.manager.stop(created.preview_id, created.lease_token)
    await h.close()


async def test_filter_metadata_is_restored_before_first_status(workspace, monkeypatch):
    h = await harness(workspace)
    created = await h.start()
    config = workspace.main.git_identity.common / "config"
    original = config.read_bytes()
    config.write_bytes(original + b'\n[filter "evil"]\n clean = echo should-never-run\n')
    spawn = run_process
    calls = []

    async def guarded(argv, **kwargs):
        assert h.processes[0].dead and config.read_bytes() == original
        calls.append(argv)
        return await spawn(argv, **kwargs)

    monkeypatch.setattr(core, "run_process", guarded)
    result = await h.manager.stop(created.preview_id, created.lease_token)
    assert result.reason == "worktree_changed"
    assert calls and workspace.integrity.rules_suspended and not workspace.integrity.main_suspect
    await h.close()


@pytest.mark.parametrize("tamper", ["files", "head", "revision"])
async def test_changed_evidence_never_stages_or_silently_merges(workspace, monkeypatch, tamper):
    h = await harness(workspace)
    created = await h.start()
    if tamper == "files":
        (h.tree.root / "app-written.txt").write_text("keep this")
    elif tamper == "head":
        ref = workspace.main.git_identity.common / "refs/heads/agent-1"
        # Restitution sees metadata tampering even when the final HEAD resolves to the same commit.
        ref.write_text("0" * 40 + "\n")
    else:
        owner = h.manager.barrier.owner
        assert owner is not None
        object.__setattr__(owner.candidate, "current", lambda: False)
    result = await h.manager.stop(created.preview_id, created.lease_token)
    assert result.reason == "worktree_changed"
    async with workspace.locks(main=True, agent_id="agent-1"):
        with pytest.raises(PreviewError, match="new revision"):
            await workspace.preview_evidence(h.tree, 1, h.head)
        await workspace.preview_evidence(h.tree, 2, "new-reviewed-head")
    if tamper == "files":
        assert (h.tree.root / "app-written.txt").read_text() == "keep this"
    await h.close()


async def test_edit_holds_admission_through_its_first_effect(workspace, monkeypatch):
    h = await harness(workspace)
    await h.start()
    original = write_file

    def write(*args):
        assert h.processes[0].dead
        assert h.manager.barrier.owner is None
        return original(*args)

    monkeypatch.setattr(core, "write_file", write)
    before = await h.tree.read_text("shop/mod.py")
    await h.tree.write_text("shop/mod.py", before.content + "\n", expected_hash=before.hash)
    assert h.sink.records[-1].reason == "mutation"
    await h.close()


async def test_nested_lease_runs_hook_once_and_no_tree_lock_is_taken_beneath_it(workspace):
    h = await harness(workspace)
    other = await workspace.create_worktree("agent-2")
    await h.start()
    async with (
        workspace.locks(agent_id="agent-2"),
        workspace.operations.git_lease(),
        workspace.operations.git_lease(),
    ):
        assert h.processes[0].dead
        assert not workspace.operations.holds(agent_id="agent-1")
        assert await other.head()
    await h.close()


@pytest.mark.parametrize("operation", ["suite_capture", "integrity", "behavior", "revert"])
async def test_special_git_paths_share_namespace_and_snapshot_exclusion(
    workspace,
    monkeypatch,
    operation,
):
    from oversight.workspace import (
        prepare_inverse,
        test_tree,
    )

    h = await harness(workspace)
    other = await workspace.create_worktree("agent-2")
    base = await other.head()
    async with workspace.locks(main=True, agent_id="agent-2"):
        inverse_tree = (await workspace.git_ok(other, "rev-parse", "HEAD^{tree}")).strip()
        read = await other.read_text("shop/mod.py")
        await other.write_text("shop/mod.py", read.content + "\n", expected_hash=read.hash)
        head = await other.commit_all("candidate", workspace.agent_author("agent-2"))
    await h.start()
    process = h.processes[0]
    calls = []

    async def guarded(argv, **kwargs):
        owner = h.manager.barrier.owner
        assert process.dead and (owner is None or owner.snapshot_finished)
        calls.append(argv)
        return await run_process(argv, **kwargs)

    monkeypatch.setattr(core, "run_process", guarded)
    monkeypatch.setattr(test_tree, "run_process", guarded)
    if operation == "behavior":

        async def worker(*args, **kwargs):
            owner = h.manager.barrier.owner
            assert process.dead and owner is not None and owner.snapshot_finished
            return {}

        monkeypatch.setattr(core, "run_behavior_worker", worker)
        await workspace.run_behavior(other, (), timeout_s=2)
    else:
        async with workspace.locks(main=True, agent_id="agent-2"):
            if operation == "suite_capture":
                async with workspace.git_lease():
                    await test_tree.capture_test_tree(
                        other.root,
                        workspace.tmp_dir,
                        env=workspace._git_env(other, None, None),
                        flags=workspace.git_flags,
                        timeout_s=5,
                    )
            else:
                await workspace.git_ok(workspace.main, "merge", "--no-ff", "--no-commit", head)
                if operation == "integrity":
                    await verify_combination(workspace, base, head)
                else:
                    await workspace.git_ok(workspace.main, "commit", "-qm", "merge")
                    main_head = await workspace.main.head()
                    async with prepare_inverse(
                        workspace,
                        agent_id="agent-2",
                        main_commit=main_head,
                        inverse_tree=inverse_tree,
                        message="Revert",
                        timeout_s=5,
                    ):
                        pass
    assert calls or operation == "behavior"
    await h.close()


async def test_server_source_inventory_and_repeated_staging_use_guarded_runner(
    workspace, monkeypatch
):
    from types import SimpleNamespace

    h = await harness(workspace)
    await h.start()
    calls = []
    monkeypatch.setattr(workspace, "_own_source", SimpleNamespace())

    async def inventory(*args, spawn, **kwargs):
        assert h.processes[0].live is (not calls)
        await spawn(["git", "--version"], cwd=workspace.runtime_root, timeout_s=2)
        assert h.processes[0].dead
        calls.append("inventory")
        return "listing"

    async def stage(selection, *, spawn, **kwargs):
        await inventory(spawn=spawn)
        return "staged"

    monkeypatch.setattr(core, "inventory", inventory)
    monkeypatch.setattr(core, "validate_selection", lambda value: value)
    monkeypatch.setattr(core, "stage_source", stage)
    assert await workspace.stage_own_source() == "staged"
    assert calls == ["inventory", "inventory"]
    await h.close()


async def verify_combination(workspace, base, head):
    from oversight.workspace import prepare_merge_guard, verify_merge_abort, verify_merge_guard

    guard = await prepare_merge_guard(workspace, workspace.main, pre=base, head=head, timeout_s=5)
    await verify_merge_guard(
        workspace, workspace.main, guard, tested_tree=guard.candidate_tree, timeout_s=5
    )
    await verify_merge_abort(workspace, workspace.main, pre=base, head=head, timeout_s=5)
    await workspace.git_ok(workspace.main, "merge", "--abort")
