"""Real filters, metadata traps and incomplete raw queries cannot cross C2a's guards."""

import shlex
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from git_attack_helpers import malicious_sub, raw_git

from oversight.workspace import (
    ProcessResult,
    Workspace,
    WorkspaceError,
    core,
    run_process,
    test_tree,
)
from oversight.workspace.integrity import IntegrityError, veto_index

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "args",
    [
        ("add", "-A"),
        ("diff-files",),
        ("diff-index", "HEAD"),
        ("status", "--porcelain"),
        ("diff",),
        ("commit", "-qm", "Unsafe"),
    ],
)
async def test_nested_filter_is_blocked_before_every_tree_git(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, args: tuple[str, ...], tmp_path: Path
) -> None:
    sentinel = tmp_path / "filter-ran"
    await malicious_sub(workspace.main.root, sentinel, parent_gitlink=True)
    spawn = AsyncMock(wraps=run_process)
    monkeypatch.setattr(core, "run_process", spawn)
    async with workspace.locks(main=True):
        with pytest.raises(IntegrityError, match=r"sub/\.git"):
            await workspace.git(workspace.main, *args)
    spawn.assert_not_awaited()
    assert not sentinel.exists()


@pytest.mark.parametrize("command", ["read-tree", "ls-files", "add", "write-tree", "rev-parse"])
@pytest.mark.parametrize("stream", ["stdout", "stderr"])
async def test_capture_refuses_truncation_without_repeating_effects(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, command: str, stream: str
) -> None:
    calls: list[str] = []
    real = run_process

    async def spawn(argv: list[str], **kwargs: Any) -> ProcessResult:
        result = await real(argv, **kwargs)
        verb = argv[len(core.GIT_FLAGS) + 1]
        calls.append(verb)
        return replace(result, truncated=True, **{stream: "partial"}) if verb == command else result

    monkeypatch.setattr(test_tree, "run_process", spawn)
    async with workspace.locks(main=True), workspace.git_lease():
        with pytest.raises(WorkspaceError, match=f"git {command} failed: output was truncated"):
            await test_tree.capture_test_tree(
                workspace.main.root,
                workspace.tmp_dir,
                env=workspace._git_env(workspace.main, None, None),  # pyright: ignore[reportPrivateUsage]
                flags=core.GIT_FLAGS,
                timeout_s=5,
            )
    assert calls.count(command) == 1 and calls[-1] == command
    assert not list(workspace.tmp_dir.glob("suite-index-*"))


async def test_raw_git_refuses_truncation(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawn = AsyncMock(return_value=ProcessResult(0, "partial", "", False, True, 1))
    monkeypatch.setattr(core, "run_process", spawn)
    async with workspace.locks(main=True):
        with pytest.raises(WorkspaceError, match="output was truncated"):
            await workspace.git(workspace.main, "write-tree")
    spawn.assert_awaited_once()


async def test_fsmonitor_and_hooks_are_disabled_in_git_and_capture(
    workspace: Workspace, tmp_path: Path
) -> None:
    sentinel = tmp_path / "executed"
    script = tmp_path / "trap"
    script.write_text(f"#!/bin/sh\ntouch {shlex.quote(str(sentinel))}\nexit 0\n")
    script.chmod(0o755)
    root = workspace.main.root
    await raw_git(root, "config", "core.fsmonitor", str(script))
    await raw_git(root, "config", "core.hooksPath", str(tmp_path / "hooks"))
    (tmp_path / "hooks").mkdir()
    (tmp_path / "hooks" / "pre-commit").symlink_to(script)
    (root / "extra.py").write_text("# new\n")
    await workspace.main.commit_all("Guard hooks", workspace.user_author())
    async with workspace.locks(main=True), workspace.git_lease():
        await test_tree.capture_test_tree(
            root,
            workspace.tmp_dir,
            env=workspace._git_env(workspace.main, None, None),  # pyright: ignore[reportPrivateUsage]
            flags=core.GIT_FLAGS,
            timeout_s=5,
        )
    assert not sentinel.exists()


async def test_replace_refs_cannot_change_captured_tree(workspace: Workspace) -> None:
    root = workspace.main.root
    before = await workspace.main.head()
    original = (root / "shop/mod.py").read_text()
    (root / "shop/mod.py").write_text("# replacement\n")
    after = await workspace.main.commit_all("Make replacement", workspace.user_author())
    assert after is not None
    await raw_git(root, "replace", before, after)
    async with workspace.locks(main=True):
        text = await workspace.git_ok(workspace.main, "show", f"{before}:shop/mod.py")
    assert text == original


@pytest.mark.parametrize(
    "attack",
    [
        "pointer",
        "config",
        "fsmonitor",
        "hooks",
        "alternates",
        "shallow",
        "replace",
        "refs",
        "index",
        "MERGE_HEAD",
        "abort",
    ],
)
async def test_nested_guard_precedes_tree_git_even_with_poisoned_metadata(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, attack: str
) -> None:
    tree = await workspace.create_worktree("agent-1")
    metadata, common = tree.git_identity.directory, tree.git_identity.common
    sentinel = tmp_path / "filter-ran"
    await malicious_sub(tree.root, sentinel)
    targets = {
        "pointer": tree.root / ".git",
        "config": common / "config",
        "fsmonitor": common / "config",
        "hooks": common / "hooks" / "pre-commit",
        "alternates": common / "objects/info/alternates",
        "shallow": common / "shallow",
        "replace": common / "refs/replace" / ("a" * 40),
        "refs": common / "refs/heads/main",
        "index": metadata / "index",
        "MERGE_HEAD": metadata / "MERGE_HEAD",
        "abort": metadata / "HEAD.lock",
    }
    target = targets[attack]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"attacker-controlled\n")
    before = target.read_bytes()
    spawn = AsyncMock(wraps=run_process)
    monkeypatch.setattr(core, "run_process", spawn)
    async with workspace.locks(agent_id="agent-1"):
        with pytest.raises(IntegrityError, match=r"sub/\.git"):
            await workspace.git(tree, "merge", "--abort" if attack == "abort" else "HEAD")
    spawn.assert_not_awaited()
    assert target.read_bytes() == before and not sentinel.exists()


@pytest.mark.parametrize("location", ["index.lock", "HEAD.lock", "refs/heads/main.lock"])
async def test_unknown_git_locks_are_never_removed(workspace: Workspace, location: str) -> None:
    root = workspace.main.root
    lock = root / ".git" / location
    lock.write_text("unknown owner\n")
    (root / "extra.py").write_text("# dirty\n")
    async with workspace.locks(main=True):
        args = ("add", "-A") if location == "index.lock" else ("commit", "-qam", "Guard")
        with pytest.raises(WorkspaceError):
            await workspace.git_ok(workspace.main, *args)
    assert lock.read_text() == "unknown owner\n"


@pytest.mark.parametrize("path", [".gitmodules", "nested/.GiTmodules"])
async def test_modules_veto_prevents_staging(workspace: Workspace, path: str) -> None:
    target = workspace.main.root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("[submodule]\n")
    async with workspace.locks(main=True):
        index = await workspace.git_ok(workspace.main, "write-tree")
        with pytest.raises(IntegrityError, match="Git modules are forbidden"):
            await workspace.veto_index(workspace.main)
        assert await workspace.git_ok(workspace.main, "write-tree") == index


async def test_gitlink_veto_uses_every_nul_delimited_stage() -> None:
    for stage in (0, 1, 2, 3):
        with pytest.raises(IntegrityError, match="Git links are forbidden"):
            veto_index(f"100644 {'a' * 40} 0\tordinary\0" + f"160000 {'b' * 40} {stage}\tsub\0")
    with pytest.raises(IntegrityError, match="code=git_failed"):
        veto_index(f"100644 {'a' * 40} 0\tpartial")


@pytest.mark.parametrize("committed", [False, True])
async def test_preexisting_gitlink_never_reaches_add(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, committed: bool
) -> None:
    root = workspace.main.root
    head = await workspace.main.head()
    await raw_git(root, "update-index", "--add", "--cacheinfo", f"160000,{head},sub")
    if committed:
        await raw_git(root, "commit", "-qm", "Attacker gitlink")
    spawn = AsyncMock(wraps=run_process)
    monkeypatch.setattr(test_tree, "run_process", spawn)
    async with workspace.locks(main=True), workspace.git_lease():
        with pytest.raises(IntegrityError, match="Git links are forbidden"):
            if committed:
                await test_tree.capture_test_tree(
                    root,
                    workspace.tmp_dir,
                    env=workspace._git_env(workspace.main, None, None),  # pyright: ignore[reportPrivateUsage]
                    flags=core.GIT_FLAGS,
                    timeout_s=5,
                )
            else:
                await workspace.veto_index(workspace.main)
    assert all(call.args[0][len(core.GIT_FLAGS) + 1] != "add" for call in spawn.call_args_list)


async def test_second_veto_catches_index_changed_during_staging(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = workspace.main.root
    (root / "extra.py").write_text("# dirty\n")
    pre = await workspace.main.head()
    real = workspace.git

    async def git(tree: Any, *args: str, **kwargs: Any) -> ProcessResult:
        result = await real(tree, *args, **kwargs)
        if args[0] == "add":
            await raw_git(root, "update-index", "--add", "--cacheinfo", f"160000,{pre},sub")
        assert args[0] != "commit", "a gitlink must never reach commit"
        return result

    monkeypatch.setattr(workspace, "git", git)
    with pytest.raises(IntegrityError, match="Git links are forbidden"):
        await workspace.main.commit_all("Guard", workspace.user_author())
    assert await workspace.main.head() == pre


@pytest.mark.parametrize("force", [False, True])
async def test_worktree_removal_scans_the_registered_target(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, force: bool
) -> None:
    tree = await workspace.create_worktree("agent-1")
    sentinel = tmp_path / "filter-ran"
    await malicious_sub(tree.root, sentinel, parent_gitlink=True)
    spawn = AsyncMock(wraps=run_process)
    monkeypatch.setattr(core, "run_process", spawn)
    async with workspace.locks(main=True, agent_id="agent-1"):
        with pytest.raises(IntegrityError, match=r"sub/\.git"):
            await workspace.git(
                workspace.main,
                "worktree",
                "remove",
                *(["--force"] if force else []),
                str(tree.root),
            )
    spawn.assert_not_awaited()
    assert tree.root.exists() and not sentinel.exists()
