"""Reading files and rescanning main (docs/plans/files.md): a read sees a whole commit, a rescan
covers a commit the server did not see, and neither breaks the chain of deltas. Every delta chains
to the one before (`Harness.events`, run at teardown)."""

import asyncio
import os
from typing import Any

import pytest
from changes_helpers import (
    EDIT,
    PATH,
    TEMPLATE,
    THRICE,
    Harness,
    approve,
    bounded,
    cancel_twice,
    commit_on_main,
    deltas,
    edit_main,
    errors,
    queued_callbacks_ran,
    spoil_main,
    spy_drains,
)

from oversight.changes import ChangeNotFound
from oversight.workspace import InvalidPath, content_hash
from oversight_contracts.graph import GraphDelta, GraphSnapshot

pytestmark = pytest.mark.anyio


# --- Reading (GET /files) ---------------------------------------------------------------------


@bounded
async def test_read_file_for_both_targets(h: Harness) -> None:
    main = await h.manager.read_file(None, PATH)
    assert (main.path, main.agent_id) == (PATH, None)
    assert main.content == TEMPLATE[PATH] and main.hash == content_hash(main.content.encode())
    await h.open(1)
    assert await h.edit(1, *EDIT)

    agent = await h.manager.read_file("agent-1", PATH)

    assert agent.agent_id == "agent-1" and "return 3" in agent.content
    assert (await h.manager.read_file(None, PATH)).content == TEMPLATE[PATH]  # main is unchanged
    assert await h.finish(1)
    await approve(h)
    assert not h.worktree_dir("agent-1").exists()
    for gone in ("agent-1", "agent-2", "../x", ""):
        with pytest.raises(ChangeNotFound):
            await h.manager.read_file(gone, PATH)  # closed, unknown or malformed


@bounded
async def test_read_file_refuses_what_it_cannot_read(h: Harness) -> None:
    root = h.workspace.main.root
    (root / "blob.bin").write_bytes(b"\xff\xfe")
    os.symlink(h.tmp_path, root / "link")
    for path in ("../x", "/etc/passwd", "a/.git/config", "link/x", "shop", f"{PATH}/x", "blob.bin"):
        with pytest.raises(InvalidPath) as raised:
            await h.manager.read_file(None, path)
        assert str(h.tmp_path) not in str(raised.value), path
    with pytest.raises(FileNotFoundError):
        await h.manager.read_file(None, "shop/missing.py")
    assert (await h.manager.read_file(None, "shop/__init__.py")).content == ""


@bounded
async def test_get_main_waits_for_a_held_merge(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """A merge that conflicts leaves markers on main until its `merge --abort`: a read answers
    after it, with the bytes of the last commit."""
    await h.run_agent(1, EDIT)
    await commit_on_main(h, PATH, "return 1", "return 5")
    h.workspace.arm("merge", mode="hold_after")
    approval = h.manager.approve("change-1", None)
    await h.workspace.entered.wait()
    assert "<<<<<<<" in (await h.workspace.main.read_text(PATH)).content
    reads: list[str] = []
    real = h.workspace.main.read_text

    async def counting(path: str) -> Any:
        reads.append(path)
        return await real(path)

    monkeypatch.setattr(h.workspace.main, "read_text", counting)
    get = asyncio.get_running_loop().create_task(h.manager.read_file(None, PATH))
    await queued_callbacks_ran()
    assert reads == []  # it waits for main's lock, which the merge holds
    h.workspace.resume.set()
    await approval
    answer = await get

    assert reads == [PATH]
    assert "<<<<<<<" not in answer.content and "return 5" in answer.content
    assert answer.hash == content_hash(answer.content.encode())


# --- Rescans ----------------------------------------------------------------------------------


@pytest.mark.parametrize("fails", ["scan", "publish"])
@bounded
async def test_scan_failure_after_commit_still_answers_saved_then_rescan_covers_it(
    h: Harness, monkeypatch: pytest.MonkeyPatch, fails: str
) -> None:
    before = h.manager.snapshot
    content, base = await edit_main(h, PATH, "return 1", "return 4")
    if fails == "scan":
        real = h.main_graph.scan
        calls = [0]

        def scan(commit: str) -> GraphSnapshot:
            calls[0] += 1
            if calls[0] == 1:
                raise RuntimeError("a scan that raises")
            return real(commit)

        monkeypatch.setattr(h.main_graph, "scan", scan)
    else:
        h.fail_once = GraphDelta

    saved = await h.manager.save_main(PATH, content, base)

    assert saved.hash == content_hash(content.encode())  # the commit is the save
    assert (await h.workspace.main.read_text(PATH)).content == content
    [error] = errors(h, f"graph was not updated after saving {PATH}")
    assert (error.source, error.fatal) == ("graph", False)
    assert deltas(h) == [] and h.manager.snapshot is before

    await h.manager.rescan()

    [delta] = deltas(h)
    assert (delta.cause, delta.from_version, delta.commit) == (
        "rescan",
        before.version,
        await h.main_head(),
    )
    assert h.manager.snapshot.version == delta.to_version


@bounded
async def test_rescan_after_a_commit_behind_the_server(h: Harness) -> None:
    add = "def thrice() -> int:\n    return 3\n\n\ndef twice"
    head = await commit_on_main(h, PATH, "def twice", add)

    await h.manager.rescan()

    [delta] = deltas(h)
    assert (delta.cause, delta.commit) == ("rescan", head)
    assert [n.id for n in delta.nodes_added] == [THRICE]
    assert h.manager.snapshot.commit == head


@bounded
async def test_rescan_with_no_change_publishes_nothing(h: Harness) -> None:
    shown, before = len(h.log), h.manager.snapshot

    await h.manager.rescan()

    assert (len(h.log), h.manager.snapshot) == (shown, before)


@pytest.mark.parametrize("how", ["dirty", "merging"])
@bounded
async def test_rescan_refuses_a_dirty_or_merging_main(h: Harness, how: str) -> None:
    await spoil_main(h, how)
    shown, before = len(h.log), h.manager.snapshot

    await h.manager.rescan()

    [error] = h.events()[shown:]
    assert error.type == "error"
    assert error.payload.message == (
        "Main has uncommitted edits or a merge in progress; nothing was rescanned."
    )
    assert (error.payload.source, error.payload.fatal) == ("graph", False)
    assert h.manager.snapshot is before


@bounded
async def test_rescan_refuses_a_suspect_main(h: Harness) -> None:
    h.manager._suspect = True  # pyright: ignore[reportPrivateUsage]
    shown = len(h.log)

    await h.manager.rescan()

    [error] = h.events()[shown:]
    assert error.type == "error" and error.payload.message.endswith("nothing was rescanned.")
    assert "state is unknown" in error.payload.message


@bounded
async def test_rescan_that_fails_reports_and_keeps_the_snapshot(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = h.manager.snapshot
    await commit_on_main(h, PATH, "return 1", "return 6")

    def scan(commit: str) -> GraphSnapshot:
        raise RuntimeError("a scan that raises")

    monkeypatch.setattr(h.main_graph, "scan", scan)
    with pytest.raises(RuntimeError, match="a scan that raises"):
        await h.manager.rescan()

    [error] = errors(h, "Main's graph was not rescanned")
    assert (error.source, error.fatal) == ("graph", False)
    assert deltas(h) == [] and h.manager.snapshot is before


# --- Pending close-outs, a cancelled request, a publish a cancel left running -----------------


@bounded
async def test_rescan_sends_a_pending_merge_delta_first(h: Harness) -> None:
    await h.run_agent(1, EDIT)
    h.fail_once, h.fail_times = GraphDelta, 2
    await h.manager.approve("change-1", None)

    await h.manager.rescan()

    [merge] = deltas(h)  # sent by the rescan's opening; nothing else had moved
    assert merge.cause == "merge" and h.manager.snapshot.version == merge.to_version
    await h.open(2)
    assert len(deltas(h)) == 1


@bounded
async def test_rescan_supersedes_a_failed_merge_delta(h: Harness) -> None:
    await h.run_agent(1, EDIT)
    before = h.manager.snapshot.version
    h.fail_once, h.fail_times = GraphDelta, 4
    await h.manager.approve("change-1", None)

    await h.manager.rescan()

    [delta] = deltas(h)
    assert (delta.cause, delta.from_version) == ("rescan", before)
    await h.open(2)
    assert len(deltas(h)) == 1  # the merge's own is never sent
    assert h.types()[-1] == "agent.status"


@bounded
async def test_cancel_during_the_rescan_publish_moves_the_snapshot(h: Harness) -> None:
    head = await commit_on_main(h, PATH, "return 1", "return 6")
    h.hold = GraphDelta
    rescan = asyncio.get_running_loop().create_task(h.manager.rescan())
    await h.held.wait()
    rescan.cancel()
    await queued_callbacks_ran()
    assert not rescan.done()
    h.release.set()
    with pytest.raises(asyncio.CancelledError):
        await rescan

    [delta] = deltas(h)
    assert (delta.cause, delta.commit) == ("rescan", head)
    assert h.manager.snapshot.version == delta.to_version


@bounded
async def test_a_rescan_waits_for_a_close_out_publish_a_cancel_left_running(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    await h.run_agent(1, EDIT)
    waited = spy_drains(monkeypatch)
    h.hold = GraphDelta
    approval = h.manager.approve("change-1", None)
    rescan = asyncio.get_running_loop().create_task(h.manager.rescan())  # nothing was pending
    await h.held.wait()
    await cancel_twice(approval)  # the approval ends, its publish still held
    h.release.set()
    await rescan

    assert any(task is not approval for task in waited)  # waited for under main's lock
    [merge] = deltas(h)  # the rescan finds nothing past the merge
    assert merge.cause == "merge" and h.manager.snapshot.version == merge.to_version
