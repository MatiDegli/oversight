"""Explicit source preparation and graph export use fake Git, never the network."""

import asyncio
import shutil
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

import workspace_source
from oversight.workspace import BOLTONS, ProcessResult
from oversight_contracts.graph import GraphSnapshot


class FakeGit:
    def __init__(self, *, head: str = BOLTONS.commit, fail: str = "") -> None:
        self.head = head
        self.fail = fail
        self.calls: list[tuple[str, ...]] = []

    async def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout_s: float,
        env: Mapping[str, str] | None = None,
        output_cap: int = 262144,
    ) -> ProcessResult:
        assert timeout_s > 0 and env is not None
        self.calls.append(tuple(argv))
        operation = argv[1]
        if operation == "clone":
            (Path(argv[-1]) / ".git").mkdir(parents=True)
        if operation == self.fail:
            return ProcessResult(1, "", "fake failure", False, False, 1)
        return ProcessResult(0, self.head if operation == "rev-parse" else "", "", False, False, 1)


@pytest.mark.anyio
async def test_prepare_first_and_verified_reuse(tmp_path: Path) -> None:
    runtime = tmp_path / ".runtime"
    fake = FakeGit()
    assert await workspace_source.fetch_source(BOLTONS, runtime, spawn=fake) == BOLTONS.checkout(
        runtime
    )
    assert fake.calls == [
        (
            "git",
            "clone",
            "--no-checkout",
            "--no-hardlinks",
            "--",
            BOLTONS.upstream,
            str(BOLTONS.checkout(runtime)),
        ),
        ("git", "checkout", "--detach", BOLTONS.commit),
        ("git", "rev-parse", "HEAD"),
        ("git", "status", "--porcelain", "--untracked-files=all"),
    ]
    fake.calls.clear()
    await workspace_source.fetch_source(BOLTONS, runtime, spawn=fake)
    assert [call[1] for call in fake.calls] == ["rev-parse", "status"]


@pytest.mark.anyio
async def test_prepare_wrong_pin_never_replaces_checkout(tmp_path: Path) -> None:
    runtime = tmp_path / ".runtime"
    root = BOLTONS.checkout(runtime)
    (root / ".git").mkdir(parents=True)
    (root / "keep").write_text("untouched")
    fake = FakeGit(head="0" * 40)
    with pytest.raises(ValueError, match="make workspace-fetch-boltons"):
        await workspace_source.fetch_source(BOLTONS, runtime, spawn=fake)
    assert [call[1] for call in fake.calls] == ["rev-parse"]
    assert (root / "keep").read_text() == "untouched"


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["clone", "checkout"])
async def test_prepare_failure_stops(tmp_path: Path, failure: str) -> None:
    fake = FakeGit(fail=failure)
    with pytest.raises(ValueError, match="fake failure"):
        await workspace_source.fetch_source(BOLTONS, tmp_path / ".runtime", spawn=fake)
    assert fake.calls[-1][1] == failure
    assert not BOLTONS.checkout(tmp_path / ".runtime").exists()
    # A retry can prepare a clean checkout instead of rejecting a permanent partial clone.
    await workspace_source.fetch_source(BOLTONS, tmp_path / ".runtime", spawn=FakeGit())


@pytest.mark.anyio
async def test_export_bare_whole_tree_snapshot_without_layout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runtime = tmp_path / ".runtime"
    root = BOLTONS.checkout(runtime)
    (root / ".git").mkdir(parents=True)
    for name in ("boltons/__init__.py", "boltons/value.py", "tests/test_value.py", "docs/conf.py"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def value():\n    return 42\n")
    (root / "tests/test_value.py").write_text(
        "from boltons.value import value\ndef test_value():\n    assert value() == 42\n"
    )
    (root / "broken.py").write_text("def !!!")
    fake = FakeGit()
    path = await workspace_source.export_graph(BOLTONS, runtime, spawn=fake)
    assert path == runtime / "graphs/boltons-d31d821.snapshot.json"
    snapshot = GraphSnapshot.model_validate_json(path.read_text())
    assert snapshot.commit == BOLTONS.commit
    assert {n.path for n in snapshot.nodes} >= {
        "boltons/value.py",
        "tests/test_value.py",
        "docs/conf.py",
    }
    assert snapshot.parse_errors
    assert snapshot.edges
    assert not snapshot.positions
    assert not await asyncio.to_thread(lambda: list(tmp_path.rglob("*layout*")))
    assert [call[1] for call in fake.calls] == ["rev-parse", "status"]
    output = capsys.readouterr().out
    assert BOLTONS.name in output and BOLTONS.commit in output


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["timeout", "oserror", "cancelled"])
async def test_prepare_clone_interruption_removes_only_its_own_directory(tmp_path, failure):
    root = BOLTONS.checkout(tmp_path)
    neighbor = root.parent / "keep"
    neighbor.mkdir(parents=True)
    (neighbor / "sentinel").write_text("untouched")

    async def interrupted(argv, **kwargs):
        (root / ".git").mkdir(parents=True)
        if failure == "oserror":
            raise OSError("fake interruption")
        if failure == "cancelled":
            raise asyncio.CancelledError
        return ProcessResult(None, "", "fake timeout", True, False, 1)

    error = {"timeout": ValueError, "oserror": OSError, "cancelled": asyncio.CancelledError}[
        failure
    ]
    with pytest.raises(error):
        await workspace_source.fetch_source(BOLTONS, tmp_path, spawn=interrupted)
    assert not root.exists()
    assert (neighbor / "sentinel").read_text() == "untouched"


@pytest.mark.anyio
async def test_prepare_existing_partial_checkout_is_never_deleted(tmp_path):
    root = BOLTONS.checkout(tmp_path)
    root.mkdir(parents=True)
    (root / "sentinel").write_text("untouched")
    fake = FakeGit()
    with pytest.raises(ValueError, match="missing or unsafe checkout"):
        await workspace_source.fetch_source(BOLTONS, tmp_path, spawn=fake)
    assert not fake.calls
    assert (root / "sentinel").read_text() == "untouched"


@pytest.mark.anyio
@pytest.mark.parametrize("original_cancelled", [False, True])
async def test_cleanup_finishes_despite_repeated_cancellation(
    tmp_path, monkeypatch, original_cancelled
):
    root = BOLTONS.checkout(tmp_path)
    clone_started = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = threading.Event()
    loop = asyncio.get_running_loop()
    original_rmtree = shutil.rmtree
    failure = ValueError("original clone failure")

    async def interrupted(argv, **kwargs):
        (root / ".git").mkdir()
        clone_started.set()
        if original_cancelled:
            await asyncio.Future()
        raise failure

    def delayed_rmtree(path):
        loop.call_soon_threadsafe(cleanup_started.set)
        assert release_cleanup.wait(5), "test did not release cleanup"
        original_rmtree(path)

    monkeypatch.setattr(workspace_source.shutil, "rmtree", delayed_rmtree)
    task = asyncio.create_task(workspace_source.fetch_source(BOLTONS, tmp_path, spawn=interrupted))
    try:
        async with asyncio.timeout(5):
            await clone_started.wait()
            if original_cancelled:
                task.cancel("original clone cancellation")
            await cleanup_started.wait()
            for _ in range(3):
                task.cancel("cancellation during cleanup")
                await asyncio.sleep(0)
                assert not task.done(), "fetch returned before cleanup finished"
            release_cleanup.set()
            if original_cancelled:
                with pytest.raises(asyncio.CancelledError, match="original clone cancellation"):
                    await task
            else:
                with pytest.raises(ValueError) as caught:
                    await task
                assert caught.value is failure
        assert not root.exists()
    finally:
        release_cleanup.set()
        # Also retrieve the old implementation's failure when testing the red case.
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.anyio
@pytest.mark.parametrize("original_cancelled", [False, True])
async def test_cleanup_error_preserves_original_failure(tmp_path, monkeypatch, original_cancelled):
    failure = (
        asyncio.CancelledError("original clone cancellation")
        if original_cancelled
        else ValueError("original clone failure")
    )
    cleaned = []

    async def interrupted(argv, **kwargs):
        raise failure

    def failed_rmtree(path):
        cleaned.append(path)
        raise OSError("cleanup failure")

    monkeypatch.setattr(workspace_source.shutil, "rmtree", failed_rmtree)
    with pytest.raises(type(failure)) as caught:
        await workspace_source.fetch_source(BOLTONS, tmp_path, spawn=interrupted)
    assert caught.value is failure
    assert cleaned == [BOLTONS.checkout(tmp_path)]
