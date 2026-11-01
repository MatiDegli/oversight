"""Cleanup precedes comparison, verified writes are final, and artifact vetoes stay local."""

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest

from oversight.workspace import Workspace, core, run_process, test_tree
from oversight.workspace import snapshot as snapshots
from oversight.workspace.integrity import IntegrityError
from oversight.workspace.isolation import IsolatedRunner
from oversight.workspace.metadata_io import Entry, replace_file
from oversight.workspace.snapshot import MetadataSnapshot, take_snapshot

from .test_snapshot import force_nt
from .test_snapshot_attacks import attack_source, attack_workspace, monitor_git

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("nt", [False, True])
@pytest.mark.parametrize("artifact", ["out.json", ".coverage"])
async def test_ordinary_artifact_only_vetoes_that_suite(
    make_workspace: Any, artifact: str, monkeypatch: pytest.MonkeyPatch, nt: bool
) -> None:
    workspace = await attack_workspace(make_workspace)
    first = await workspace.create_worktree("agent-1")
    second = await workspace.create_worktree("agent-2")
    (first.root / "tests/test_artifact.py").write_text(
        "from pathlib import Path\ndef test_artifact():\n"
        f"    Path({artifact!r}).write_text('artifact')\n"
    )
    # Ignore it too: a status-only barrier would miss this artifact.
    with (first.git_identity.common / "info/exclude").open("a") as handle:
        handle.write(artifact + "\n")
    if nt:
        force_nt(monkeypatch)
    run = await workspace.run_tests(first, timeout_s=10)
    assert run.result.outcome == "passed"
    assert any("code=integrity_invalid" in p and artifact in p for p in run.problems)
    assert not workspace.integrity.rules_suspended and not workspace.integrity.main_suspect
    other = await workspace.run_tests(second, timeout_s=10)
    assert other.result.outcome == "passed" and not other.problems


@pytest.mark.parametrize("nt", [False, True])
async def test_failed_readback_is_suspect_without_another_git(
    make_workspace: Any, monkeypatch: pytest.MonkeyPatch, nt: bool
) -> None:
    workspace = await attack_workspace(make_workspace)
    tree = await workspace.create_worktree("agent-1")
    (tree.root / "tests/test_attack.py").write_text(attack_source(workspace, "config", main=False))
    if nt:
        force_nt(monkeypatch)
    original = replace_file
    writes: list[Path] = []

    def corrupted(
        path: Path, before: Entry | None, wanted: Entry | None, deadline: float, *, cas: bool
    ) -> None:
        writes.append(path)
        original(path, before, wanted, deadline, cas=cas)
        if path.name == "config":
            path.write_bytes(b"readback no longer matches the snapshot")

    monkeypatch.setattr(snapshots, "replace_file", corrupted)
    sentinel = workspace.tmp_dir / "git-sentinel"
    calls = monitor_git(workspace, monkeypatch, sentinel)
    with pytest.raises(IntegrityError, match="code=main_suspect"):
        await workspace.run_tests(tree, timeout_s=10)
    assert len(writes) == 1  # No second attempt at the effect.
    assert workspace.integrity.rules_suspended and workspace.integrity.main_suspect
    before = len(calls)
    with pytest.raises(IntegrityError, match="code=main_suspect"):
        await tree.commit_all("Never run", workspace.user_author())
    assert len(calls) == before and not sentinel.exists()


@pytest.mark.parametrize("nt", [False, True])
async def test_lock_racing_restoration_is_not_stolen(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, nt: bool
) -> None:
    if nt:
        force_nt(monkeypatch)
    identity = workspace.main.git_identity
    snapshot = take_snapshot(identity, workspace.integrity, 5)
    path = identity.common / "refs/heads/main"
    path.write_bytes(b"1" * 40 + b"\n")
    original = replace_file
    lock = path.with_suffix(".lock")

    def racing(
        target: Path, before: Entry | None, wanted: Entry | None, deadline: float, *, cas: bool
    ) -> None:
        lock.write_bytes(b"unknown owner")
        original(target, before, wanted, deadline, cas=cas)

    monkeypatch.setattr(snapshots, "replace_file", racing)
    with pytest.raises(IntegrityError, match="code=main_suspect"):
        snapshots.finish_snapshot(snapshot, workspace.integrity)
    assert lock.read_bytes() == b"unknown owner"
    assert path.read_bytes() == b"1" * 40 + b"\n"


@pytest.mark.parametrize("nt", [False, True])
async def test_repeated_cancellation_holds_lease_through_restoration(
    make_workspace: Any, monkeypatch: pytest.MonkeyPatch, nt: bool
) -> None:
    workspace = await attack_workspace(make_workspace)
    tree = await workspace.create_worktree("agent-1")
    second = await workspace.create_worktree("agent-2")
    marker = workspace.tmp_dir / "started"
    source = attack_source(workspace, "config", main=False)
    source += f"    Path({str(marker)!r}).touch()\n    import time\n    time.sleep(60)\n"
    (tree.root / "tests/test_attack.py").write_text(source)
    config = tree.git_identity.common / "config"
    original = config.read_bytes()
    entered, release = threading.Event(), threading.Event()
    finish = MetadataSnapshot.finish

    def delayed(self: MetadataSnapshot, state: Any, deadline: float) -> None:
        entered.set()
        assert release.wait(5)
        finish(self, state, deadline)

    monkeypatch.setattr(MetadataSnapshot, "finish", delayed)
    if nt:
        force_nt(monkeypatch)
    task = asyncio.create_task(workspace.run_tests(tree, timeout_s=10))
    async with asyncio.timeout(5):
        while not await asyncio.to_thread(marker.exists):  # noqa: ASYNC110 -- Child file, bounded wait.
            await asyncio.sleep(0.01)
    task.cancel()
    assert await asyncio.to_thread(entered.wait, 5)
    waiting = asyncio.create_task(second.head())
    try:
        await asyncio.sleep(0.02)
        assert not waiting.done()
        task.cancel()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await waiting
    assert config.read_bytes() == original
    assert workspace.integrity.rules_suspended and not workspace.integrity.main_suspect
    assert workspace.operations.idle


async def test_unconfirmed_namespace_death_never_compares_or_restores(
    make_workspace: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = await attack_workspace(make_workspace)
    runner = IsolatedRunner(workspace.runtime_root)
    original = MetadataSnapshot.finish
    calls = 0

    def finish(self: MetadataSnapshot, state: Any, deadline: float) -> None:
        nonlocal calls
        calls += 1
        original(self, state, deadline)

    async def unconfirmed(self: IsolatedRunner, argv: Any, **kwargs: Any) -> Any:
        result = await run_process(argv, **kwargs)
        self.cleanup_complete = False
        return result

    monkeypatch.setattr(MetadataSnapshot, "finish", finish)
    monkeypatch.setattr(IsolatedRunner, "__call__", unconfirmed)
    with pytest.raises(IntegrityError, match="code=main_suspect"):
        await workspace.run_tests(workspace.main, timeout_s=10, spawn=runner)
    assert calls == 0
    assert workspace.integrity.rules_suspended and workspace.integrity.main_suspect
    spawn = pytest.MonkeyPatch()
    try:
        # No Git whatsoever after uncertainty, including commands normally exempt from scans.
        from unittest.mock import AsyncMock

        forbidden = AsyncMock()
        spawn.setattr(core, "run_process", forbidden)
        spawn.setattr(test_tree, "run_process", forbidden)
        with pytest.raises(IntegrityError, match="code=main_suspect"):
            await workspace.main.head()
        forbidden.assert_not_awaited()
    finally:
        spawn.undo()


async def test_git_queued_before_uncertainty_is_refused_inside_lease(
    make_workspace: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = await attack_workspace(make_workspace)
    first = await workspace.create_worktree("agent-1")
    second = await workspace.create_worktree("agent-2")
    entered, release = asyncio.Event(), asyncio.Event()

    async def poison(argv: Any, **kwargs: Any) -> Any:
        entered.set()
        await release.wait()
        result = await run_process(argv, **kwargs)
        (first.git_identity.directory / "index.lock").write_bytes(b"unowned")
        return result

    calls = monitor_git(workspace, monkeypatch, workspace.tmp_dir / "git-sentinel")
    suite = asyncio.create_task(workspace.run_tests(first, timeout_s=10, spawn=poison))
    await entered.wait()
    queued = asyncio.create_task(second.head())
    await asyncio.sleep(0.02)
    assert not queued.done()
    before = len(calls)
    release.set()
    with pytest.raises(IntegrityError, match="code=main_suspect"):
        await suite
    with pytest.raises(IntegrityError, match="code=main_suspect"):
        await queued
    assert len(calls) == before


@pytest.mark.parametrize("artifact", ["out.json", ".coverage", "linked", "junction"])
async def test_nt_artifact_failures_only_veto_evidence(workspace, monkeypatch, artifact):
    from oversight.workspace.suite_files import file_problems, file_stamp

    from .test_snapshot import reparse_at

    force_nt(monkeypatch)
    before = file_stamp(workspace.main.root)
    path = workspace.main.root / artifact
    path.write_bytes(b"ignored artifact")
    if artifact == "linked":
        path.unlink()
        path.symlink_to(workspace.runtime_root / "server.lock")
    elif artifact == "junction":
        path.unlink()
        path.mkdir()
        reparse_at(monkeypatch, path)
    assert file_problems(workspace.main.root, before)
    assert not workspace.integrity.rules_suspended and not workspace.integrity.main_suspect


NT_REFUSALS = (
    ".GIT",
    ".g\u0131t",
    ".git.",
    ".git ",
    "GIT~1",
    "a.py:s",
    "CON",
    "nul.txt",
    "COM1",
    "C:x",
    "a/../b",
    "CON/x.py",
    "NUL/a.py",
    "COM1/b",
    "sub/CON",
    "CONIN$",
    "CONOUT$.txt",
    "LPT1.txt",
    "COM¹",
    "C:/root",
    "/root",
    "\\root",
    "\\\\host\\share",
    "a\\b",
    "a::$DATA",
    "sub/a.",
    "sub/a ",
)


@pytest.mark.parametrize("name", NT_REFUSALS)
def test_nt_working_names_refuse_before_access(tmp_path, monkeypatch, name):
    from unittest.mock import Mock

    from oversight.workspace import tree
    from oversight.workspace.metadata_io import Budget, nt_guard, nt_name, read_at
    from oversight.workspace.suite_files import file_stamp

    force_nt(monkeypatch)
    access = Mock(side_effect=AssertionError("refused name reached lstat"))
    monkeypatch.setattr(Path, "lstat", access)
    with pytest.raises(IntegrityError):
        nt_name(name, working=True)
    with pytest.raises(IntegrityError):
        read_at(tmp_path, name, tmp_path / name, Budget(float("inf")), kind="working file")
    assert tree._hash(tmp_path, name, [100], float("inf")) is None
    # Multi-component traversal reaches the actual working-root boundary too.
    if not name.startswith(("/", "\\")) and ":" not in name and "\\" not in name:
        with pytest.raises(IntegrityError):
            file_stamp(tmp_path / name)
        with pytest.raises(IntegrityError):
            nt_guard(tmp_path / name, float("inf"), working=True)
    access.assert_not_called()


@pytest.mark.parametrize("name", [".git", ".GIT", ".g\u0131t"])
def test_nt_nested_git_vetoes_without_access(tmp_path, monkeypatch, name):
    from oversight.workspace.suite_files import file_problems, file_stamp

    force_nt(monkeypatch)
    (tmp_path / "sub").mkdir()
    before = file_stamp(tmp_path)
    nested = tmp_path / "sub" / name
    nested.mkdir()
    original = Path.lstat

    def no_access(self):
        assert not self.is_relative_to(nested)
        return original(self)

    monkeypatch.setattr(Path, "lstat", no_access)
    assert file_problems(tmp_path, before)
