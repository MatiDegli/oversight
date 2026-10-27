"""Request changes: the comment goes back to the agent while its worktree, branch and base stay;
from a conflict, main is merged into its branch first, so it redoes the change on the new base."""

import asyncio

import pytest
from changes_helpers import TEMPLATE, VALUE, Harness, bounded, queued_callbacks_ran

from oversight.changes import ChangeNotFound, Revision, WrongState
from oversight_contracts.events import (
    AgentStatus,
    ChangeProposed,
    ChangeResolved,
    ChangeUpdated,
    ErrorReport,
)

pytestmark = pytest.mark.anyio

EDIT = ("shop/mod.py", "return 1", "return 3")
USE = "use_it"
USE_FILE = "from shop.mod import value\n\n\ndef use_it() -> int:\n    return value()\n"
STRICT = "from shop.mod import twice\n\n\ndef test_twice() -> None:\n    assert twice() == 2\n"


async def commit_on_main(h: Harness, files: dict[str, str]) -> str:
    """Commit whole files on main, as an earlier approval would."""
    workspace = h.workspace
    async with workspace.locks(main=True):
        for path, content in files.items():
            try:
                old: str | None = (await workspace.main.read_text(path)).hash
            except FileNotFoundError:
                old = None
            await workspace.main.write_text(path, content, expected_hash=old)
        await workspace.git_ok(workspace.main, "add", "-A")
        await workspace.git_ok(workspace.main, "commit", "-qm", "Edit on main")
    return await h.main_head()


async def conflicted(h: Harness, extra: dict[str, str] | None = None) -> tuple[str, str]:
    """Agent 1 proposes `return 3`, main meanwhile says `return 5`, and Approve conflicts: the
    proposal's head and main's HEAD."""
    await h.run_agent(1, EDIT)
    main_mod = TEMPLATE["shop/mod.py"].replace("return 1", "return 5")
    main_head = await commit_on_main(h, {"shop/mod.py": main_mod, **(extra or {})})
    await h.manager.approve("change-1", None)
    assert h.payloads(ChangeResolved)[-1].merge == "conflict"
    return h.payloads(ChangeProposed)[-1].head_commit, main_head


async def worktree_git(h: Harness, *args: str) -> str:
    tree = h.workspace.worktree("agent-1")
    async with h.workspace.locks(agent_id="agent-1"):
        return (await h.workspace.git(tree, *args)).stdout


async def resolve_markers(h: Harness, text: str) -> None:
    """Replace the conflict block in shop/mod.py, as the agent would after reading it."""
    content = (await h.workspace.worktree("agent-1").read_text("shop/mod.py")).content
    start = content.index("<<<<<<< ")
    end = content.index("\n", content.index(">>>>>>> ")) + 1
    assert await h.edit(1, "shop/mod.py", content[start:end], text)


def decisions(h: Harness) -> list[str]:
    return [r.decision for r in h.payloads(ChangeResolved)]


def errors(h: Harness, words: str) -> list[ErrorReport]:
    return [e for e in h.payloads(ErrorReport) if words in e.message]


@bounded
async def test_revision_keeps_worktree_and_base(h: Harness) -> None:
    await h.run_agent(1, EDIT)
    [first] = h.payloads(ChangeProposed)

    revision = await h.manager.request_changes("change-1", "Make it 4.")

    assert revision == Revision("Make it 4.", None)
    [requested] = h.payloads(ChangeResolved)
    assert (requested.decision, requested.comment, requested.revision, requested.merge) == (
        "changes_requested",
        "Make it 4.",
        1,
        None,
    )
    assert h.worktree_dir("agent-1").exists() and await h.has_branch("agent-1")
    assert (await worktree_git(h, "rev-parse", "HEAD")).strip() == first.head_commit
    assert "agent.status" not in h.types()  # no `done`, no cleanup

    assert await h.edit(1, "shop/mod.py", "return 3", "return 4")
    assert await h.finish(1, steps=7)
    second = h.payloads(ChangeProposed)[-1]
    assert (second.revision, second.change_id, second.base_commit) == (
        2,
        "change-1",
        first.base_commit,
    )
    assert (await worktree_git(h, "rev-parse", f"{second.head_commit}^")).strip() == (
        first.head_commit
    )
    await h.manager.approve("change-1", None)
    order = [
        (e.type, e.payload.revision)
        for e in h.events()
        if isinstance(e.payload, ChangeProposed | ChangeResolved)
    ]
    assert order == [
        ("change.proposed", 1),
        ("change.resolved", 1),
        ("change.proposed", 2),
        ("change.resolved", 2),
    ]
    assert decisions(h) == ["changes_requested", "approved"]
    done = h.payloads(AgentStatus)[-1]
    assert (done.state, done.step, done.max_steps) == ("done", 7, 40)
    assert "return 4" in (await h.workspace.main.read_text("shop/mod.py")).content


@bounded
async def test_request_changes_states(h: Harness) -> None:
    with pytest.raises(ChangeNotFound):
        h.manager.request_changes("change-9", "Again.")
    await h.open(1)
    with pytest.raises(WrongState):
        h.manager.request_changes("change-1", "Again.")  # working
    assert await h.edit(1, *EDIT)
    assert await h.finish(1)

    task = h.manager.request_changes("change-1", "Again.")
    for decide in (h.manager.request_changes, h.manager.approve, h.manager.reject):
        with pytest.raises(WrongState):
            decide("change-1", "Again.")  # deciding
    assert await task is not None
    with pytest.raises(WrongState):
        h.manager.request_changes("change-1", "Again.")  # working: the revision runs
    with pytest.raises(WrongState):
        h.manager.reject("change-1", None)
    h.manager.run_ended("agent-1", None, steps=3)
    with pytest.raises(WrongState):
        h.manager.request_changes("change-1", "Again.")  # unproposed
    await h.manager.reject("change-1", None)
    with pytest.raises(WrongState):
        h.manager.request_changes("change-1", "Again.")  # closed
    assert decisions(h) == ["changes_requested", "rejected"]


@bounded
async def test_failed_revision_is_unproposed(h: Harness) -> None:
    await h.run_agent(1, EDIT)
    assert await h.manager.request_changes("change-1", "Make it 4.") is not None

    h.manager.run_ended("agent-1", None, steps=5)  # the revision ended without proposing

    for decide in (h.manager.request_changes, h.manager.approve):
        with pytest.raises(WrongState):
            decide("change-1", "Again.")
    await h.manager.reject("change-1", None)
    assert decisions(h) == ["changes_requested", "rejected"]
    assert h.payloads(ChangeResolved)[-1].revision == 1
    done = h.payloads(AgentStatus)[-1]
    assert (done.state, done.step, done.max_steps) == ("done", 5, 40)
    assert not h.worktree_dir("agent-1").exists()


@bounded
async def test_redo_on_the_new_base(h: Harness) -> None:
    old_head, main_head = await conflicted(h)
    shown = len(h.payloads(ChangeUpdated))

    revision = await h.manager.request_changes("change-1", "Redo it on main.")

    assert revision is not None and revision.comment == "Redo it on main."
    assert revision.note is not None and "conflict markers" in revision.note
    assert "shop/mod.py" in revision.note
    parents = (await worktree_git(h, "rev-list", "--parents", "-n", "1", "HEAD")).split()[1:]
    assert parents == [old_head, main_head]
    assert await worktree_git(h, "status", "--porcelain") == ""
    [update] = h.payloads(ChangeUpdated)[shown:]
    assert (update.file.path, update.author) == ("shop/mod.py", "agent")
    assert update.file.original is not None and "return 5" in update.file.original
    assert update.file.modified is not None and "<<<<<<< " in update.file.modified
    assert decisions(h) == ["approved", "changes_requested"]

    await resolve_markers(h, "    return 3\n")
    assert await h.finish(1)
    second = h.payloads(ChangeProposed)[-1]
    assert (second.revision, second.base_commit) == (2, main_head)
    assert [f.path for f in second.files] == ["shop/mod.py"]
    await h.manager.approve("change-1", None)
    assert h.payloads(ChangeResolved)[-1].merge == "merged"
    merged = (await h.workspace.main.read_text("shop/mod.py")).content
    assert "return 3" in merged and "<<<" not in merged


@bounded
async def test_base_move_failure_keeps_the_conflict(h: Harness) -> None:
    old_head, _ = await conflicted(h)
    h.workspace.arm("merge", tree="agent-1")

    assert await h.manager.request_changes("change-1", "Redo.") is None

    [error] = errors(h, "Requesting changes on change-1 failed")
    assert "unchanged" in error.message and not error.fatal
    assert (await worktree_git(h, "rev-parse", "HEAD")).strip() == old_head
    assert await worktree_git(h, "status", "--porcelain") == ""
    assert decisions(h) == ["approved"]
    assert not h.manager.main_suspect
    assert await h.manager.request_changes("change-1", "Redo.") is not None  # still `conflict`


@bounded
async def test_main_not_clean_keeps_the_conflict(h: Harness) -> None:
    old_head, _ = await conflicted(h)
    async with h.workspace.locks(main=True):
        await h.workspace.main.write_text("stray.txt", "left over\n", expected_hash=None)

    assert await h.manager.request_changes("change-1", "Redo.") is None

    [error] = errors(h, "main has uncommitted changes")
    assert "unchanged" in error.message
    assert (await worktree_git(h, "rev-parse", "HEAD")).strip() == old_head
    assert decisions(h) == ["approved"]


@bounded
async def test_move_shown_again_by_the_next_proposal(h: Harness) -> None:
    """A file the move could not show again stays unpublished; the next proposal shows it."""
    await conflicted(h)
    shown = len(h.payloads(ChangeUpdated))
    h.fail_once = ChangeUpdated

    assert await h.manager.request_changes("change-1", "Redo.") is not None

    assert errors(h, "shop/mod.py was not shown")
    assert len(h.payloads(ChangeUpdated)) == shown
    assert await h.finish(1)
    [update] = h.payloads(ChangeUpdated)[shown:]
    assert update.file.original is not None and "return 5" in update.file.original
    assert h.types()[-2:] == ["change.updated", "change.proposed"]


@bounded
async def test_redo_after_failed_combined_tests_names_them(h: Harness) -> None:
    await h.run_agent(1, EDIT)
    main_head = await commit_on_main(h, {"tests/test_twice.py": STRICT})
    await h.manager.approve("change-1", None)
    assert h.payloads(ChangeResolved)[-1].merge == "conflict"

    revision = await h.manager.request_changes("change-1", "Fix twice.")

    assert revision is not None and revision.note is not None
    assert "tests/test_twice.py::test_twice" in revision.note
    assert "conflict markers" not in revision.note
    parents = (await worktree_git(h, "rev-list", "--parents", "-n", "1", "HEAD")).split()[1:]
    assert parents[1] == main_head


@bounded
async def test_requested_kept_until_sent(h: Harness) -> None:
    """R2: the store refuses the resolution twice; the revision runs, and the resolution goes out
    before revision 2's proposal."""
    await h.run_agent(1, EDIT)
    h.fail_once, h.fail_times = ChangeResolved, 2

    assert await h.manager.request_changes("change-1", "Make it 4.") is not None

    assert decisions(h) == []
    assert errors(h, "The request for changes on change-1 was not sent")
    assert await h.edit(1, "shop/mod.py", "return 3", "return 4")
    assert await h.finish(1)
    order = [
        (e.type, e.payload.revision)
        for e in h.events()
        if isinstance(e.payload, ChangeProposed | ChangeResolved)
    ]
    assert order == [("change.proposed", 1), ("change.resolved", 1), ("change.proposed", 2)]
    [requested] = h.payloads(ChangeResolved)
    assert (requested.decision, requested.comment) == ("changes_requested", "Make it 4.")


@bounded
async def test_requested_still_unsent_fails_the_proposal(h: Harness) -> None:
    """R2: refused twice more at the proposal, which fails rather than precede it; twice more
    before Reject, whose close-out then sends it before `rejected`."""
    await h.run_agent(1, EDIT)
    h.fail_once, h.fail_times = ChangeResolved, 6
    assert await h.manager.request_changes("change-1", "Make it 4.") is not None
    assert await h.edit(1, "shop/mod.py", "return 3", "return 4")

    assert not await h.finish(1)  # unproposed

    assert len(h.payloads(ChangeProposed)) == 1
    [fatal] = errors(h, "its request for changes is still unsent")
    assert fatal.fatal
    with pytest.raises(WrongState):
        h.manager.approve("change-1", None)
    await h.manager.reject("change-1", None)
    assert decisions(h) == ["changes_requested", "rejected"]
    assert h.payloads(ChangeResolved)[0].comment == "Make it 4."
    assert h.fail_once is None  # every armed failure was used


@bounded
async def test_requested_unsent_at_shutdown_is_named(h: Harness) -> None:
    await h.run_agent(1, EDIT)
    h.fail_once, h.fail_times = ChangeResolved, 4
    assert await h.manager.request_changes("change-1", "Make it 4.") is not None
    h.manager.run_ended("agent-1", None, steps=4)

    await h.manager.aclose()

    [named] = errors(h, "Shutting down")
    assert "change.resolved changes_requested never sent" in named.message
    assert decisions(h) == []


@pytest.mark.parametrize("cut", ["merge", "commit", "abort"])
@bounded
async def test_cancel_after_worktree_merge(h: Harness, cut: str) -> None:
    """R3: the move is cut right after the worktree's merge, or its commit, by a cancel; recovery
    decides from the worktree's facts. A failing `merge --abort` leaves the change `broken`."""
    old_head, main_head = await conflicted(h)
    if cut == "commit":
        h.workspace.arm("commit", tree="agent-1", mode="cancel")
    else:
        h.workspace.arm("merge", tree="agent-1", mode="cancel")
        if cut == "abort":
            h.workspace.arm("merge", tree="agent-1")  # the recovery's `merge --abort`

    with pytest.raises(asyncio.CancelledError):
        await h.manager.request_changes("change-1", "Redo.")

    assert decisions(h) == ["approved"]
    [error] = errors(h, "Requesting changes on change-1 was cancelled")
    assert not h.manager.main_suspect
    if cut == "abort":
        assert "unknown" in error.message
        with pytest.raises(WrongState):
            h.manager.request_changes("change-1", "Redo.")  # broken
        await h.manager.reject("change-1", None)
        assert not h.worktree_dir("agent-1").exists() and not await h.has_branch("agent-1")
        assert decisions(h) == ["approved", "rejected"]
        return
    assert await worktree_git(h, "rev-parse", "-q", "--verify", "MERGE_HEAD") == ""
    assert await worktree_git(h, "status", "--porcelain") == ""
    head = (await worktree_git(h, "rev-parse", "HEAD")).strip()
    if cut == "merge":
        assert head == old_head and "unchanged" in error.message
    else:
        parents = (await worktree_git(h, "rev-list", "--parents", "-n", "1", head)).split()[1:]
        assert parents == [old_head, main_head] and "merged" in error.message
        assert "<<<<<<< " in (h.payloads(ChangeUpdated)[-1].file.modified or "")

    revision = await h.manager.request_changes("change-1", "Redo.")  # still `conflict`

    assert revision is not None and revision.note is not None
    assert "shop/mod.py" in revision.note
    parents = (await worktree_git(h, "rev-list", "--parents", "-n", "1", "HEAD")).split()[1:]
    assert parents == [old_head, main_head]
    assert decisions(h) == ["approved", "changes_requested"]


@bounded
async def test_second_cancel_during_the_recovery_ends_broken(h: Harness) -> None:
    """R3: a second cancel cuts the recovery before its `merge --abort` runs, so the worktree is
    still mid-merge: the change is `broken`, never put back to `conflict`, and main is not suspect
    (only the worktree's state is unknown)."""
    await conflicted(h)
    h.workspace.arm("merge", tree="agent-1", mode="cancel")  # the first cancel, after the merge
    h.workspace.arm("merge", tree="agent-1", mode="cut")  # the recovery's `merge --abort`

    with pytest.raises(asyncio.CancelledError):
        await h.manager.request_changes("change-1", "Redo.")

    assert not h.manager.main_suspect
    assert await worktree_git(h, "rev-parse", "-q", "--verify", "MERGE_HEAD") != ""
    [error] = errors(h, "Requesting changes on change-1 was cancelled")
    assert "unknown" in error.message
    with pytest.raises(WrongState):
        h.manager.request_changes("change-1", "Redo.")  # broken
    await h.manager.reject("change-1", None)
    assert not h.worktree_dir("agent-1").exists() and not await h.has_branch("agent-1")
    assert decisions(h) == ["approved", "rejected"]
    assert not h.manager.main_suspect


@pytest.mark.parametrize("kind", ["merge", "tests"])
@bounded
async def test_cancel_after_the_conflict_is_stored(h: Harness, kind: str) -> None:
    """The store took the approval's conflict, then the decision was cancelled before the publish
    returned: the record matches the store, so the change is `conflict`, not `ready`. A second
    Approve is refused, and Request changes moves the base, with the conflict in its note."""
    await h.run_agent(1, EDIT)
    if kind == "merge":
        main_mod = TEMPLATE["shop/mod.py"].replace("return 1", "return 5")
        main_head = await commit_on_main(h, {"shop/mod.py": main_mod})
    else:
        main_head = await commit_on_main(h, {"tests/test_twice.py": STRICT})
    h.hold = ChangeResolved
    approval = h.manager.approve("change-1", None)
    await h.held.wait()
    approval.cancel()
    await queued_callbacks_ran()
    assert not approval.done()  # it waits for the publish's end
    h.release.set()
    with pytest.raises(asyncio.CancelledError):
        await approval

    [resolved] = h.payloads(ChangeResolved)
    assert (resolved.decision, resolved.merge) == ("approved", "conflict")
    assert not h.manager.main_suspect
    with pytest.raises(WrongState, match="is conflict"):
        h.manager.approve("change-1", None)  # a second Approve would send a second resolution
    revision = await h.manager.request_changes("change-1", "Redo.")

    assert revision is not None and revision.note is not None
    if kind == "merge":
        assert "conflict markers" in revision.note and "shop/mod.py" in revision.note
    else:
        assert "tests failed" in revision.note
        assert "tests/test_twice.py::test_twice" in revision.note
    parents = (await worktree_git(h, "rev-list", "--parents", "-n", "1", "HEAD")).split()[1:]
    assert parents[1] == main_head  # the base moved
    assert decisions(h) == ["approved", "changes_requested"]


@bounded
async def test_redo_keeps_mains_callers(h: Harness) -> None:
    """R4: main added a caller of the changed function in a file the agent never touched; the
    revision's head comes from the merged tree, so the caller stays in the graph and the cone."""
    await conflicted(h, {"shop/use.py": USE_FILE})
    assert await h.manager.request_changes("change-1", "Redo.") is not None
    await resolve_markers(h, "    return 3\n")

    assert await h.finish(1)

    second = h.payloads(ChangeProposed)[-1]
    assert VALUE in second.graph.nodes_modified
    assert f"shop.use:{USE}" not in second.graph.nodes_removed
    assert f"shop.use:{USE}" in [entry.node_id for entry in second.impact.entries]
    assert [f.path for f in second.files] == ["shop/mod.py"]
