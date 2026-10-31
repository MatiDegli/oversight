"""Workspace path validation, file operations and locking."""

import asyncio
import contextlib
import os
import threading
from pathlib import Path
from typing import Any

import pytest
from backend.tests.workspace.workspace_helpers import GitOut, count_spawns
from backend.tests.workspace.workspace_helpers import pytestmark as pytestmark

from oversight.workspace import (
    InvalidPath,
    LockOrderError,
    StaleFile,
    Workspace,
    WorkspaceError,
    content_hash,
)
from oversight.workspace import core as workspace_core
from oversight.workspace import files as workspace_files

# --- Trees: reads, writes, links, commits -----------------------------------------------------


@pytest.mark.parametrize("where", ["main", "agent-1"])
async def test_symlinked_ancestor_refused_at_use(
    workspace: Workspace, tmp_path: Path, where: str
) -> None:
    tree = workspace.main if where == "main" else await workspace.create_worktree("agent-1")
    read = await tree.read_text("shop/mod.py")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "mod.py").write_text("outside = True\n")
    (tree.root / "shop" / "mod.py").unlink()
    (tree.root / "shop" / "__init__.py").unlink()
    (tree.root / "shop").rmdir()
    (tree.root / "shop").symlink_to(outside, target_is_directory=True)
    with pytest.raises(InvalidPath):
        await tree.read_text("shop/mod.py")
    with pytest.raises(InvalidPath):
        await tree.write_text("shop/mod.py", "pwned = True\n", expected_hash=read.hash)
    with pytest.raises(InvalidPath):
        await tree.write_text("shop/new.py", "pwned = True\n", expected_hash=None)
    assert (outside / "mod.py").read_text() == "outside = True\n"
    assert list(outside.iterdir()) == [outside / "mod.py"]
    assert list((workspace.main.root.parent / "tmp").glob(".write-*")) == []


@pytest.mark.parametrize("swap", ["main", "agent", "worktrees"])
async def test_a_root_replaced_by_a_symlink_is_refused_for_every_operation(
    workspace: Workspace, tmp_path: Path, swap: str
) -> None:
    runtime = workspace.runtime_root
    agent = await workspace.create_worktree("agent-1")
    tree = workspace.main if swap == "main" else agent
    read = await tree.read_text("shop/mod.py")
    swapped = {
        "main": runtime / "workspace",
        "agent": runtime / "worktrees" / "agent-1",
        "worktrees": runtime / "worktrees",
    }[swap]
    outside = tmp_path / "outside"
    swapped.rename(outside)
    swapped.symlink_to(outside, target_is_directory=True)
    before = sorted(path.relative_to(outside) for path in outside.rglob("*"))
    mod = outside / ("agent-1" if swap == "worktrees" else "") / "shop" / "mod.py"
    original = mod.read_text()
    with pytest.raises(InvalidPath):
        await tree.read_text("shop/mod.py")
    with pytest.raises(InvalidPath):
        await tree.write_text("shop/mod.py", "pwned = True\n", expected_hash=read.hash)
    with pytest.raises(InvalidPath):
        await tree.write_text("shop/new.py", "pwned = True\n", expected_hash=None)
    with pytest.raises(InvalidPath):
        await tree.list_files()
    with pytest.raises(InvalidPath):
        tree.resolve("shop/mod.py")
    with pytest.raises(InvalidPath):
        await tree.commit_all("pwned", workspace.user_author())
    async with workspace.locks(main=tree.agent_id is None, agent_id=tree.agent_id):
        with pytest.raises(InvalidPath):
            await workspace.git(tree, "status")
    assert mod.read_text() == original
    assert sorted(path.relative_to(outside) for path in outside.rglob("*")) == before
    assert list(workspace.tmp_dir.glob(".write-*")) == []


@pytest.mark.parametrize("damage", ["worktrees symlink", "worktrees file", "workspace symlink"])
async def test_create_worktree_refuses_a_linked_or_misplaced_directory(
    workspace: Workspace, tmp_path: Path, git_out: GitOut, damage: str
) -> None:
    runtime = workspace.runtime_root
    outside = tmp_path / "outside"
    repo = runtime / "workspace"
    if damage == "workspace symlink":
        repo.rename(outside)
        repo.symlink_to(outside, target_is_directory=True)
        repo = outside
    elif damage == "worktrees symlink":
        outside.mkdir()
        (runtime / "worktrees").symlink_to(outside, target_is_directory=True)
    else:
        (runtime / "worktrees").write_text("in the way")
    with pytest.raises(WorkspaceError):
        await workspace.create_worktree("agent-1")
    with pytest.raises(WorkspaceError):
        workspace.worktree("agent-1")
    if damage == "worktrees symlink":
        assert list(outside.iterdir()) == []
    elif damage == "worktrees file":
        assert (runtime / "worktrees").read_text() == "in the way"
    else:
        assert not (runtime / "worktrees").exists()
    assert not (repo / ".git" / "worktrees").exists()
    assert (await git_out(repo, "branch", "--list", "agent-1")).strip() == ""
    assert (await git_out(repo, "worktree", "list")).count("\n") == 1


@pytest.mark.parametrize("where", ["main", "agent-1"])
async def test_a_fifo_is_refused_at_once_and_the_lock_is_released(
    workspace: Workspace, where: str
) -> None:
    tree = workspace.main if where == "main" else await workspace.create_worktree("agent-1")
    fifo = tree.root / "shop" / "pipe"
    os.mkfifo(fifo)
    stalled = threading.Event()

    def unblock() -> None:
        """Only if the read is stuck in open(): let it go, so the test fails instead of hanging."""
        stalled.set()
        with contextlib.suppress(OSError):
            os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))

    watchdog = threading.Timer(5, unblock)
    watchdog.start()
    try:
        with pytest.raises(InvalidPath, match="regular file"):
            await tree.read_text("shop/pipe")
        with pytest.raises(InvalidPath, match="regular file"):
            await tree.write_text("shop/pipe", "x = 1\n", expected_hash=None)
    finally:
        watchdog.cancel()
    assert not stalled.is_set(), "the read blocked on the FIFO"

    async def take_lock() -> None:
        async with workspace.locks(main=tree.agent_id is None, agent_id=tree.agent_id):
            pass

    await asyncio.wait_for(take_lock(), 10)


async def test_tree_resolve_read_write_and_stale_rules(workspace: Workspace) -> None:
    tree = await workspace.create_worktree("agent-1")
    assert tree.resolve("shop/mod.py") == tree.root / "shop" / "mod.py"
    for bad in ("../x", "/etc/passwd", ".git/config", "shop//mod.py"):
        with pytest.raises(InvalidPath):
            tree.resolve(bad)
    first = await tree.read_text("shop/mod.py")
    assert first.content == "def value() -> int:\n    return 1\n"
    assert first.hash == content_hash(first.content.encode())
    second = await tree.write_text("shop/mod.py", "VALUE = 2\n", expected_hash=first.hash)
    assert (await tree.read_text("shop/mod.py")).hash == second
    with pytest.raises(StaleFile):
        await tree.write_text("shop/mod.py", "VALUE = 3\n", expected_hash=first.hash)
    assert (await tree.read_text("shop/mod.py")).content == "VALUE = 2\n"
    # The worktree's edits do not reach main.
    assert (await workspace.main.read_text("shop/mod.py")).hash == first.hash


async def test_commit_all_returns_head_or_none_and_attributes_the_author(
    workspace: Workspace, git_out: GitOut
) -> None:
    tree = await workspace.create_worktree("agent-1")
    assert await tree.commit_all("feat: nothing", workspace.user_author()) is None
    await tree.write_text("shop/new.py", "x = 1\n", expected_hash=None)
    head = await tree.commit_all("feat: add new", workspace.user_author())
    assert head == await tree.head()
    assert head != tree.base_commit
    log = await git_out(tree.root, "log", "-1", "--format=%an <%ae>|%s")
    assert log.strip() == "User <user@oversight.invalid>|feat: add new"
    assert await tree.commit_all("feat: again", workspace.user_author()) is None
    # A message that looks like an option is still a message.
    await tree.write_text("shop/more.py", "y = 1\n", expected_hash=None)
    assert await tree.commit_all("--amend", workspace.user_author()) is not None
    assert (await git_out(tree.root, "log", "-1", "--format=%s")).strip() == "--amend"


# --- Locks and git ----------------------------------------------------------------------------


async def test_git_refusals(workspace: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    agent = await workspace.create_worktree("agent-1")
    spawned = count_spawns(monkeypatch)
    with pytest.raises(LockOrderError):
        await workspace.git(workspace.main, "status")
    async with workspace.locks(agent_id="agent-1"):
        with pytest.raises(LockOrderError):
            await workspace.git(workspace.main, "status")
        with pytest.raises(LockOrderError):
            async with workspace.locks(main=True):
                pytest.fail("main after a worktree")
        assert (await workspace.git(agent, "status", "--porcelain")).returncode == 0
    async with workspace.locks(main=True):
        with pytest.raises(LockOrderError):
            await workspace.git(agent, "status")
        for args in (
            ("stash",),
            ("rebase", "main"),
            ("-C", ".", "stash", "push"),
            ("-c", "a=b", "rebase"),
            ("-c", "a=b", "stash"),
            ("--git-dir", "x", "stash"),
            ("--git-dir=x", "stash"),
            ("--work-tree=x", "stash"),
            ("--work-tree", "x", "status"),
            ("-c", "alias.s=stash", "s"),
            ("--exec-path=x", "status"),
            (),
        ):
            with pytest.raises(WorkspaceError, match="not allowed"):
                await workspace.git(workspace.main, *args)
    assert [argv[-3:] for argv in spawned] == [("status", "--ignore-submodules=all", "--porcelain")]


async def test_approval_waits_for_agent_edit(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = await workspace.create_worktree("agent-1")
    started, release = threading.Event(), threading.Event()
    real = workspace_files.write_file

    def blocked(*args: Any) -> str:
        started.set()
        release.wait()
        return real(*args)

    monkeypatch.setattr(workspace_core, "write_file", blocked)
    order: list[str] = []

    async def edit() -> None:
        await tree.write_text("shop/new.py", "x = 1\n", expected_hash=None)
        order.append("edit done")

    async def approval() -> None:
        async with workspace.locks(main=True, agent_id="agent-1"):
            order.append("approval in")

    edit_task = asyncio.create_task(edit())
    await asyncio.to_thread(started.wait)
    approval_task = asyncio.create_task(approval())
    for _ in range(10):
        await asyncio.sleep(0)
    assert order == []
    release.set()
    await asyncio.gather(edit_task, approval_task)
    assert order == ["edit done", "approval in"]
