"""Filesystem refusals happen before Git, including ignored and mixed-case entries."""

import asyncio
import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from oversight.workspace import (
    LockOrderError,
    ProcessResult,
    SuiteInterrupted,
    Workspace,
    WorkspaceError,
    core,
    integrity,
    test_tree,
)
from oversight.workspace.integrity import GitIdentity, IntegrityError, scan_nested_git, skips_scan
from oversight.workspace.runner import run_process

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("data", [b"x" * (256 * 1024 + 1), b"\xff"])
async def test_registration_wraps_invalid_pointer(workspace: Workspace, data: bytes) -> None:
    tree = await workspace.create_worktree("agent-1")
    (tree.root / ".git").write_bytes(data)
    with pytest.raises(IntegrityError, match="unreadable Git registration"):
        tree.git_identity.verify_registration()


@pytest.mark.parametrize("name", [".git", ".GiT"])
@pytest.mark.parametrize("kind", ["file", "directory", "file_link", "directory_link"])
@pytest.mark.parametrize("at_root", [False, True])
async def test_scan_entries(
    workspace: Workspace, name: str, kind: str, at_root: bool, tmp_path: Path
) -> None:
    # A valid registered root entry is the only exemption; exercise it separately below.
    root = tmp_path / "tree"
    root.mkdir()
    parent = root if at_root else root / "ignored"
    parent.mkdir(exist_ok=True)
    target = parent / name
    if kind == "directory":
        target.mkdir()
    elif kind == "file":
        target.write_text("gitdir: /untrusted\n")
    else:
        target.symlink_to(tmp_path / "absent", target_is_directory=kind == "directory_link")
    identity = GitIdentity(root, root / ".git", root / ".git")
    if at_root and name == ".git" and kind == "directory":
        scan_nested_git(identity, time.monotonic() + 1)
    else:
        with pytest.raises(IntegrityError, match="code=integrity_invalid") as failure:
            scan_nested_git(identity, time.monotonic() + 1)
        assert name in str(failure.value)


async def test_scan_never_follows_other_links(workspace: Workspace, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    (outside / ".git").mkdir(parents=True)
    (workspace.main.root / "linked").symlink_to(outside, target_is_directory=True)
    scan_nested_git(workspace.main.git_identity, time.monotonic() + 1)


@pytest.mark.parametrize("failure", ["read", "deadline", "nested"])
async def test_scan_failure_prevents_launch(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    spawn = AsyncMock()
    monkeypatch.setattr(core, "run_process", spawn)
    if failure == "read":
        monkeypatch.setattr(os, "scandir", lambda _: (_ for _ in ()).throw(PermissionError()))
    elif failure == "nested":
        (workspace.main.root / "sub").mkdir()
        (workspace.main.root / "sub" / ".GiT").write_text("")
    async with workspace.locks(main=True):
        with pytest.raises(IntegrityError) as caught:
            await workspace.git(
                workspace.main, "add", "-A", timeout_s=0 if failure == "deadline" else 1
            )
    assert caught.value.code == ("git_failed" if failure == "deadline" else "integrity_invalid")
    spawn.assert_not_awaited()


async def test_reason_escapes_path_controls_and_delimiters(workspace: Workspace) -> None:
    name = "evil\n; code=forged\t"
    nested = workspace.main.root / name / ".git"
    nested.mkdir(parents=True)
    async with workspace.locks(main=True):
        with pytest.raises(IntegrityError) as caught:
            await workspace.git(workspace.main, "status")
    message = str(caught.value)
    assert "\n" not in message and "\t" not in message
    assert message.count("; code=") == 0 and "\\u003b" in message
    assert len(str(IntegrityError("nested Git entry", "x" * 2000))) < 350


COMMANDS = [
    ("add", "-A"),
    ("status", "--porcelain"),
    ("diff",),
    ("diff-files",),
    ("diff-index", "HEAD"),
    ("merge", "--abort"),
    ("commit", "--dry-run"),
    ("read-tree", "HEAD"),
    ("restore", "--worktree", "."),
    ("reset", "--hard"),
    ("future-command",),
    ("cat-file", "--filters", "HEAD:shop/mod.py"),
    ("ls-files", "--modified"),
    ("worktree", "prune"),
]
SAFE = [
    ("rev-parse", "HEAD"),
    ("rev-list", "--parents", "-n", "1", "HEAD"),
    ("merge-base", "HEAD", "HEAD"),
    ("write-tree",),
    ("ls-files", "-s", "-z"),
    ("cat-file", "-p", "HEAD"),
    ("cat-file", "-e", "HEAD"),
    ("cat-file", "-s", "HEAD"),
    ("cat-file", "-t", "HEAD"),
]


@pytest.mark.parametrize("args", COMMANDS + SAFE)
async def test_allowlist_scans_once_and_defaults_to_scan(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, args: tuple[str, ...]
) -> None:
    scans: list[GitIdentity] = []
    real = integrity.scan_nested_git

    def scan(identity: GitIdentity, deadline: float) -> None:
        scans.append(identity)
        real(identity, deadline)

    spawn = AsyncMock(return_value=ProcessResult(0, "", "", False, False, 1))
    monkeypatch.setattr(integrity, "scan_nested_git", scan)
    monkeypatch.setattr(core, "run_process", spawn)
    async with workspace.locks(main=True):
        await workspace.git(workspace.main, *args)
    assert len(scans) == (0 if args in SAFE else 1)
    assert skips_scan(args) == (args in SAFE)
    spawn.assert_awaited_once()


async def test_registered_pins_ignore_pointer_config_and_parent_environment(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tree = await workspace.create_worktree("agent-1")
    pre = await tree.head()
    # An attacker redirects both discovery paths. Pins use the server's original registration.
    (tree.root / ".git").write_text(f"gitdir: {tmp_path}/foreign\n")
    config = workspace.main.root / ".git" / "config"
    with config.open("a") as handle:
        handle.write(f"\n[core]\nworktree = {tmp_path}/foreign\n")
    for key in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.setenv(key, str(tmp_path / "foreign"))
    assert await tree.head() == pre
    async with workspace.locks(agent_id="agent-1"):
        assert (await workspace.git_ok(tree, "rev-parse", "--show-toplevel")).strip() == str(
            tree.root
        )
    env = workspace._git_env(tree, None, None)  # pyright: ignore[reportPrivateUsage]
    assert env["GIT_DIR"] == str(tree.git_identity.directory)
    assert env["GIT_COMMON_DIR"] == str(workspace.main.root / ".git")
    assert env["GIT_WORK_TREE"] == str(tree.root) and env["GIT_NO_REPLACE_OBJECTS"] == "1"


async def test_capture_shares_pins_scans_once_and_only_overrides_index(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = await workspace.create_worktree("agent-1")
    scans: list[GitIdentity] = []
    calls: list[dict[str, str]] = []
    real_scan, real_spawn = scan_nested_git, run_process

    def scan(identity: GitIdentity, deadline: float) -> None:
        scans.append(identity)
        real_scan(identity, deadline)

    async def spawn(*args: Any, **kwargs: Any) -> ProcessResult:
        calls.append(kwargs["env"])
        return await real_spawn(*args, **kwargs)

    monkeypatch.setattr(test_tree, "scan_nested_git", scan)
    monkeypatch.setattr(test_tree, "run_process", spawn)
    async with workspace.locks(main=True, agent_id="agent-1"), workspace.git_lease():
        await test_tree.capture_test_tree(
            tree.root,
            workspace.tmp_dir,
            env=workspace._git_env(tree, None, None),
            flags=core.GIT_FLAGS,
            timeout_s=5,  # pyright: ignore[reportPrivateUsage]
        )
    assert len(scans) == 1 and len(calls) == 6
    baseline = workspace._git_env(tree, None, None)  # pyright: ignore[reportPrivateUsage]
    for env in calls:
        assert {key: value for key, value in env.items() if key != "GIT_INDEX_FILE"} == baseline
        assert env["GIT_INDEX_FILE"].startswith(str(workspace.tmp_dir / "suite-index-"))


async def test_suite_lease_blocks_other_git_through_cancel_cleanup(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = await workspace.create_worktree("agent-1")
    second = await workspace.create_worktree("agent-2")
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    attempted, launched = asyncio.Event(), asyncio.Event()
    real = run_process

    async def suite(*args: Any, **kwargs: Any) -> ProcessResult:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
        raise AssertionError("unreachable")

    async def server(*args: Any, **kwargs: Any) -> ProcessResult:
        launched.set()
        return await real(*args, **kwargs)

    async def git() -> None:
        async with workspace.locks(agent_id="agent-2"):
            attempted.set()
            await workspace.git(second, "rev-parse", "HEAD")

    monkeypatch.setattr(core, "run_process", server)
    run = asyncio.create_task(workspace.run_tests(first, timeout_s=10, spawn=suite))
    await entered.wait()
    query = asyncio.create_task(git())
    try:
        await attempted.wait()
        assert not launched.is_set()
        run.cancel()
        await cleaning.wait()
        assert not launched.is_set()
    finally:
        release.set()
        with pytest.raises(SuiteInterrupted):
            await run
        await query
    assert launched.is_set() and workspace.operations.idle


async def test_lease_order_and_reentrancy(workspace: Workspace) -> None:
    async with workspace.locks(main=True), workspace.git_lease(), workspace.git_lease():
        await workspace.git(workspace.main, "rev-parse", "HEAD")
        with pytest.raises(LockOrderError, match="before the Git lease"):
            async with workspace.locks(agent_id="agent-1"):
                pytest.fail("worktree lock after lease")
    async with workspace.locks(agent_id="agent-1"):
        with pytest.raises(LockOrderError):
            await workspace.run_tests(workspace.main, timeout_s=1)
    assert workspace.operations.idle


@pytest.mark.parametrize("main", [False, True])
@pytest.mark.parametrize("replacement", ["wrong_kind", "wrong_pointer", "missing"])
async def test_root_exemption_requires_registered_kind_and_pointer(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, main: bool, replacement: str
) -> None:
    tree = workspace.main if main else await workspace.create_worktree("agent-1")
    entry = tree.root / ".git"
    saved = tree.root.parent / f"saved-git-{tree.root.name}"
    entry.rename(saved)
    try:
        if replacement == "wrong_kind":
            if main:
                entry.write_text(f"gitdir: {saved}\n")
            else:
                entry.mkdir()
        elif replacement == "wrong_pointer":
            entry.write_text("gitdir: /unregistered\n")
        spawn = AsyncMock()
        monkeypatch.setattr(core, "run_process", spawn)
        async with workspace.locks(main=main, agent_id=tree.agent_id):
            with pytest.raises(IntegrityError, match=r"\.git"):
                await workspace.git(tree, "status", "--porcelain")
        spawn.assert_not_awaited()
    finally:
        if entry.is_dir():
            entry.rmdir()
        else:
            entry.unlink(missing_ok=True)
        saved.rename(entry)


async def test_reset_drops_stale_registered_identities(make_workspace: Any) -> None:
    workspace = make_workspace()
    stale = workspace.runtime_root / "worktrees" / "agent-1"
    workspace.git_identities[stale] = GitIdentity(stale, stale / ".git", stale / ".git")
    await workspace.acquire_runtime()
    await workspace.reset()
    assert workspace.git_identities == {workspace.main.root: workspace.main.git_identity}
    async with workspace.locks(main=True):
        with pytest.raises(WorkspaceError, match="requires a registered tree"):
            await workspace.git(workspace.main, "worktree", "remove", str(stale))


async def test_scan_deadline_expires_during_walk_before_git(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = os.scandir

    def slow(path: Path) -> Any:
        time.sleep(0.03)
        return real(path)

    spawn = AsyncMock()
    monkeypatch.setattr(os, "scandir", slow)
    monkeypatch.setattr(core, "run_process", spawn)
    async with workspace.locks(main=True):
        with pytest.raises(IntegrityError, match="code=git_failed"):
            await workspace.git(workspace.main, "add", "-A", timeout_s=0.01)
    spawn.assert_not_awaited()


async def test_lease_wait_deadline_prevents_git(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = await workspace.create_worktree("agent-1")
    holding, release = asyncio.Event(), asyncio.Event()

    async def hold() -> None:
        async with workspace.locks(main=True), workspace.git_lease():
            holding.set()
            await release.wait()

    task = asyncio.create_task(hold())
    await holding.wait()
    spawn = AsyncMock()
    monkeypatch.setattr(core, "run_process", spawn)
    try:
        async with workspace.locks(agent_id="agent-1"):
            with pytest.raises(IntegrityError, match="code=git_failed"):
                await workspace.git(tree, "rev-parse", "HEAD", timeout_s=0.01)
            with pytest.raises(IntegrityError, match="code=git_failed"):
                await workspace.veto_index(tree, timeout_s=0.01)
    finally:
        release.set()
        await task
    spawn.assert_not_awaited()
    assert workspace.operations.idle
