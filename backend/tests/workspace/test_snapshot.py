"""Raw snapshots retain the real index and refuse uncertain metadata without Git."""

import os
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from oversight.workspace import Workspace, core, test_tree
from oversight.workspace.integrity import IntegrityError
from oversight.workspace.metadata_io import MAX_FILE, Budget, read_entry, replace_file
from oversight.workspace.snapshot import MetadataSnapshot, finish_snapshot, take_snapshot

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("absent", [False, True])
async def test_capture_keeps_the_real_index_separate(workspace: Workspace, absent: bool) -> None:
    tree = await workspace.create_worktree("agent-1")
    index = tree.git_identity.directory / "index"
    if absent:
        index.unlink()
    before = index.read_bytes() if index.exists() else None
    snapshot = take_snapshot(tree.git_identity, workspace.integrity, 5)
    async with workspace.locks(main=True, agent_id=tree.agent_id), workspace.git_lease():
        await test_tree.capture_test_tree(
            tree.root,
            workspace.tmp_dir,
            env=workspace._git_env(tree, None, None),
            flags=core.GIT_FLAGS,
            timeout_s=5,
        )
        finish_snapshot(snapshot, workspace.integrity)
    assert (index.read_bytes() if index.exists() else None) == before
    assert not workspace.integrity.rules_suspended


@pytest.mark.parametrize("kind", ["symlink", "parent_link", "fifo", "oversize", "deadline"])
async def test_unsafe_snapshot_never_spawns(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str
) -> None:
    info = workspace.main.git_identity.common / "info"
    target = info / "test-input"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "safe").write_text("untouched")
    timeout = 5
    if kind == "symlink":
        target.symlink_to(outside / "safe")
    elif kind == "parent_link":
        info.rename(info.with_name("saved-info"))
        info.symlink_to(outside, target_is_directory=True)
    elif kind == "fifo":
        os.mkfifo(target)
    elif kind == "oversize":
        with target.open("wb") as handle:
            handle.truncate(MAX_FILE + 1)
    else:
        timeout = 0
    spawn = AsyncMock()
    monkeypatch.setattr(core, "run_process", spawn)
    monkeypatch.setattr(test_tree, "run_process", spawn)
    with pytest.raises(IntegrityError, match="code=main_suspect"):
        await workspace.run_tests(workspace.main, timeout_s=timeout, spawn=spawn)
    spawn.assert_not_awaited()
    assert workspace.integrity.rules_suspended and workspace.integrity.main_suspect
    assert (outside / "safe").read_text() == "untouched"


async def test_ref_compare_and_swap_does_not_overwrite_new_writer(workspace: Workspace) -> None:
    path = workspace.main.git_identity.common / "refs/heads/main"
    deadline = time.monotonic() + 5
    previous = read_entry(path, Budget(deadline))
    path.write_bytes(b"1" * 40 + b"\n")
    with pytest.raises(IntegrityError, match="compare-and-swap failed"):
        replace_file(path, previous, previous, deadline, cas=True)
    assert path.read_bytes() == b"1" * 40 + b"\n"
    assert not path.with_suffix(".lock").exists()


@pytest.mark.parametrize("lock", ["index.lock", "HEAD.lock", "refs/heads/main.lock"])
async def test_unknown_lock_is_never_removed(workspace: Workspace, lock: str) -> None:
    snapshot = take_snapshot(workspace.main.git_identity, workspace.integrity, 5)
    path = workspace.main.git_identity.common / lock
    path.write_bytes(b"unknown owner")
    with pytest.raises(IntegrityError, match="code=main_suspect"):
        finish_snapshot(snapshot, workspace.integrity)
    assert path.read_bytes() == b"unknown owner"
    assert workspace.integrity.rules_suspended and workspace.integrity.main_suspect


async def test_snapshot_api_does_not_invoke_git(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawn = AsyncMock()
    monkeypatch.setattr(core, "run_process", spawn)
    snapshot = MetadataSnapshot.take(workspace.main.git_identity, time.monotonic() + 5)
    finish_snapshot(snapshot, workspace.integrity)
    spawn.assert_not_awaited()


@pytest.mark.parametrize("growing", [False, True])
async def test_large_working_file_is_a_working_file_limit(workspace, monkeypatch, growing):
    from oversight.workspace import metadata_io
    from oversight.workspace.suite_files import file_stamp

    path = workspace.main.root / "large.dat"
    path.write_bytes(b"x" * 33)
    monkeypatch.setattr(metadata_io, "MAX_FILE", 32)
    if growing:
        # The stat check passed, but the bounded descriptor read sees a growing file.
        original = os.stat

        def small(name, *args, **kwargs):
            result = original(name, *args, **kwargs)
            if name == "large.dat":
                fields = list(result)
                fields[6] = 0
                return os.stat_result(fields)
            return result

        monkeypatch.setattr(os, "stat", small)
    with pytest.raises(
        IntegrityError, match="oversized working file: read limit exceeded"
    ) as error:
        file_stamp(workspace.main.root)
    assert "Git metadata" not in str(error.value)
    assert not workspace.integrity.rules_suspended and not workspace.integrity.main_suspect


@pytest.mark.parametrize("kind", ["Git metadata", "working file"])
@pytest.mark.parametrize("failure", ["linked", "open", "read"])
async def test_read_failure_names_its_kind(tmp_path, monkeypatch, kind, failure):
    from oversight.workspace import metadata_io

    path = tmp_path / "input"
    path.write_bytes(b"unchanged")
    messages = {
        "linked": f"nonregular or linked {kind}",
        "open": f"{kind} changed during open",
        "read": f"{kind} changed during read",
    }
    if failure == "linked":
        os.link(path, tmp_path / "alias")
    else:
        original = os.fstat
        calls = 0

        def changed(fd):
            nonlocal calls
            calls += 1
            info = original(fd)
            if (failure == "open" and calls == 1) or (failure == "read" and calls == 2):
                fields = list(info)
                fields[1 if failure == "open" else 6] += 1
                return os.stat_result(fields)
            return info

        monkeypatch.setattr(os, "fstat", changed)
    budget = Budget(time.monotonic() + 5)
    with metadata_io.directory(tmp_path, budget.deadline) as fd:
        kwargs = {} if kind == "Git metadata" else {"kind": kind}
        with pytest.raises(IntegrityError, match=messages[failure]):
            metadata_io.read_at(fd, path.name, path, budget, **kwargs)
    assert path.read_bytes() == b"unchanged"


@pytest.mark.parametrize(
    "value, expected",
    [
        ('"hooks with spaces"', "hooks with spaces"),
        (r'"C:\Users\h"', r"C:\Users\h"),
        (r'"hooks\\tail"', "hooks\\tail"),
        (r'"hooks\"tail"', 'hooks"tail'),
    ],
)
async def test_quoted_hooks_path_retains_literal_backslashes(workspace, value, expected):
    hooks = workspace.main.root / expected
    hooks.mkdir()
    hook = hooks / "pre-commit"
    hook.write_text("original hook")
    config = workspace.main.git_identity.common / "config"
    with config.open("a") as handle:
        handle.write(f"[core]\n hooksPath = {value}\n")
    snapshot = take_snapshot(workspace.main.git_identity, workspace.integrity, 5)
    assert snapshot.entries[hook].data == b"original hook"
    assert not workspace.integrity.main_suspect
