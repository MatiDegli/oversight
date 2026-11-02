"""NT completion signals across the real suite wrapper, snapshots, locks and discard."""

import asyncio
from unittest.mock import Mock

import pytest

from oversight.workspace import IntegrityError, SuiteInterrupted, WorkspaceBusy, runner, suite_guard
from oversight.workspace.runner import NtCleanupError, NtCleanupRunner, ProcessResult

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("mode", ["normal", "timeout", "cancel"])
@pytest.mark.parametrize("verified", [True, False])
async def test_cleanup_precedes_snapshot_or_retains_runtime(workspace, monkeypatch, mode, verified):
    agent = await workspace.create_worktree("agent-1")
    entered, finish = asyncio.Event(), asyncio.Event()
    order = []

    original = runner._execute

    async def execute(argv, cwd, timeout_s, env, cap, abort):
        if "pytest" not in argv:
            return await original(argv, cwd, timeout_s, env, cap, abort)
        entered.set()
        await finish.wait()
        order.append("leader/job checked")
        if not verified:
            raise NtCleanupError("injected members still alive")
        return ProcessResult(0, "", "", mode == "timeout", False, 1)

    monkeypatch.setattr(runner, "_execute", execute)
    # Force only the NT completion transport, without changing the process platform.
    spawn = NtCleanupRunner(runner.run_process)
    from oversight.workspace.snapshot import finish_snapshot

    restored = finish_snapshot

    def restore(*args):
        assert verified and order == ["leader/job checked"]
        order.append("restore")
        return restored(*args)

    monkeypatch.setattr(suite_guard, "finish_snapshot", restore)
    task = asyncio.create_task(workspace.run_tests(agent, timeout_s=300, spawn=spawn))
    await asyncio.wait_for(entered.wait(), 5)
    assert not workspace.operations.idle
    if mode == "cancel":
        task.cancel("first cancellation")
        await asyncio.sleep(0)
        task.cancel("repeated cancellation")
    assert not task.done() and not order
    finish.set()
    try:
        if not verified:
            with pytest.raises(SuiteInterrupted if mode == "cancel" else IntegrityError) as caught:
                await task
            if mode == "cancel":
                assert isinstance(caught.value, SuiteInterrupted)
                assert caught.value.run.result.passed == 0 and caught.value.run.cases == ()
                assert caught.value.run.problems
            assert order == ["leader/job checked"]
            assert workspace.integrity.main_suspect and workspace.integrity.rules_suspended
            with pytest.raises(IntegrityError):
                await workspace.discard_worktree("agent-1", delete_branch=True)
            with pytest.raises(IntegrityError):
                await workspace.reset()
            workspace.release_runtime()
            assert workspace._lock_fd is not None
            from oversight.workspace import Workspace

            other = Workspace(workspace.runtime_root, workspace._template, pytest_pin="9.1.1")
            with pytest.raises(WorkspaceBusy):
                await other.acquire_runtime()
            assert agent.root.exists()
        elif mode == "cancel":
            with pytest.raises(SuiteInterrupted):
                await task
            assert order[-1] == "restore"
        else:
            await task
            assert order[-1] == "restore"
        assert workspace.operations.idle
    finally:
        # The injected Job now has no live process. Native tests must prove that separately.
        workspace._cleanup_unverified = False


async def test_nt_reap_signal_preserves_original_cancellation(monkeypatch, tmp_path):
    entered, finish = asyncio.Event(), asyncio.Event()

    async def execute(*args):
        entered.set()
        await finish.wait()
        raise NtCleanupError("job query failed")

    monkeypatch.setattr(runner, "_execute", execute)
    task = asyncio.create_task(runner.run_process(["fixture"], cwd=tmp_path, timeout_s=300))
    await entered.wait()
    task.cancel("person stopped")
    await asyncio.sleep(0)
    finish.set()
    with pytest.raises(asyncio.CancelledError, match="person stopped") as caught:
        await task
    assert isinstance(caught.value.__cause__, NtCleanupError)


async def test_reap_close_failure_has_nt_only_signal(monkeypatch):
    async def failed(*args, **kwargs):
        raise OSError("query/close failure")

    monkeypatch.setattr(runner, "_reap_nt_job", failed)
    with pytest.raises(NtCleanupError, match="cleanup is unverified"):
        await runner._reap_nt(Mock(), Mock(), 123)
