"""Workspace lifecycle against real git in temporary repositories. None of it touches the
repository this project lives in, or its .runtime."""

import asyncio
import contextlib
import os
import stat
import threading
import tomllib
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from oversight.workspace import (
    InvalidPath,
    LockOrderError,
    ProcessResult,
    StaleFile,
    UnsafeDeletion,
    Workspace,
    WorkspaceBusy,
    WorkspaceError,
    content_hash,
    run_process,
)
from oversight.workspace import core as workspace_core
from oversight.workspace import files as workspace_files
from oversight.workspace import runner as workspace_runner
from oversight_contracts.events import SuiteResult

pytestmark = pytest.mark.anyio

type GitOut = Callable[..., Awaitable[str]]
type MakeWorkspace = Callable[..., Workspace]

INITIAL_DATE = "2026-10-26T00:00:00+00:00"


def count_spawns(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """Record every process the workspace starts, and still run it."""
    spawned: list[tuple[str, ...]] = []
    real = workspace_runner.run_process

    async def counting(argv: Any, **kwargs: Any) -> ProcessResult:
        spawned.append(tuple(argv))
        return await real(argv, **kwargs)

    monkeypatch.setattr(workspace_core, "run_process", counting)
    return spawned


# --- reset ------------------------------------------------------------------------------------


async def test_reset_initial_commit_is_identical_across_roots(
    make_workspace: MakeWorkspace, git_out: GitOut
) -> None:
    commits = []
    for name in ("one", "two"):
        workspace = make_workspace(name)
        await workspace.acquire_runtime()
        commits.append(await workspace.reset())
    assert commits[0] == commits[1]
    first = make_workspace("one").main.root
    log = await git_out(first, "log", "--format=%an <%ae>|%cn <%ce>|%aI|%cI|%s")
    assert log.splitlines() == [
        f"Oversight <oversight@oversight.invalid>|Oversight <oversight@oversight.invalid>|"
        f"{INITIAL_DATE}|{INITIAL_DATE}|Initial workspace"
    ]
    assert (await git_out(first, "rev-parse", "HEAD")).strip() == commits[0]
    assert (await git_out(first, "branch", "--show-current")).strip() == "main"
    assert (await git_out(first, "status", "--porcelain")).strip() == ""


async def test_reset_copy_rules(workspace: Workspace, git_out: GitOut) -> None:
    root = workspace.main.root
    assert await workspace.main.list_files() == [
        "pyproject.toml",
        "shop/__init__.py",
        "shop/mod.py",
        "tests/test_mod.py",
    ]
    assert not (root / ".venv").exists()
    assert not (root / "shop" / "__pycache__").exists()
    for path in root.rglob("*"):
        if path.is_file() and ".git" not in path.relative_to(root).parts:
            assert stat.S_IMODE(path.stat().st_mode) == 0o644, path
    tracked = (await git_out(root, "ls-files")).split()
    assert ".venv/bin/python" not in tracked
    # Cache directories created later never show as untracked.
    (root / "shop" / "__pycache__").mkdir()
    (root / "shop" / "__pycache__" / "x.pyc").write_text("x")
    assert (await git_out(root, "status", "--porcelain")).strip() == ""


async def test_reset_refuses_a_template_symlink(
    make_workspace: MakeWorkspace, template: Path
) -> None:
    (template / "link.py").symlink_to(template / "shop" / "mod.py")
    workspace = make_workspace()
    await workspace.acquire_runtime()
    with pytest.raises(WorkspaceError, match="symbolic link"):
        await workspace.reset()


async def test_reset_wipes_state_and_recovers(
    make_workspace: MakeWorkspace, tmp_path: Path, git_out: GitOut
) -> None:
    first = make_workspace("again")
    await first.acquire_runtime()
    base = await first.reset()
    root = first.main.root.parent
    await first.create_worktree("agent-1")
    (first.main.root / "shop" / "mod.py").write_text("broken = (\n")
    (first.main.root / "extra.py").write_text("x")
    (first.main.root / ".git" / "index.lock").write_text("")
    (root / "tmp" / "half-copy").mkdir()
    (root / "runs").mkdir()
    (root / "runs" / "run-1.sqlite").write_text("events")
    (root / "ledger.sqlite").write_text("spend")
    (root / "demo-venv").mkdir()
    (root / "demo-venv" / "oversight-stamp").write_text("pytest==9.1.1")
    first.release_runtime()

    second = make_workspace("again")
    await second.acquire_runtime()
    assert await second.reset() == base
    assert (await git_out(second.main.root, "status", "--porcelain")).strip() == ""
    assert not (second.main.root / ".git" / "index.lock").exists()
    assert not (second.main.root / "extra.py").exists()
    assert not (root / "worktrees").exists()
    assert not (root / "tmp" / "half-copy").exists()
    assert (root / "runs" / "run-1.sqlite").read_text() == "events"
    assert (root / "ledger.sqlite").read_text() == "spend"
    assert (root / "demo-venv" / "oversight-stamp").exists()
    with pytest.raises(WorkspaceError):
        second.worktree("agent-1")


async def test_startup_guards(make_workspace: MakeWorkspace) -> None:
    first = make_workspace("guards")
    other = make_workspace("guards")
    with pytest.raises(WorkspaceBusy):
        await first.reset()
    await first.acquire_runtime()
    with pytest.raises(WorkspaceBusy, match="another Oversight server"):
        await other.acquire_runtime()
    await first.reset()
    marker = first.main.root / "marker.txt"
    marker.write_text("kept")
    with pytest.raises(WorkspaceBusy, match="already ran"):
        await first.reset()
    assert marker.read_text() == "kept"
    first.release_runtime()
    await other.acquire_runtime()


async def test_the_runtime_lock_is_not_inherited_by_children(
    make_workspace: MakeWorkspace, tmp_path: Path
) -> None:
    workspace = make_workspace("inherit")
    await workspace.acquire_runtime()
    result = await run_process(["ls", "-l", "/proc/self/fd"], cwd=tmp_path, timeout_s=30)
    assert result.returncode == 0
    assert "server.lock" not in result.stdout


async def test_reset_refused_during_agent_operation(
    make_workspace: MakeWorkspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = make_workspace("busy")
    await workspace.acquire_runtime()
    workspace.main.root.mkdir(parents=True)
    (workspace.main.root.parent / "tmp").mkdir()
    started, release = threading.Event(), threading.Event()
    real = workspace_files.write_file

    def blocked(*args: Any) -> str:
        started.set()
        release.wait()
        return real(*args)

    monkeypatch.setattr(workspace_core, "write_file", blocked)
    write = asyncio.create_task(workspace.main.write_text("a.txt", "x", expected_hash=None))
    await asyncio.to_thread(started.wait)
    with pytest.raises(WorkspaceBusy, match="in progress"):
        await workspace.reset()
    assert (workspace.main.root / "a.txt").exists() is False
    release.set()
    assert await write == content_hash(b"x")
    assert (workspace.main.root / "a.txt").read_text() == "x"


async def test_git_ignores_user_global_config_and_the_parent_environment(
    make_workspace: MakeWorkspace,
    tmp_path: Path,
    git_out: GitOut,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    (home / "hooks").mkdir(parents=True)
    hook = home / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch {tmp_path}/hook-ran\n")
    hook.chmod(0o755)
    (home / ".gitconfig").write_text(
        f"[user]\n\tname = Leak\n\temail = leak@example.com\n"
        f"[commit]\n\tgpgsign = true\n[core]\n\thooksPath = {home}/hooks\n"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Leak")
    workspace = make_workspace("global")
    await workspace.acquire_runtime()
    await workspace.reset()
    tree = await workspace.create_worktree("agent-1")
    await tree.write_text("new.py", "x = 1\n", expected_hash=None)
    head = await tree.commit_all("feat: add new", workspace.agent_author("agent-1"))
    assert head is not None
    author = await git_out(tree.root, "log", "-1", "--format=%an <%ae>|%cn <%ce>")
    assert (
        author.strip() == "agent-1 <agent-1@oversight.invalid>|agent-1 <agent-1@oversight.invalid>"
    )
    assert not (tmp_path / "hook-ran").exists()
    assert (await git_out(tree.root, "log", "-1", "--format=%G?")).strip() == "N"


async def test_git_never_looks_above_the_runtime_directory(
    make_workspace: MakeWorkspace, tmp_path: Path, git_out: GitOut
) -> None:
    await git_out(tmp_path, "init", "-q", "-b", "main")
    workspace = make_workspace("outer")
    await workspace.acquire_runtime()
    workspace.main.root.mkdir(parents=True)
    async with workspace.locks(main=True):
        result = await workspace.git(workspace.main, "rev-parse", "--show-toplevel")
    assert result.returncode != 0


# --- Worktrees --------------------------------------------------------------------------------


async def test_create_worktree_records_base_commit_and_branch(
    workspace: Workspace, git_out: GitOut
) -> None:
    main_head = await workspace.main.head()
    tree = await workspace.create_worktree("agent-1")
    assert (tree.agent_id, tree.branch, tree.base_commit) == ("agent-1", "agent-1", main_head)
    assert tree.root == workspace.main.root.parent / "worktrees" / "agent-1"
    assert workspace.worktree("agent-1") is tree
    assert (tree.root / ".git").is_file()
    assert (await git_out(tree.root, "rev-parse", "HEAD")).strip() == main_head
    assert (await git_out(tree.root, "branch", "--show-current")).strip() == "agent-1"
    listing = await git_out(workspace.main.root, "worktree", "list", "--porcelain")
    assert f"worktree {tree.root}" in listing
    assert await tree.list_files() == await workspace.main.list_files()


@pytest.mark.parametrize(
    "agent_id", ["../x", "agent-0", "agent-1/..", "agent-01", "agent-10000", "main", "", "agent-1 "]
)
async def test_bad_agent_ids_and_existing_paths_are_refused(
    workspace: Workspace, agent_id: str
) -> None:
    worktrees = workspace.main.root.parent / "worktrees"
    with pytest.raises(WorkspaceError):
        await workspace.create_worktree(agent_id)
    assert not worktrees.exists() or list(worktrees.iterdir()) == []


async def test_an_existing_path_or_worktree_is_refused_and_left_alone(workspace: Workspace) -> None:
    existing = workspace.main.root.parent / "worktrees" / "agent-2"
    existing.mkdir(parents=True)
    (existing / "keep.txt").write_text("mine")
    with pytest.raises(WorkspaceError, match="already"):
        await workspace.create_worktree("agent-2")
    assert (existing / "keep.txt").read_text() == "mine"
    await workspace.create_worktree("agent-1")
    with pytest.raises(WorkspaceError, match="already"):
        await workspace.create_worktree("agent-1")


async def test_discard_removes_dir_metadata_and_branch(
    workspace: Workspace, git_out: GitOut
) -> None:
    main_head = await workspace.main.head()
    tree = await workspace.create_worktree("agent-1")
    await tree.write_text(
        "shop/mod.py",
        "def value() -> int:\n    return 2\n",
        expected_hash=content_hash(b"def value() -> int:\n    return 1\n"),
    )
    await tree.write_text("shop/new.py", "x = 1\n", expected_hash=None)
    await tree.commit_all("feat: edit", workspace.agent_author("agent-1"))
    (tree.root / "untracked.txt").write_text("x")
    (tree.root / "tests" / "test_mod.py").write_text("dirty")
    metadata = workspace.main.root / ".git" / "worktrees" / "agent-1"
    assert metadata.is_dir()

    await workspace.discard_worktree("agent-1", delete_branch=True)

    assert not tree.root.exists()
    assert not metadata.exists()
    assert (await git_out(workspace.main.root, "branch", "--list", "agent-1")).strip() == ""
    assert await workspace.main.head() == main_head
    with pytest.raises(WorkspaceError):
        workspace.worktree("agent-1")
    again = await workspace.create_worktree("agent-1")
    assert again.base_commit == main_head


async def test_discard_can_keep_the_branch(workspace: Workspace, git_out: GitOut) -> None:
    await workspace.create_worktree("agent-1")
    await workspace.discard_worktree("agent-1", delete_branch=False)
    assert (await git_out(workspace.main.root, "branch", "--list", "agent-1")).strip() != ""


async def test_cleanup_merged_refuses_unmerged_or_dirty_and_succeeds_after_a_merge(
    workspace: Workspace, git_out: GitOut
) -> None:
    tree = await workspace.create_worktree("agent-1")
    await tree.write_text("shop/new.py", "x = 1\n", expected_hash=None)
    head = await tree.commit_all("feat: add", workspace.agent_author("agent-1"))
    with pytest.raises(UnsafeDeletion, match="not merged"):
        await workspace.cleanup_merged_worktree("agent-1", delete_branch=True)
    assert tree.root.exists()
    assert (await git_out(workspace.main.root, "branch", "--list", "agent-1")).strip() != ""

    async with workspace.locks(main=True, agent_id="agent-1"):
        await workspace.git_ok(workspace.main, "merge", "--ff-only", "-q", "agent-1")
    (tree.root / "stray.txt").write_text("x")
    with pytest.raises(UnsafeDeletion, match="uncommitted"):
        await workspace.cleanup_merged_worktree("agent-1", delete_branch=True)
    assert (tree.root / "stray.txt").exists()
    (tree.root / "stray.txt").unlink()

    await workspace.cleanup_merged_worktree("agent-1", delete_branch=True)
    assert not tree.root.exists()
    assert (await git_out(workspace.main.root, "branch", "--list", "agent-1")).strip() == ""
    assert await workspace.main.head() == head
    assert (workspace.main.root / "shop" / "new.py").read_text() == "x = 1\n"


async def test_removal_refusals_run_no_git_and_lose_nothing(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = workspace.main.root.parent
    registered = await workspace.create_worktree("agent-1")
    plain = root / "worktrees" / "agent-3"
    plain.mkdir()
    (plain / "keep.txt").write_text("mine")
    foreign = root / "worktrees" / "agent-4"
    foreign.mkdir()
    (foreign / ".git").write_text(f"gitdir: {workspace.main.root}/.git/worktrees/agent-1\n")
    (foreign / "keep.txt").write_text("mine")
    spawned = count_spawns(monkeypatch)

    for agent_id in ("agent-3", "agent-4", "agent-9", "not-an-agent"):
        for removal in (workspace.discard_worktree, workspace.cleanup_merged_worktree):
            with pytest.raises(UnsafeDeletion):
                await removal(agent_id, delete_branch=True)
    assert (plain / "keep.txt").read_text() == "mine"
    assert (foreign / "keep.txt").read_text() == "mine"

    # The registered worktree, with its recorded gitdir changed to another place.
    gitdir = workspace.main.root / ".git" / "worktrees" / "agent-1" / "gitdir"
    original = gitdir.read_text()
    gitdir.write_text(str(plain / ".git") + "\n")
    for removal in (workspace.discard_worktree, workspace.cleanup_merged_worktree):
        with pytest.raises(UnsafeDeletion):
            await removal("agent-1", delete_branch=True)
    gitdir.write_text(original)

    # A symlinked ancestor.
    (root / "worktrees").rename(root / "worktrees-real")
    (root / "worktrees").symlink_to(root / "worktrees-real", target_is_directory=True)
    for removal in (workspace.discard_worktree, workspace.cleanup_merged_worktree):
        with pytest.raises(UnsafeDeletion):
            await removal("agent-1", delete_branch=True)
    assert spawned == []
    assert (root / "worktrees-real" / "agent-1" / "shop" / "mod.py").exists()
    assert registered.root.exists() or (root / "worktrees-real" / "agent-1").exists()


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


# --- Demo venv and redaction ------------------------------------------------------------------


def interpreter_exists(path: str) -> bool:
    return Path(path).exists()


class FakeUv:
    """Stands in for the runner: records calls, builds a fake venv, and answers the probe. Like the
    real runner, starting an interpreter that is not there raises `FileNotFoundError`."""

    def __init__(self, root: Path, *, probe_ok: bool = True, install_ok: bool = True) -> None:
        self.root = root
        self.calls: list[tuple[tuple[str, ...], dict[str, str]]] = []
        self.probe_ok = probe_ok
        self.install_ok = install_ok

    async def __call__(self, argv: Any, *, env: Any = None, **_: Any) -> ProcessResult:
        argv = tuple(argv)
        self.calls.append((argv, dict(env or {})))
        code = 0
        if argv[:3] == ("uv", "--no-config", "venv"):
            (self.root / "demo-venv" / "bin").mkdir(parents=True)
            (self.root / "demo-venv" / "bin" / "python").write_text("")
        elif "pip" in argv and not self.install_ok:
            code = 1
        elif argv[0].endswith("python"):
            if not interpreter_exists(argv[0]):  # absent, or a symlink whose target is gone
                raise FileNotFoundError(2, "No such file or directory", argv[0])
            code = 0 if self.probe_ok else 1
        return ProcessResult(code, "", "boom" if code else "", False, False, 1)

    @property
    def commands(self) -> list[tuple[str, ...]]:
        return [call[0] for call in self.calls]


async def test_ensure_demo_venv_builds_with_absolute_targets_and_stamps_last(
    make_workspace: MakeWorkspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = make_workspace("venv")
    root = workspace.main.root.parent
    root.mkdir()
    fake = FakeUv(root)
    monkeypatch.setattr(workspace_core, "run_process", fake)
    monkeypatch.setenv("META_API_KEY", "test-key")
    for name in [key for key in os.environ if key.startswith("UV_")]:
        monkeypatch.delenv(name)  # a developer's own settings must not reach the assertions
    monkeypatch.setenv("UV_CACHE_DIR", str(root / "uv-cache"))
    for name in ("UV_OVERRIDE", "UV_EXCLUDE", "UV_CONSTRAINT", "UV_NO_SOURCES"):
        monkeypatch.setenv(name, "test-value")
    await workspace.ensure_demo_venv()
    venv, python = root / "demo-venv", root / "demo-venv" / "bin" / "python"
    assert fake.commands == [
        ("uv", "--no-config", "venv", "--quiet", "--python", "3.12", str(venv)),
        (
            "uv",
            "--no-config",
            "pip",
            "install",
            "--quiet",
            "--exclude-newer",
            "14 days",
            "--python",
            str(python),
            "pytest==9.1.1",
        ),
    ]
    assert all(Path(part).is_absolute() for part in (str(venv), str(python)))
    assert (venv / "oversight-stamp").read_text().strip() == "pytest==9.1.1"
    for _, env in fake.calls:
        assert "META_API_KEY" not in env
        assert env["UV_CACHE_DIR"] == str(root / "uv-cache")
        assert sorted(key for key in env if key.startswith("UV_")) == ["UV_CACHE_DIR"]


async def test_ensure_demo_venv_reuses_a_stamped_venv_that_runs(
    make_workspace: MakeWorkspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = make_workspace("reuse")
    root = workspace.main.root.parent
    (root / "demo-venv" / "bin").mkdir(parents=True)
    (root / "demo-venv" / "bin" / "python").write_text("")
    (root / "demo-venv" / "oversight-stamp").write_text("pytest==9.1.1\n")
    fake = FakeUv(root)
    monkeypatch.setattr(workspace_core, "run_process", fake)
    await workspace.ensure_demo_venv()
    assert [argv[0] for argv in fake.commands] == [str(root / "demo-venv" / "bin" / "python")]


@pytest.mark.parametrize("stamp", ["pytest==9.0.0\n", "", None])
async def test_ensure_demo_venv_rebuilds_on_a_wrong_or_missing_stamp(
    make_workspace: MakeWorkspace, monkeypatch: pytest.MonkeyPatch, stamp: str | None
) -> None:
    workspace = make_workspace("rebuild")
    root = workspace.main.root.parent
    (root / "demo-venv").mkdir(parents=True)
    (root / "demo-venv" / "leftover.txt").write_text("old")
    if stamp is not None:
        (root / "demo-venv" / "oversight-stamp").write_text(stamp)
    fake = FakeUv(root)
    monkeypatch.setattr(workspace_core, "run_process", fake)
    await workspace.ensure_demo_venv()
    assert not (root / "demo-venv" / "leftover.txt").exists()
    assert [argv[2] for argv in fake.commands] == ["venv", "pip"]


async def test_ensure_demo_venv_rebuilds_when_the_python_does_not_run(
    make_workspace: MakeWorkspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = make_workspace("broken")
    root = workspace.main.root.parent
    (root / "demo-venv" / "bin").mkdir(parents=True)
    (root / "demo-venv" / "bin" / "python").write_text("")
    (root / "demo-venv" / "oversight-stamp").write_text("pytest==9.1.1\n")
    fake = FakeUv(root, probe_ok=False)
    monkeypatch.setattr(workspace_core, "run_process", fake)
    await workspace.ensure_demo_venv()
    assert len(fake.commands) == 3


@pytest.mark.parametrize("damage", ["no interpreter", "dangling symlink"])
async def test_ensure_demo_venv_rebuilds_when_the_python_is_missing_or_a_broken_link(
    make_workspace: MakeWorkspace, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    workspace = make_workspace("missing")
    root = workspace.main.root.parent
    (root / "demo-venv" / "bin").mkdir(parents=True)
    (root / "demo-venv" / "oversight-stamp").write_text("pytest==9.1.1\n")
    if damage == "dangling symlink":
        (root / "demo-venv" / "bin" / "python").symlink_to(root / "gone" / "python3.12")
    fake = FakeUv(root)
    monkeypatch.setattr(workspace_core, "run_process", fake)
    await workspace.ensure_demo_venv()
    python = str(root / "demo-venv" / "bin" / "python")
    assert [argv[0] for argv in fake.commands] == [python, "uv", "uv"]
    assert [argv[2] for argv in fake.commands[1:]] == ["venv", "pip"]
    assert (root / "demo-venv" / "oversight-stamp").read_text().strip() == "pytest==9.1.1"


@pytest.mark.parametrize("name", ["UV_INDEX_URL", "UV_DEFAULT_INDEX", "UV_INDEX"])
async def test_a_mirror_chosen_through_the_environment_reaches_uv_and_overrides_do_not(
    make_workspace: MakeWorkspace, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    workspace = make_workspace("mirror")
    root = workspace.main.root.parent
    root.mkdir()
    fake = FakeUv(root)
    monkeypatch.setattr(workspace_core, "run_process", fake)
    for key in [key for key in os.environ if key.startswith("UV_")]:
        monkeypatch.delenv(key)
    monkeypatch.setenv(name, "https://mirror.invalid/simple")
    for denied in ("UV_OVERRIDE", "UV_EXCLUDE", "UV_CONSTRAINT", "UV_EXTRA_INDEX_URL"):
        monkeypatch.setenv(denied, "test-value")
    await workspace.ensure_demo_venv()
    assert len(fake.calls) == 2
    for _, env in fake.calls:
        assert {key: value for key, value in env.items() if key.startswith("UV_")} == {
            name: "https://mirror.invalid/simple"
        }


def test_the_install_passes_the_repositorys_release_age_rule(repo_root: Path) -> None:
    pyproject = tomllib.loads((repo_root / "pyproject.toml").read_text())
    assert pyproject["tool"]["uv"]["exclude-newer"] == workspace_core.EXCLUDE_NEWER


async def test_a_failed_install_leaves_no_stamp(
    make_workspace: MakeWorkspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = make_workspace("failed")
    root = workspace.main.root.parent
    root.mkdir()
    monkeypatch.setattr(workspace_core, "run_process", FakeUv(root, install_ok=False))
    with pytest.raises(WorkspaceError, match="uv failed"):
        await workspace.ensure_demo_venv()
    assert not (root / "demo-venv" / "oversight-stamp").exists()


async def test_redact_strips_runtime_paths(make_workspace: MakeWorkspace) -> None:
    workspace = make_workspace("redact")
    root = str(workspace.main.root.parent)
    text = (
        f"{root}/workspace/shop/mod.py:3: error\n"
        f"{root}/worktrees/agent-12/tests/test_x.py::test_a FAILED\n"
        f"File {root}/demo-venv/lib/python3.12/site-packages/_pytest/x.py\n"
        f"cwd {root}/workspace\ncwd {root}/worktrees/agent-1 done\nroot {root}\nplain text"
    )
    redacted = workspace.redact(text)
    assert root not in redacted
    assert redacted.splitlines() == [
        "shop/mod.py:3: error",
        "tests/test_x.py::test_a FAILED",
        "File .runtime/demo-venv/lib/python3.12/site-packages/_pytest/x.py",
        "cwd ",
        "cwd  done",
        "root .runtime",
        "plain text",
    ]


# --- Running the demo repo's tests ------------------------------------------------------------


async def test_run_tests_on_demo_repo_passes(
    make_workspace: MakeWorkspace,
    demo_python: Path,
    git_out: GitOut,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    repo_root: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    workspace = make_workspace("demo", source=repo_root / "demo-repo", demo_python=demo_python)
    await workspace.acquire_runtime()
    await workspace.reset()
    tree = await workspace.create_worktree("agent-1")
    for target in (workspace.main, tree):
        run = await workspace.run_tests(target, timeout_s=120)
        assert isinstance(run.result, SuiteResult)
        assert run.result.outcome == "passed", run.output
        assert run.result.passed > 0
        assert (run.result.failed, run.result.failed_tests) == (0, [])
        assert str(workspace.main.root.parent) not in run.output
        assert "passed" in run.output
        assert (await git_out(target.root, "status", "--porcelain")).strip() == ""
        assert not list(target.root.rglob("__pycache__"))
        assert not (target.root / ".pytest_cache").exists()
    assert list((workspace.main.root.parent / "tmp").glob("*.jsonl")) == []


CASES = (
    "import pytest\n\n\n"
    "def test_ok() -> None:\n    assert True\n\n\n"
    "def test_bad() -> None:\n    assert 1 == 2\n\n\n"
    "@pytest.mark.skip\ndef test_skipped() -> None:\n    pass\n\n\n"
    "@pytest.mark.xfail\ndef test_xfailed() -> None:\n    assert False\n"
)


async def test_run_tests_reports_failures_and_skips(
    make_workspace: MakeWorkspace,
    demo_python: Path,
    tmp_path: Path,
    write_tree_files: Callable[[Path, dict[str, str]], Path],
) -> None:
    source = write_tree_files(tmp_path / "cases-source", {"tests/test_cases.py": CASES})
    workspace = make_workspace("cases", source=source, demo_python=demo_python)
    await workspace.acquire_runtime()
    await workspace.reset()
    run = await workspace.run_tests(workspace.main, timeout_s=120)
    result = run.result
    assert (result.outcome, result.passed, result.failed, result.skipped) == ("failed", 1, 1, 2)
    assert result.failed_tests == ["tests/test_cases.py::test_bad"]
    assert "assert 1 == 2" in run.output


async def test_run_tests_timeout_reports_partial_counts(
    make_workspace: MakeWorkspace,
    demo_python: Path,
    tmp_path: Path,
    write_tree_files: Callable[[Path, dict[str, str]], Path],
    wait_gone: Callable[[int], None],
) -> None:
    pid_file = tmp_path / "hang.pid"
    hang = (
        "import os, time\n\n\ndef test_hang() -> None:\n"
        f"    open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n    time.sleep(600)\n"
    )
    source = write_tree_files(
        tmp_path / "hang-source",
        {
            "tests/test_a_first.py": "def test_first() -> None:\n    assert True\n",
            "tests/test_b_hang.py": hang,
        },
    )
    workspace = make_workspace("hang", source=source, demo_python=demo_python)
    await workspace.acquire_runtime()
    await workspace.reset()
    run = await workspace.run_tests(workspace.main, timeout_s=8)
    assert run.result.outcome == "timeout"
    assert (run.result.passed, run.result.failed) == (1, 0)
    wait_gone(int(pid_file.read_text()))
    assert list((workspace.main.root.parent / "tmp").glob("*.jsonl")) == []
