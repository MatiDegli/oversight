"""Raw snapshots retain the real index and refuse uncertain metadata without Git."""

import os
import sys
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
        if sys.platform == "win32":
            pytest.skip("FIFO is explicitly POSIX")
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


@pytest.mark.parametrize("nt", [False, True])
async def test_ref_compare_and_swap_does_not_overwrite_new_writer(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, nt: bool
) -> None:
    if nt:
        force_nt(monkeypatch)
    path = workspace.main.git_identity.common / "refs/heads/main"
    deadline = time.monotonic() + 60
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
    snapshot = MetadataSnapshot.take(workspace.main.git_identity, time.monotonic() + 60)
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


@pytest.mark.parametrize("nt", [False, True])
@pytest.mark.parametrize("kind", ["Git metadata", "working file"])
@pytest.mark.parametrize("failure", ["linked", "open", "read"])
async def test_read_failure_names_its_kind(tmp_path, monkeypatch, kind, failure, nt):
    if nt:
        force_nt(monkeypatch)
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
    budget = Budget(time.monotonic() + 60)
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


def force_nt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Select only W2b's typed boundaries; leave the host OS and subprocess backend intact."""
    from types import SimpleNamespace

    from oversight.workspace import metadata_io, snapshot, suite_files, tree

    for module in (metadata_io, snapshot, suite_files, tree):
        monkeypatch.setattr(module, "sys", SimpleNamespace(platform="win32"))


def reparse_at(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    from types import SimpleNamespace

    original = Path.lstat

    def injected(self: Path):
        info = original(self)
        if self == path:
            fields = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
            return SimpleNamespace(**(fields | {"st_file_attributes": 0x400}))
        return info

    monkeypatch.setattr(Path, "lstat", injected)


@pytest.mark.parametrize("site", ["parent", "leaf", "hardlink"])
async def test_nt_unsafe_snapshot_never_spawns(workspace, monkeypatch, tmp_path, site):
    force_nt(monkeypatch)
    info = workspace.main.git_identity.common / "info"
    leaf = info / "input"
    leaf.write_bytes(b"untouched")
    if site == "hardlink":
        os.link(leaf, tmp_path / "alias")
    else:
        reparse_at(monkeypatch, info if site == "parent" else leaf)
    spawn = AsyncMock()
    monkeypatch.setattr(core, "run_process", spawn)
    monkeypatch.setattr(test_tree, "run_process", spawn)
    with pytest.raises(IntegrityError, match="code=main_suspect"):
        await workspace.run_tests(workspace.main, timeout_s=5, spawn=spawn)
    spawn.assert_not_awaited()
    assert workspace.integrity.main_suspect and workspace.integrity.rules_suspended
    assert leaf.read_bytes() == b"untouched"


@pytest.mark.parametrize("failure", ["file", "index", "total", "entries", "deadline"])
def test_nt_metadata_budgets(tmp_path, monkeypatch, failure):
    from oversight.workspace import metadata_io as module

    force_nt(monkeypatch)
    path = tmp_path / ("index" if failure == "index" else "input")
    path.write_bytes(b"12345")
    monkeypatch.setattr(module, "MAX_FILE", 4)
    monkeypatch.setattr(module, "MAX_INDEX", 6)
    deadline = time.monotonic() + 5
    if failure == "index":
        entry = read_entry(path, Budget(deadline))
        assert entry is not None and entry.data == b"12345"
        path.write_bytes(b"1234567")
    if failure in {"total", "entries"}:
        path.write_bytes(b"1234")
        monkeypatch.setattr(module, "MAX_TOTAL" if failure == "total" else "MAX_ENTRIES", 0)
    if failure == "deadline":
        deadline = time.monotonic() - 1
    with pytest.raises(IntegrityError):
        read_entry(path, Budget(deadline))


async def test_nt_binary_cas_and_exclusive_lock(tmp_path, monkeypatch):
    from oversight.workspace.metadata_io import Entry

    force_nt(monkeypatch)
    path = tmp_path / "index"
    path.write_bytes(b"old\r\n\x1a\x00")
    real_open = os.open
    binary = 0x8000  # Linux ignores this bit; assert that the NT boundary supplies it.
    monkeypatch.setattr(os, "O_BINARY", binary, raising=False)
    calls = []

    def opened(name, flags, mode=0o777, **kwargs):
        assert flags & binary and not kwargs and Path(name) != tmp_path
        calls.append(flags)
        return real_open(name, flags & ~binary, mode)

    monkeypatch.setattr(os, "open", opened)
    deadline = time.monotonic() + 60
    before = read_entry(path, Budget(deadline))
    assert before is not None
    wanted = Entry(before.mode, b"new\r\n\x1a\x00")
    replace_file(path, before, wanted, deadline, cas=True)
    assert path.read_bytes() == wanted.data
    assert any(flags & os.O_EXCL for flags in calls)
    lock = tmp_path / "index.lock"
    lock.write_bytes(b"other owner")
    with pytest.raises(FileExistsError):
        replace_file(path, wanted, before, deadline, cas=True)
    assert lock.read_bytes() == b"other owner" and path.read_bytes() == wanted.data


@pytest.mark.parametrize("failure", ["stolen", "replace", "cleanup"])
def test_nt_owned_temp_and_retry_exhaustion(tmp_path, monkeypatch, failure):
    from oversight.workspace import metadata_io as module

    force_nt(monkeypatch)
    monkeypatch.setattr("oversight.workspace._platform.time.sleep", lambda _: None)
    path = tmp_path / "HEAD"
    path.write_bytes(b"old")
    deadline = time.monotonic() + 60
    before = read_entry(path, Budget(deadline))
    original = module._same
    calls = []
    lock = tmp_path / "HEAD.lock"

    def compare(fd, target, expected, limit):
        if failure == "stolen":
            lock.rename(tmp_path / "displaced")
            lock.write_bytes(b"another writer")
        if failure == "cleanup":
            raise IntegrityError("CAS refused", "HEAD")
        original(fd, target, expected, limit)

    def denied(*args, **kwargs):
        error = PermissionError("controlled sharing failure")
        error.__dict__["winerror"] = 32
        calls.append(error)
        raise error

    monkeypatch.setattr(module, "_same", compare)
    if failure == "replace":
        monkeypatch.setattr(os, "replace", denied)
    elif failure == "cleanup":
        monkeypatch.setattr(Path, "unlink", denied)
    with pytest.raises(IntegrityError if failure == "stolen" else PermissionError):
        replace_file(path, before, before, deadline, cas=True)
    assert path.read_bytes() == b"old"
    if failure == "stolen":
        assert lock.read_bytes() == b"another writer"
    else:
        assert len(calls) == 7
        assert lock.exists() == (failure == "cleanup")


@pytest.mark.parametrize("value", ["/dev/null", "pinned", "other", "suffix", "relative", "dotdot"])
async def test_nt_hooks_sentinel(workspace, monkeypatch, value):
    force_nt(monkeypatch)
    pinned = workspace.runtime_root / "server.lock/hooks"
    paths = {
        "pinned": str(pinned).replace("/", "\\").upper(),
        "other": str(workspace.runtime_root / "hooks"),
        "suffix": str(workspace.runtime_root / "other/server.lock/hooks"),
        "relative": "server.lock/hooks",
        "dotdot": str(workspace.runtime_root / "other/../server.lock/hooks"),
    }
    original = Path.lstat

    def no_child(self):
        assert not self.is_relative_to(pinned), "must never probe below the sentinel file"
        return original(self)

    monkeypatch.setattr(Path, "lstat", no_child)
    config = workspace.main.git_identity.common / "config"
    with config.open("a") as handle:
        handle.write(f"[core]\n hooksPath = {paths.get(value, value)}\n")
    if value in {"/dev/null", "pinned"}:
        snapshot = take_snapshot(workspace.main.git_identity, workspace.integrity, 60)
        assert workspace.runtime_root / "server.lock" in snapshot.anchors
        finish_snapshot(snapshot, workspace.integrity)
    else:
        with pytest.raises(IntegrityError, match="code=main_suspect"):
            take_snapshot(workspace.main.git_identity, workspace.integrity, 60)


@pytest.mark.parametrize("failure", ["config-case", "lock-case", "sentinel"])
async def test_nt_snapshot_pins_and_admin_names(workspace, monkeypatch, failure):
    force_nt(monkeypatch)
    identity = workspace.main.git_identity
    snapshot: MetadataSnapshot | None = None
    if failure == "config-case":
        config = identity.common / "config"
        config.rename(config.with_name("CONFIG"))
        with config.with_name("CONFIG").open("a") as handle:
            handle.write("[core]\n hooksPath = /untrusted\n")
    elif failure == "lock-case":
        (identity.common / "HEAD.LOCK").write_bytes(b"unknown owner")
    else:
        snapshot = take_snapshot(identity, workspace.integrity, 60)
        sentinel = workspace.runtime_root / "server.lock"
        sentinel.rename(sentinel.with_name("displaced-lock"))
        sentinel.write_bytes(b"different identity")
    with pytest.raises(IntegrityError, match="code=main_suspect"):
        if failure == "sentinel":
            assert snapshot is not None
            finish_snapshot(snapshot, workspace.integrity)
        else:
            take_snapshot(identity, workspace.integrity, 60)
    spawn = AsyncMock()
    monkeypatch.setattr(core, "run_process", spawn)
    with pytest.raises(IntegrityError, match="code=main_suspect"):
        await workspace.main.head()
    spawn.assert_not_awaited()


async def test_nt_delete_and_directory_restoration(workspace, monkeypatch):
    force_nt(monkeypatch)
    identity = workspace.main.git_identity
    snapshot = take_snapshot(identity, workspace.integrity, 60)
    (identity.common / "refs/extra").mkdir()
    (identity.common / "refs/extra/ref").write_bytes(b"1" * 40 + b"\n")
    (identity.common / "info/exclude").unlink()
    (identity.common / "info").rmdir()
    with pytest.raises(IntegrityError, match="rules suspended"):
        finish_snapshot(snapshot, workspace.integrity)
    assert not workspace.integrity.main_suspect
    assert take_snapshot(identity, workspace.integrity, 60).entries == snapshot.entries


@pytest.mark.parametrize("name", [".git", ".GIT", ".g\u0131t"])
def test_nt_authorized_metadata_roots(tmp_path, monkeypatch, name):
    force_nt(monkeypatch)
    admin = tmp_path / name
    admin.mkdir()
    (admin / "index").write_bytes(b"binary\r\n\x1a\x00")
    entry = read_entry(admin / "index", Budget(time.monotonic() + 60))
    assert entry is not None and entry.data == b"binary\r\n\x1a\x00"
