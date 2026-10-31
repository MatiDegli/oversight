"""Inverse transactions on disposable Git repositories; no provider or credits."""

import asyncio
from pathlib import Path

import pytest
from backend.tests.workspace.workspace_helpers import (
    MESSAGE,
    candidate,
    facts,
)
from backend.tests.workspace.workspace_helpers import api as api
from backend.tests.workspace.workspace_helpers import pytestmark as pytestmark

from oversight.workspace import ProcessResult


async def test_prepare_exact_unreachable_commit_and_verified_cleanup(workspace, api):
    h, t = await candidate(workspace, api)
    async with workspace.locks(main=True, agent_id="agent-1"):
        before = await facts(workspace)
        async with api.prepare_inverse(
            workspace,
            agent_id="agent-1",
            main_commit=h,
            inverse_tree=t,
            message=MESSAGE,
            timeout_s=5,
        ) as item:
            assert (item.main_commit, item.inverse_tree) == (h, t)
            assert item.tree.agent_id == "agent-1"
            git = workspace.git_ok
            assert (await git(item.tree, "rev-parse", "HEAD")).strip() == item.inverse_commit
            assert (await git(item.tree, "rev-parse", "HEAD^{tree}")).strip() == t
            assert (await git(item.tree, "rev-list", "--parents", "-n", "1", "HEAD")).split() == [
                item.inverse_commit,
                h,
            ]
            identity = await git(item.tree, "show", "-s", "--format=%an|%ae|%cn|%ce", "HEAD")
            assert identity.strip() == "User|user@oversight.invalid|User|user@oversight.invalid"
            assert not await git(workspace.main, "for-each-ref", "--contains", item.inverse_commit)
            assert not item.cleaned
            assert await facts(workspace) == before
            root, admin = item.tree.root, item.tree.git_identity.directory
        assert item.cleaned
        assert not root.exists() and not admin.exists()
        assert str(root) not in await git(workspace.main, "worktree", "list", "--porcelain")
        assert await facts(workspace) == before


async def test_empty_inverse_refused_before_any_object(workspace, api, monkeypatch):
    async with workspace.locks(main=True, agent_id="agent-1"):
        h = await workspace.main.head()
        t = (await workspace.git_ok(workspace.main, "rev-parse", "HEAD^{tree}")).strip()
        real = workspace.git
        calls = []

        async def observe(tree, *args, **kwargs):
            calls.append(args[0])
            return await real(tree, *args, **kwargs)

        monkeypatch.setattr(workspace, "git", observe)
        with pytest.raises(api.InverseError, match="empty"):
            async with api.prepare_inverse(
                workspace,
                agent_id="agent-1",
                main_commit=h,
                inverse_tree=t,
                message=MESSAGE,
                timeout_s=5,
            ):
                pytest.fail("empty inverse accepted")
        assert "commit-tree" not in calls and "worktree" not in calls
        assert not list(workspace.tmp_dir.glob("revert-*"))


@pytest.mark.parametrize("held", ["none", "main", "agent", "wrong_agent"])
async def test_prepare_requires_both_original_locks(workspace, api, held):
    h, t = await candidate(workspace, api)

    async def attempt():
        with pytest.raises(api.InverseError, match="lock"):
            async with api.prepare_inverse(
                workspace,
                agent_id="agent-1",
                main_commit=h,
                inverse_tree=t,
                message=MESSAGE,
                timeout_s=5,
            ):
                pytest.fail("missing caller locks accepted")

    if held == "none":
        await attempt()
    else:
        async with workspace.locks(
            main=held != "agent",
            agent_id={"main": None, "agent": "agent-1", "wrong_agent": "agent-2"}[held],
        ):
            await attempt()


@pytest.mark.parametrize("main_held", [False, True])
async def test_invalid_agent_does_not_create_lock(workspace, api, main_held):
    async def attempt():
        before = dict(workspace.operations._agents)
        try:
            with pytest.raises(api.InverseError, match="invalid original agent id"):
                async with api.prepare_inverse(
                    workspace,
                    agent_id="../invalid",
                    main_commit="1" * 40,
                    inverse_tree="2" * 40,
                    message=MESSAGE,
                    timeout_s=5,
                ):
                    pytest.fail("invalid agent accepted")
        finally:
            assert workspace.operations._agents == before

    if main_held:
        async with workspace.locks(main=True):
            await attempt()
    else:
        await attempt()


async def test_partial_add_cancellation_removes_registration(workspace, api, monkeypatch):
    h, t = await candidate(workspace, api)
    real, roots = workspace.git, []

    async def interrupt(tree, *args, **kwargs):
        result = await real(tree, *args, **kwargs)
        if args[:2] == ("worktree", "add"):
            roots.append(Path(args[3]))
            raise asyncio.CancelledError
        return result

    monkeypatch.setattr(workspace, "git", interrupt)
    async with workspace.locks(main=True, agent_id="agent-1"):
        with pytest.raises(asyncio.CancelledError):
            async with api.prepare_inverse(
                workspace,
                agent_id="agent-1",
                main_commit=h,
                inverse_tree=t,
                message=MESSAGE,
                timeout_s=5,
            ):
                pytest.fail("interrupted add yielded")
        assert roots and not roots[0].exists()
        assert not (workspace.main.git_identity.common / "worktrees" / roots[0].name).exists()
        assert str(roots[0]) not in await workspace.git_ok(
            workspace.main, "worktree", "list", "--porcelain"
        )


@pytest.mark.parametrize("prune_fails", [False, True])
async def test_cleanup_fallback_and_unverified_cleanup(workspace, api, monkeypatch, prune_fails):
    h, t = await candidate(workspace, api)
    real = workspace.git

    async def fail(tree, *args, **kwargs):
        if args[:2] == ("worktree", "remove") or (
            prune_fails and args[:2] == ("worktree", "prune")
        ):
            return ProcessResult(1, "", "injected cleanup error", False, False, 1)
        return await real(tree, *args, **kwargs)

    monkeypatch.setattr(workspace, "git", fail)
    async with workspace.locks(main=True, agent_id="agent-1"):
        async with api.prepare_inverse(
            workspace,
            agent_id="agent-1",
            main_commit=h,
            inverse_tree=t,
            message=MESSAGE,
            timeout_s=5,
        ) as item:
            pass
        assert item.cleaned is (not prune_fails)
        assert not item.tree.root.exists()
        assert item.tree.git_identity.directory.exists() is prune_fails


@pytest.mark.parametrize("body_fails", [False, True])
async def test_cleanup_cancellation_preserves_body_exit(workspace, api, monkeypatch, body_fails):
    h, t = await candidate(workspace, api)
    real = workspace.git
    cleaning = asyncio.Event()
    interrupted = False

    async def interrupt(tree, *args, **kwargs):
        nonlocal interrupted
        if args[:2] == ("worktree", "remove") and not interrupted:
            interrupted = True
            cleaning.set()
            await asyncio.Event().wait()
        return await real(tree, *args, **kwargs)

    monkeypatch.setattr(workspace, "git", interrupt)
    error = ValueError("body failed")
    seen = []

    async def owner():
        async with (
            workspace.locks(main=True, agent_id="agent-1"),
            api.prepare_inverse(
                workspace,
                agent_id="agent-1",
                main_commit=h,
                inverse_tree=t,
                message=MESSAGE,
                timeout_s=5,
            ) as item,
        ):
            seen.append(item)
            if body_fails:
                raise error

    task = asyncio.create_task(owner())
    async with asyncio.timeout(60):
        await cleaning.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
    assert task.cancelled()
    assert caught.value.__cause__ is (error if body_fails else None)
    assert interrupted and seen[0].cleaned
