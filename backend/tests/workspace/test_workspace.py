"""Workspace lifecycle against real git in temporary repositories. None of it touches the
repository this project lives in, or its .runtime."""

import asyncio
import stat
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from backend.tests.workspace.workspace_helpers import GitOut, MakeWorkspace, count_spawns
from backend.tests.workspace.workspace_helpers import pytestmark as pytestmark

from oversight.workspace import (
    UnsafeDeletion,
    Workspace,
    WorkspaceBusy,
    WorkspaceError,
    content_hash,
    run_process,
)
from oversight.workspace import core as workspace_core
from oversight.workspace import files as workspace_files

INITIAL_DATE = "2026-10-26T00:00:00+00:00"


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
    author, committer, authored, committed, message = log.strip().split("|")
    assert author == committer == "Oversight <oversight@oversight.invalid>"
    assert datetime.fromisoformat(authored) == datetime.fromisoformat(INITIAL_DATE)
    assert datetime.fromisoformat(committed) == datetime.fromisoformat(INITIAL_DATE)
    assert message == "Initial workspace"
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
