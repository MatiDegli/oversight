"""The person's save on main (docs/plans/files.md): one commit as the user and one `graph.delta`,
each step whole through a cancel of its request, refusals that write nothing, and a failed commit
that restores the file or makes main suspect. Every delta chains to the one before
(`Harness.events`, run at teardown)."""

import asyncio
import threading
from typing import Any

import pytest
from changes_helpers import (
    EDIT,
    LOOSER,
    PATH,
    TEMPLATE,
    TESTS,
    THRICE,
    VALUE,
    Harness,
    approve,
    assert_main_clean,
    bounded,
    cancel_twice,
    commit_on_main,
    deltas,
    edit_main,
    errors,
    hold_a_save_that_ends_suspect,
    queued_callbacks_ran,
    spoil_main,
    spy_drains,
    start_save,
)

from oversight.changes import WrongState
from oversight.workspace import InvalidPath, StaleFile, WorkspaceError, content_hash
from oversight_contracts.commands import FileSaved
from oversight_contracts.events import ChangeProposed, ChangeResolved, ChangeUpdated, ErrorReport
from oversight_contracts.graph import GraphDelta, GraphSnapshot

pytestmark = pytest.mark.anyio


async def log(h: Harness, fmt: str) -> str:
    return (await h.main_git("log", "-1", f"--format={fmt}")).stdout.strip()


# --- Saves ------------------------------------------------------------------------------------


@bounded
async def test_save_commits_as_the_user(h: Harness) -> None:
    pre = await h.main_head()
    content, base = await edit_main(h, PATH, "return 1", "return 4")

    saved = await h.manager.save_main(PATH, content, base)

    assert saved == FileSaved(path=PATH, hash=content_hash(content.encode()))
    commit = await log(h, "%an <%ae>|%s|%aI|%P")
    assert commit == f"User <user@oversight.invalid>|Edit {PATH}|2026-10-26T12:00:00+00:00|{pre}"
    assert (await h.main_git("diff", "--name-only", pre, "HEAD")).stdout.split() == [PATH]
    await assert_main_clean(h)
    assert h.types() == ["graph.snapshot", "graph.delta"]  # no change.* and no agent.status
    [delta] = deltas(h)
    assert (delta.cause, delta.commit) == ("user_edit", await h.main_head())
    assert delta.to_version != delta.from_version  # the file's bytes changed
    assert h.manager.snapshot.version == delta.to_version
    assert h.manager.snapshot.commit == delta.commit


@bounded
async def test_save_rechecks_the_hash_under_the_lock(h: Harness) -> None:
    """Main moves under a save that waits for its lock: the lock's holder wins, the save is stale,
    and the file is never overwritten."""
    content, base = await edit_main(h, PATH, "return 1", "return 4")
    async with h.workspace.locks(main=True):
        save = start_save(h, content, base)
        await queued_callbacks_ran()
        assert not save.done()  # it waits for the lock
        await commit_on_main(h, PATH, "return 1", "return 5")
    with pytest.raises(StaleFile):
        await save

    assert "return 5" in (await h.workspace.main.read_text(PATH)).content
    assert deltas(h) == []
    await assert_main_clean(h)


@bounded
async def test_identical_save_is_a_no_op(h: Harness) -> None:
    read = await h.workspace.main.read_text(PATH)
    head, shown = await h.main_head(), len(h.log)

    saved = await h.manager.save_main(PATH, read.content, read.hash)

    assert saved == FileSaved(path=PATH, hash=read.hash)
    assert (await h.main_head(), len(h.log)) == (head, shown)
    assert h.payloads(ErrorReport) == []


@bounded
async def test_body_only_save_chains(h: Harness) -> None:
    first_text, first_base = await edit_main(h, PATH, "return 1", "return 4")
    await h.manager.save_main(PATH, first_text, first_base)
    second_text, second_base = await edit_main(h, PATH, "return 4", "return 5")

    await h.manager.save_main(PATH, second_text, second_base)

    first, second = deltas(h)  # no node moved: each delta carries its versions and commit only
    assert second.from_version == first.to_version != second.to_version
    assert (second.nodes_added, second.nodes_changed, second.nodes_removed) == ([], [], [])
    assert h.manager.snapshot.version == second.to_version


@bounded
async def test_save_that_adds_a_function_sends_its_node(h: Harness) -> None:
    add = "def thrice() -> int:\n    return 3\n\n\ndef twice"
    content, base = await edit_main(h, PATH, "def twice", add)

    await h.manager.save_main(PATH, content, base)

    [delta] = deltas(h)
    assert [n.id for n in delta.nodes_added] == [THRICE]
    assert {n.id for n in h.manager.snapshot.nodes} >= {VALUE, THRICE}


@bounded
async def test_non_python_save_moves_the_commit(h: Harness) -> None:
    before = h.manager.snapshot
    read = await h.workspace.main.read_text("pyproject.toml")

    await h.manager.save_main("pyproject.toml", read.content + "# a note\n", read.hash)

    [delta] = deltas(h)
    head = await h.main_head()
    assert (delta.from_version, delta.to_version) == (before.version, before.version)
    assert (delta.cause, delta.commit) == ("user_edit", head)
    assert (delta.nodes_added, delta.nodes_changed, delta.nodes_removed) == ([], [], [])
    assert h.manager.snapshot.commit == head != before.commit


# --- Refusals write nothing -------------------------------------------------------------------


@pytest.mark.parametrize("how", ["dirty", "merging"])
@bounded
async def test_save_refuses_a_dirty_or_merging_main(h: Harness, how: str) -> None:
    await spoil_main(h, how)
    head, shown = await h.main_head(), len(h.log)
    content, base = await edit_main(h, TESTS, *LOOSER)

    with pytest.raises(WrongState, match="uncommitted edits or a merge"):
        await h.manager.save_main(TESTS, content, base)

    assert (await h.main_head(), len(h.log)) == (head, shown)
    assert (await h.workspace.main.read_text(TESTS)).content == TEMPLATE[TESTS]


@bounded
async def test_save_refuses_what_it_cannot_write_and_writes_nothing(h: Harness) -> None:
    (h.workspace.main.root / "blob.bin").write_bytes(b"\xff\xfe")
    async with h.workspace.locks(main=True):
        await h.workspace.git_ok(h.workspace.main, "add", "-A")
        await h.workspace.git_ok(h.workspace.main, "commit", "-qm", "Add a blob")
    head, shown = await h.main_head(), len(h.log)
    _, base = await edit_main(h, PATH, "return 1", "return 4")
    cases: list[tuple[str, str, type[Exception]]] = [
        ("shop/new.py", base, StaleFile),  # missing: a save never creates a file
        (PATH, "0" * 64, StaleFile),
        ("../x.py", base, InvalidPath),
        ("/etc/passwd", base, InvalidPath),
        ("a/.git/config", base, InvalidPath),
        (f"{PATH}/x", base, InvalidPath),  # through a file
        ("shop", base, InvalidPath),  # a directory
        ("blob.bin", base, InvalidPath),  # not text
    ]
    for path, given, failure in cases:
        with pytest.raises(failure) as raised:
            await h.manager.save_main(path, "x = 1\n", given)
        assert str(h.tmp_path) not in str(raised.value), path

    assert (await h.main_head(), len(h.log)) == (head, shown)
    assert not (h.workspace.main.root / "shop" / "new.py").exists()
    await assert_main_clean(h)


# --- Pending close-outs (B1) ------------------------------------------------------------------


@bounded
async def test_save_supersedes_a_failed_merge_delta(h: Harness) -> None:
    """The merge's delta fails four times: the approval's two tries, then the save's. The save's
    delta starts from the graph the UI holds and covers the merge: the merge's own is never sent,
    so the next `open` sends only `done`."""
    await h.run_agent(1, EDIT)
    before = h.manager.snapshot.version
    h.fail_once, h.fail_times = GraphDelta, 4
    await h.manager.approve("change-1", None)
    assert errors(h, "close-out of change-1 is incomplete")
    content, base = await edit_main(h, PATH, "return 3", "return 4")

    await h.manager.save_main(PATH, content, base)

    [delta] = deltas(h)
    assert (delta.cause, delta.from_version) == ("user_edit", before)
    assert h.manager.snapshot.version == delta.to_version
    await h.open(2)
    assert len(deltas(h)) == 1
    assert h.types()[-1] == "agent.status"  # the `done` the merge was waiting to send


@bounded
async def test_save_sends_a_pending_merge_delta_first(h: Harness) -> None:
    await h.run_agent(1, EDIT)
    h.fail_once, h.fail_times = GraphDelta, 2
    await h.manager.approve("change-1", None)
    assert deltas(h) == []
    content, base = await edit_main(h, PATH, "return 3", "return 4")

    await h.manager.save_main(PATH, content, base)

    merge, edit = deltas(h)
    assert (merge.cause, edit.cause) == ("merge", "user_edit")
    assert edit.from_version == merge.to_version


# --- A failed commit (B2) ---------------------------------------------------------------------


@bounded
async def test_commit_landing_as_a_timeout_counts_as_saved(h: Harness) -> None:
    pre = await h.main_head()
    content, base = await edit_main(h, PATH, "return 1", "return 4")
    h.workspace.arm("commit", mode="after")  # git ran, then reported a timeout

    saved = await h.manager.save_main(PATH, content, base)

    assert saved.hash == content_hash(content.encode())
    assert await h.main_head() != pre
    assert (await h.workspace.main.read_text(PATH)).content == content
    await assert_main_clean(h)
    assert [d.cause for d in deltas(h)] == ["user_edit"]
    assert h.payloads(ErrorReport) == []


@pytest.mark.parametrize(
    ("subcommand", "mode"), [("add", "fail"), ("commit", "fail"), ("commit", "timeout")]
)
@bounded
async def test_commit_failure_restores_the_file(h: Harness, subcommand: str, mode: Any) -> None:
    pre = await h.main_head()
    original = await h.workspace.main.read_text(PATH)
    content, base = await edit_main(h, PATH, "return 1", "return 4")
    h.workspace.arm(subcommand, mode=mode)

    with pytest.raises(WorkspaceError, match=f"saving {PATH} failed"):
        await h.manager.save_main(PATH, content, base)

    assert await h.main_head() == pre
    assert await h.workspace.main.read_text(PATH) == original
    await assert_main_clean(h)
    [error] = h.payloads(ErrorReport)
    assert (error.source, error.fatal, error.agent_id, error.change_id) == (
        "git",
        False,
        None,
        None,
    )
    assert "main is unchanged" in error.message
    assert deltas(h) == [] and not h.manager.main_suspect
    await h.manager.save_main(PATH, content, base)  # main is trusted: the same save now lands
    assert [d.cause for d in deltas(h)] == ["user_edit"]


@bounded
async def test_restore_failure_makes_main_suspect(h: Harness) -> None:
    await h.run_agent(1, EDIT)
    content, base = await edit_main(h, PATH, "return 1", "return 4")
    h.workspace.arm("commit")
    h.workspace.arm("add", skip=1)  # the save's own `add` passes; the restore's fails

    with pytest.raises(WorkspaceError, match=f"saving {PATH} failed"):
        await h.manager.save_main(PATH, content, base)

    assert h.manager.main_suspect
    [error] = h.payloads(ErrorReport)
    assert (error.fatal, error.agent_id) == (True, None)
    assert deltas(h) == []
    with pytest.raises(WrongState, match="state is unknown"):
        await h.manager.save_main(PATH, content, base)
    await h.manager.reject("change-1", None)  # still closes the change
    assert h.payloads(ChangeResolved)[-1].decision == "rejected"


# --- A cancelled request (B3) -----------------------------------------------------------------


@bounded
async def test_cancel_during_the_save_publish_moves_the_snapshot(h: Harness) -> None:
    """The store took the save's delta, then the request was cancelled before hearing it: the
    snapshot still moves to it, so the next save chains."""
    content, base = await edit_main(h, PATH, "return 1", "return 4")
    h.hold = GraphDelta
    save = start_save(h, content, base)
    await h.held.wait()  # committed to the store, its receipt not yet back
    save.cancel()
    await queued_callbacks_ran()
    assert not save.done()  # the save runs on to its end
    h.release.set()
    with pytest.raises(asyncio.CancelledError):
        await save

    [first] = deltas(h)
    assert h.manager.snapshot.version == first.to_version
    content, base = await edit_main(h, PATH, "return 4", "return 5")
    await h.manager.save_main(PATH, content, base)
    assert deltas(h)[1].from_version == first.to_version


@bounded
async def test_cancel_during_a_held_commit_still_saves(h: Harness) -> None:
    pre = await h.main_head()
    content, base = await edit_main(h, PATH, "return 1", "return 4")
    h.workspace.arm("commit", mode="hold")
    save = start_save(h, content, base)
    await h.workspace.entered.wait()
    save.cancel()
    await queued_callbacks_ran()
    assert not save.done()
    h.workspace.resume.set()
    with pytest.raises(asyncio.CancelledError):
        await save

    assert await h.main_head() != pre
    assert (await h.workspace.main.read_text(PATH)).content == content
    await assert_main_clean(h)
    assert [d.cause for d in deltas(h)] == ["user_edit"]
    assert h.manager.snapshot.version == deltas(h)[0].to_version


# --- Authority rechecked in the lock (B4) -----------------------------------------------------


@bounded
async def test_approve_queued_behind_a_failing_save_does_not_merge(h: Harness) -> None:
    await h.run_agent(1, EDIT)
    pre = await h.main_head()
    save = await hold_a_save_that_ends_suspect(h)
    approval = h.manager.approve("change-1", None)  # the app's check passed: main is still trusted
    await queued_callbacks_ran()
    assert not approval.done()  # it waits for main's lock
    h.workspace.resume.set()
    with pytest.raises(WorkspaceError):
        await save
    await approval

    assert h.manager.main_suspect
    assert await h.main_head() == pre
    assert h.payloads(ChangeResolved) == []
    [error] = errors(h, "became suspect before this approval ran")
    assert (error.source, error.fatal, error.change_id) == ("server", False, "change-1")
    await h.manager.approve("change-1", None)  # `ready` again, not stuck deciding
    assert len(errors(h, "became suspect before this approval ran")) == 2
    await h.manager.reject("change-1", None)
    assert h.payloads(ChangeResolved)[-1].decision == "rejected"


@bounded
async def test_open_queued_behind_a_failing_save_refuses(h: Harness) -> None:
    save = await hold_a_save_that_ends_suspect(h)
    opening = asyncio.get_running_loop().create_task(h.open(2))
    await queued_callbacks_ran()
    assert not opening.done()  # it waits for main's lock
    h.workspace.resume.set()
    with pytest.raises(WorkspaceError):
        await save

    with pytest.raises(WrongState, match="state is unknown"):
        await opening
    assert not h.worktree_dir("agent-2").exists()
    assert not await h.has_branch("agent-2")


# --- The scan thread (M2) and a publish a cancel left running (M4) ----------------------------


@bounded
async def test_cancelled_approval_scan_holds_main_until_its_thread_ends(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    await h.run_agent(1, EDIT)
    content, base = await edit_main(h, TESTS, *LOOSER)
    loop = asyncio.get_running_loop()
    inside, gate = asyncio.Event(), threading.Event()
    order: list[str] = []
    real_scan, real_git = h.main_graph.scan, h.workspace.git
    calls: list[str] = []

    def scan(commit: str) -> GraphSnapshot:
        calls.append(commit)
        if len(calls) == 1:  # only the first scan waits
            loop.call_soon_threadsafe(inside.set)
            assert gate.wait(30)
            order.append("scan ended")
        return real_scan(commit)

    async def git(tree: Any, *args: str, **kwargs: Any) -> Any:
        order.append(f"git {args[0]}")
        return await real_git(tree, *args, **kwargs)

    monkeypatch.setattr(h.main_graph, "scan", scan)
    monkeypatch.setattr(h.workspace, "git", git)
    approval = h.manager.approve("change-1", None)
    await inside.wait()  # the merge is committed; its scan thread is running
    order.append("cancelled")
    approval.cancel()
    save = start_save(h, content, base, TESTS)
    await queued_callbacks_ran()
    gate.set()
    with pytest.raises(asyncio.CancelledError):
        await approval
    await save

    # No git call, the approval's recovery or the save, between the cancel and the thread's end.
    assert order[order.index("cancelled") + 1] == "scan ended"
    assert [d.cause for d in deltas(h)] == ["merge", "user_edit"]


@bounded
async def test_a_close_out_publish_a_cancel_left_running_is_waited_for(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A save queued behind an approval whose delta publish a double cancel leaves running drains
    that publish under the lock, so its own delta starts where the merge's ended."""
    await h.run_agent(1, EDIT)
    content, base = await edit_main(h, TESTS, *LOOSER)
    waited = spy_drains(monkeypatch)
    h.hold = GraphDelta
    approval = h.manager.approve("change-1", None)
    save = start_save(h, content, base, TESTS)  # past its `_finish_pending`: nothing was pending
    await h.held.wait()  # the store took the merge's delta; its receipt is on its way
    await cancel_twice(approval)  # the approval ends, its publish still held
    h.release.set()
    await save

    assert any(task is not approval for task in waited)  # waited for under main's lock
    merge, edit = deltas(h)
    assert (merge.cause, edit.cause) == ("merge", "user_edit")
    assert edit.from_version == merge.to_version


# --- A save among other work on main ----------------------------------------------------------


@bounded
async def test_save_waits_for_a_held_merge_then_saves_on_the_merge_commit(h: Harness) -> None:
    await h.run_agent(1, EDIT)
    content, base = await edit_main(h, TESTS, *LOOSER)
    h.workspace.arm("merge", mode="hold_after")
    approval = h.manager.approve("change-1", None)
    await h.workspace.entered.wait()  # main is mid-merge, which a save must not see as dirty
    save = start_save(h, content, base, TESTS)
    await queued_callbacks_ran()
    assert not save.done()
    h.workspace.resume.set()
    await approval
    await save

    merge_commit = h.payloads(ChangeResolved)[-1].merge_commit
    assert await log(h, "%P") == merge_commit
    assert [d.cause for d in deltas(h)] == ["merge", "user_edit"]
    await assert_main_clean(h)


@bounded
async def test_save_on_main_leaves_a_working_agents_change_untouched(h: Harness) -> None:
    await h.open(1)
    assert await h.edit(1, *EDIT)
    tree = h.workspace.worktree("agent-1")
    async with h.workspace.locks(agent_id="agent-1"):
        before = (await h.workspace.git(tree, "rev-parse", "HEAD")).stdout
    events = len(h.log)
    content, base = await edit_main(h, TESTS, *LOOSER)

    await h.manager.save_main(TESTS, content, base)

    assert [e.type for e in h.events()[events:]] == ["graph.delta"]
    async with h.workspace.locks(agent_id="agent-1"):
        assert (await h.workspace.git(tree, "rev-parse", "HEAD")).stdout == before
    assert await h.finish(1)
    assert h.payloads(ChangeProposed)[0].base_commit == h.base_commit  # the base did not move
    assert [u.file.path for u in h.payloads(ChangeUpdated)] == [PATH]
    await approve(h)  # main moved by the save: the merge is tested on the combination (R5)


@bounded
async def test_combined_tests_after_a_save_moved_main(h: Harness) -> None:
    await h.run_agent(1, EDIT)  # value() is 3: its own test passes
    content, base = await edit_main(h, TESTS, "assert value() > 0", "assert value() == 1")
    await h.manager.save_main(TESTS, content, base)  # passes alone on main, where value() is 1
    pre = await h.main_head()

    await h.manager.approve("change-1", None)

    assert await h.main_head() == pre
    await assert_main_clean(h)
    [resolved] = h.payloads(ChangeResolved)
    conflict = resolved.conflict
    assert conflict is not None and conflict.tests is not None
    assert (resolved.merge, conflict.kind) == ("conflict", "tests")
    assert conflict.tests.failed_tests == ["tests/test_mod.py::test_value"]
