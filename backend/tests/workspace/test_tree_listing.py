"""A cancelled tree listing keeps the workspace locked until its disk walk ends."""

import asyncio
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from oversight.workspace import Workspace
from oversight.workspace import tree as module

from .test_snapshot import force_nt, reparse_at

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("nt", [False, True])
@pytest.mark.parametrize("cancel", [False, True], ids=["timeout", "cancel"])
@pytest.mark.parametrize("worker_raises", [False, True], ids=["returns", "raises"])
async def test_listing_joins_worker_before_releasing_lock(
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
    cancel: bool,
    worker_raises: bool,
    nt: bool,
) -> None:
    if nt:
        force_nt(monkeypatch)
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    finished = asyncio.Event()
    release = threading.Event()

    def delayed(root: Path, deadline: float):
        loop.call_soon_threadsafe(started.set)
        try:
            assert release.wait(5), "the test did not release its walker"
            if worker_raises:
                raise OSError("disk read failed during cancellation")
            return walk(root, time.monotonic() + 5)
        finally:
            loop.call_soon_threadsafe(finished.set)

    walk = module.walk_tree
    monkeypatch.setattr(module, "TREE_TIMEOUT_S", 10 if cancel else 0.1)
    monkeypatch.setattr(module, "walk_tree", delayed)
    pending = asyncio.create_task(module.list_tree(workspace.main))
    try:
        await asyncio.wait_for(started.wait(), 5)
        if cancel:
            pending.cancel()
        else:
            await asyncio.sleep(0.15)  # let the request's 0.1 s timeout cancel it
        # Allow cancellation to propagate through the listing, rather than inspecting the
        # task immediately after its cancel flag was set.
        for _ in range(5):
            await asyncio.sleep(0)
        assert not pending.done(), "listing released its lock while the walker was running"
        assert not workspace.operations.idle
        assert not finished.is_set()
        # A second cancellation must not interrupt the join either.
        if cancel:
            pending.cancel()
            for _ in range(5):
                await asyncio.sleep(0)
            assert not pending.done()
            assert not workspace.operations.idle
    finally:
        release.set()
        await asyncio.wait_for(finished.wait(), 5)
        result = (await asyncio.gather(pending, return_exceptions=True))[0]
    assert isinstance(result, asyncio.CancelledError if cancel else TimeoutError)
    assert workspace.operations.idle


@pytest.mark.parametrize("site", ["root", "directory", "file", "hash-parent"])
def test_nt_tree_reparse_never_reads_targets(tmp_path, monkeypatch, site):
    force_nt(monkeypatch)
    root = tmp_path / "tree"
    root.mkdir()
    folder = root / "folder"
    folder.mkdir()
    leaf = folder / "secret.py"
    leaf.write_bytes(b"do not hash")
    target = {"root": root, "directory": folder, "file": leaf, "hash-parent": folder}[site]
    reparse_at(monkeypatch, target)
    original = os.open
    opened = []

    def no_target(path, flags, *args, **kwargs):
        assert not Path(path).is_relative_to(target)
        opened.append(path)
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", no_target)
    if site == "root":
        with pytest.raises(OSError):
            module.walk_tree(root, time.monotonic() + 5)
    elif site == "hash-parent":
        assert module._hash(folder, leaf.name, [100], time.monotonic() + 5) is None
    else:
        _, files, truncated = module.walk_tree(root, time.monotonic() + 5)
        assert not files and not truncated
    assert not opened


@pytest.mark.parametrize("failure", ["cap", "error", "timeout", "done"])
def test_nt_tree_closes_iterators(tmp_path, monkeypatch, failure):
    force_nt(monkeypatch)
    (tmp_path / "a.py").touch()
    real_scan = os.scandir
    iterators = []

    class Scan:
        def __init__(self, path):
            self.iterator = real_scan(path)
            self.closed = False
            iterators.append(self)

        def __next__(self):
            if failure == "error":
                raise OSError("controlled scan error")
            if failure == "timeout":
                raise TimeoutError("controlled deadline")
            # NT DirEntry identities may be cached zeros: only its name may be used.
            return SimpleNamespace(name=next(self.iterator).name)

        def __iter__(self):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

        def close(self):
            self.closed = True
            self.iterator.close()

    monkeypatch.setattr(os, "scandir", Scan)
    if failure == "cap":
        monkeypatch.setattr(module, "ENTRY_CAP", 0)
    if failure in {"error", "timeout"}:
        with pytest.raises(OSError):
            module.walk_tree(tmp_path, time.monotonic() + 5)
    else:
        _, _, truncated = module.walk_tree(tmp_path, time.monotonic() + 5)
        assert truncated == (failure == "cap")
        if failure == "done":
            from oversight.workspace.suite_files import file_stamp

            assert "a.py" in file_stamp(tmp_path)
    assert iterators and all(iterator.closed for iterator in iterators)
