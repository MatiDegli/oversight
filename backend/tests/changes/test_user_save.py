"""The person's save in review: committed to the agent's worktree as the user and re-proposed with
fresh tests and cone, and Approve merging only the revision it was clicked on (R1, R6, R9)."""

import asyncio

import pytest
from changes_helpers import (
    ADD_THRICE,
    TEMPLATE,
    TWICE,
    VALUE,
    Harness,
    Mode,
    bounded,
    queued_callbacks_ran,
)
from decision_helpers import observed_target

from oversight.changes import ChangeNotFound, WrongState
from oversight.workspace import StaleFile, WorkspaceError, content_hash
from oversight_contracts.events import ChangeProposed, ChangeResolved, ChangeUpdated, ErrorReport

pytestmark = pytest.mark.anyio

EDIT = ("shop/mod.py", "return 1", "return 3")
PATH = "shop/mod.py"


async def worktree_git(h: Harness, *args: str) -> str:
    tree = h.workspace.worktree("agent-1")
    async with h.workspace.locks(agent_id="agent-1"):
        return (await h.workspace.git(tree, *args)).stdout


async def new_side(h: Harness, old: str, new: str) -> tuple[str, str]:
    """What the person saves from agent-1's version of PATH, and the hash they started from."""
    read = await h.workspace.worktree("agent-1").read_text(PATH)
    assert read.content.count(old) == 1
    return read.content.replace(old, new), read.hash


def start_save(h: Harness, content: str, base: str) -> asyncio.Task[str]:
    """The save in its own task, as a request's, so a test can cancel it."""
    save = h.manager.save_for_review("agent-1", PATH, content, base)
    return asyncio.get_running_loop().create_task(save)


async def main_text(h: Harness) -> str:
    return (await h.workspace.main.read_text(PATH)).content


def errors(h: Harness, words: str) -> list[ErrorReport]:
    return [e for e in h.payloads(ErrorReport) if words in e.message]


def revisions(h: Harness) -> list[tuple[str, int]]:
    return [
        (e.type, e.payload.revision)
        for e in h.events()
        if isinstance(e.payload, ChangeProposed | ChangeResolved)
    ]


def cone(proposal: ChangeProposed) -> list[str]:
    return [entry.node_id for entry in proposal.impact.entries]


@bounded
async def test_save_commits_as_the_user(h: Harness) -> None:
    await h.run_agent(1, EDIT)
    [first] = h.payloads(ChangeProposed)
    content, base = await new_side(h, "return 3", "return 4")

    saved = await h.manager.save_for_review("agent-1", PATH, content, base)

    assert saved == content_hash(content.encode())
    head = (await worktree_git(h, "rev-parse", "HEAD")).strip()
    commit = (await worktree_git(h, "log", "-1", "--format=%an <%ae> %P")).strip()
    assert commit == f"User <user@oversight.invalid> {first.head_commit}"
    assert await worktree_git(h, "status", "--porcelain") == ""
    assert h.types()[-2:] == ["change.updated", "change.proposed"]
    update = h.payloads(ChangeUpdated)[-1]
    assert (update.author, update.file.modified, update.node_ids) == ("user", content, [VALUE])
    second = h.payloads(ChangeProposed)[-1]
    assert (second.revision, second.head_commit, second.base_commit) == (
        2,
        head,
        first.base_commit,
    )
    assert (second.summary, second.tests.outcome) == ("Done.", "passed")  # the agent's summary

    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))

    resolved = h.payloads(ChangeResolved)[-1]
    assert (resolved.merge, resolved.revision) == ("merged", 2)
    assert await main_text(h) == content


@bounded
async def test_save_needs_ready_and_the_hash_it_started_from(h: Harness) -> None:
    with pytest.raises(ChangeNotFound):
        await h.manager.save_for_review("agent-9", PATH, "x = 1\n", "0" * 64)
    await h.open(1)
    assert await h.edit(1, *EDIT)
    content, base = await new_side(h, "return 3", "return 4")
    with pytest.raises(WrongState, match="is working"):
        await h.manager.save_for_review("agent-1", PATH, content, base)
    assert await h.finish(1)
    shown = len(h.log)

    with pytest.raises(StaleFile):
        await h.manager.save_for_review("agent-1", PATH, content, "0" * 64)

    assert len(h.log) == shown
    assert await worktree_git(h, "status", "--porcelain") == ""
    await h.open(2)
    h.manager.run_ended("agent-2", None, steps=0)
    with pytest.raises(WrongState, match="is unproposed"):
        await h.manager.save_for_review("agent-2", PATH, content, base)

    # Main moves under agent-1, so its approval conflicts; a conflict is not editable (answer 6).
    async with h.workspace.locks(main=True):
        main_mod = TEMPLATE[PATH].replace("return 1", "return 5")
        old = (await h.workspace.main.read_text(PATH)).hash
        await h.workspace.main.write_text(PATH, main_mod, expected_hash=old)
        await h.workspace.git_ok(h.workspace.main, "commit", "-qam", "Edit on main")
    approval = h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    with pytest.raises(WrongState, match="is deciding"):
        await h.manager.save_for_review("agent-1", PATH, content, base)
    await approval
    with pytest.raises(WrongState, match="is conflict"):
        await h.manager.save_for_review("agent-1", PATH, content, base)
    await h.manager.reject("change-1", None, **observed_target(h.manager, "change-1"))
    with pytest.raises(WrongState, match="is closed"):
        await h.manager.save_for_review("agent-1", PATH, content, base)
    assert [u.author for u in h.payloads(ChangeUpdated)] == ["agent"]


@bounded
async def test_save_rechecks_under_the_lock(h: Harness) -> None:
    """Reject is clicked while the save waits for the agent's lock: the save finds the change
    taken and writes nothing, and Reject closes revision 1."""
    await h.run_agent(1, EDIT)
    content, base = await new_side(h, "return 3", "return 4")
    async with h.workspace.locks(agent_id="agent-1"):
        save = start_save(h, content, base)
        await queued_callbacks_ran()
        assert not save.done()  # it waits for the lock
        rejection = h.manager.reject("change-1", None, **observed_target(h.manager, "change-1"))

    with pytest.raises(WrongState, match="is deciding"):
        await save

    await rejection
    assert revisions(h) == [("change.proposed", 1), ("change.resolved", 1)]
    assert [u.author for u in h.payloads(ChangeUpdated)] == ["agent"]


CUT_MODES: dict[str, Mode] = {"commit": "cancel", "rev-parse": "fail", "add": "cut"}


@pytest.mark.parametrize("cut", list(CUT_MODES))
@bounded
async def test_cut_save_is_never_approved_stale(h: Harness, cut: str) -> None:
    """R1: a save cut short after its write: cancelled right after its commit, failing in the
    `rev-parse` after it, or cancelled before `add`, with nothing committed. Approve merges
    nothing: it re-proposes what the worktree holds as the user's revision 2 and names it; the
    next Approve puts the person's text on main."""
    await h.run_agent(1, EDIT)
    content, base = await new_side(h, "return 3", "return 4")
    h.workspace.arm(cut, tree="agent-1", mode=CUT_MODES[cut])
    save = start_save(h, content, base)
    with pytest.raises(WorkspaceError if cut == "rev-parse" else asyncio.CancelledError):
        await save
    assert h.workspace.faults[0].left == 0
    assert len(h.payloads(ChangeProposed)) == 1
    pre = await h.main_head()

    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))

    assert await h.main_head() == pre
    assert h.payloads(ChangeResolved) == []
    head = (await worktree_git(h, "rev-parse", "HEAD")).strip()
    assert (await worktree_git(h, "log", "-1", "--format=%an")).strip() == "User"
    second = h.payloads(ChangeProposed)[-1]
    assert (second.revision, second.head_commit, second.summary) == (2, head, "Done.")
    update = h.payloads(ChangeUpdated)[-1]
    assert (update.author, update.file.modified) == ("user", content)
    [error] = errors(h, "Revision 2 of change-1 holds edits that were not proposed")
    assert not error.fatal

    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))

    resolved = h.payloads(ChangeResolved)[-1]
    assert (resolved.merge, resolved.revision) == ("merged", 2)
    assert await main_text(h) == content


async def cut_before_commit(h: Harness) -> str:
    """A save cancelled before its `add`: the file is written, nothing committed. Its content."""
    await h.run_agent(1, EDIT)
    content, base = await new_side(h, "return 3", "return 4")
    h.workspace.arm("add", tree="agent-1", mode="cut")
    with pytest.raises(asyncio.CancelledError):
        await start_save(h, content, base)
    return content


@pytest.mark.parametrize("where", ["commit", "publish"])
@bounded
async def test_approve_cut_in_its_reproposal(h: Harness, where: str) -> None:
    """R1 from facts: Approve's own re-proposal of a cut save is cancelled right after its commit,
    or while the store's receipt of revision 2 is on its way. Nothing merges and the change is
    `ready`; the next Approve finds the worktree as that cancel left it, proposes revision 2 once
    if the store lacks it, and merges only after revision 2 was clicked on."""
    content = await cut_before_commit(h)
    pre = await h.main_head()
    if where == "commit":
        h.workspace.arm("commit", tree="agent-1", mode="cancel")
    else:
        h.hold = ChangeProposed
    approval = h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    if where == "publish":
        await h.held.wait()
        approval.cancel()
        await queued_callbacks_ran()
        assert not approval.done()  # it waits for the publish's end
        h.release.set()
    with pytest.raises(asyncio.CancelledError):
        await approval
    assert await h.main_head() == pre
    assert (await worktree_git(h, "log", "-1", "--format=%an")).strip() == "User"

    if where == "commit":
        await h.manager.approve(
            "change-1", None, **observed_target(h.manager, "change-1")
        )  # proposes revision 2 and names it
        assert errors(h, "Revision 2 of change-1 holds edits")
    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))

    assert revisions(h) == [("change.proposed", 1), ("change.proposed", 2), ("change.resolved", 2)]
    assert h.payloads(ChangeResolved)[-1].merge == "merged"
    assert await main_text(h) == content


@pytest.mark.parametrize("subcommand", ["rev-parse", "status"])
@bounded
async def test_approve_merges_nothing_when_the_worktree_cannot_be_read(
    h: Harness, subcommand: str
) -> None:
    await h.run_agent(1, EDIT)
    pre = await h.main_head()
    h.workspace.arm(subcommand, tree="agent-1")

    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))

    assert h.workspace.faults[0].left == 0
    assert await h.main_head() == pre
    [error] = errors(h, "Approving change-1 merged nothing")
    assert (error.source, error.fatal) == ("git", False)
    assert h.payloads(ChangeResolved) == []
    await h.manager.approve(
        "change-1", None, **observed_target(h.manager, "change-1")
    )  # `ready` again
    assert h.payloads(ChangeResolved)[-1].merge == "merged"


@bounded
async def test_approve_wants_the_revision_it_saw(h: Harness) -> None:
    """R1: Approve is clicked on revision 1 while the save's revision 2 waits before the store:
    nothing merges, an `error` names revision 2, and the change is `ready` for its review."""
    await h.run_agent(1, EDIT)
    content, base = await new_side(h, "return 3", "return 4")
    pre = await h.main_head()
    h.gate = ChangeProposed
    save = start_save(h, content, base)
    await h.at_gate.wait()
    approval = h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    h.open_gate.set()
    await save

    await approval

    assert await h.main_head() == pre
    assert revisions(h) == [("change.proposed", 1), ("change.proposed", 2)]
    [error] = errors(h, "Revision 2 of change-1 arrived after you clicked")
    assert not error.fatal
    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    resolved = h.payloads(ChangeResolved)[-1]
    assert (resolved.merge, resolved.revision) == ("merged", 2)
    assert await main_text(h) == content


@pytest.mark.parametrize("cancels", [1, 2])
@bounded
async def test_save_cancelled_in_its_publish_is_recorded(h: Harness, cancels: int) -> None:
    """The store took the save's revision 2, then the save was cancelled, once or twice, before
    hearing it: the record matches the store, so Approve merges revision 2 and never proposes it
    again."""
    await h.run_agent(1, EDIT)
    content, base = await new_side(h, "return 3", "return 4")
    h.hold = ChangeProposed
    save = start_save(h, content, base)
    await h.held.wait()  # committed, its receipt not yet back
    for _ in range(cancels):
        save.cancel()
        await queued_callbacks_ran()
    assert not save.done()  # it waits for the publish's end
    h.release.set()
    with pytest.raises(asyncio.CancelledError):
        await save

    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))

    assert revisions(h) == [("change.proposed", 1), ("change.proposed", 2), ("change.resolved", 2)]
    assert h.payloads(ChangeResolved)[-1].merge == "merged"
    assert await main_text(h) == content
    assert h.payloads(ErrorReport) == []


@bounded
async def test_save_reproposes_with_a_new_cone(h: Harness) -> None:
    """R6: the agent added `thrice`; the person's save edits `value`, which `twice` calls, and
    breaks its test: revision 2 has `twice` in its cone and the failing test, both from the saved
    head."""
    await h.run_agent(1, ADD_THRICE)
    [first] = h.payloads(ChangeProposed)
    assert TWICE not in cone(first) and first.tests.outcome == "passed"
    content, base = await new_side(h, "return 1", "return 0")

    await h.manager.save_for_review("agent-1", PATH, content, base)

    second = h.payloads(ChangeProposed)[-1]
    assert (second.revision, second.summary) == (2, "Done.")
    assert VALUE in second.graph.nodes_modified
    assert TWICE in cone(second)
    assert (second.tests.outcome, second.tests.failed_tests) == (
        "failed",
        ["tests/test_mod.py::test_value"],
    )


@bounded
async def test_identical_save_is_a_no_op(h: Harness) -> None:
    """R9: the same content stages nothing: no commit, no event, and the hash comes back."""
    await h.run_agent(1, EDIT)
    read = await h.workspace.worktree("agent-1").read_text(PATH)
    head = (await worktree_git(h, "rev-parse", "HEAD")).strip()
    shown = len(h.log)

    saved = await h.manager.save_for_review("agent-1", PATH, read.content, read.hash)

    assert saved == read.hash
    assert len(h.log) == shown
    assert (await worktree_git(h, "rev-parse", "HEAD")).strip() == head


@pytest.mark.parametrize("decision", ["request_changes", "reject"])
@bounded
async def test_decision_during_a_save_names_its_revision(h: Harness, decision: str) -> None:
    """Request changes or Reject is clicked while the save's revision 2 waits before the store: the
    decision waits for the save and refuses its stale observed target without resolving or
    resuming. The person must review revision 2 before another decision."""
    await h.run_agent(1, EDIT)
    content, base = await new_side(h, "return 3", "return 4")
    h.gate = ChangeProposed
    save = start_save(h, content, base)
    await h.at_gate.wait()
    if decision == "request_changes":
        task: asyncio.Task[object] = h.manager.request_changes(
            "change-1", "Make it 5.", **observed_target(h.manager, "change-1")
        )
    else:
        task = h.manager.reject("change-1", None, **observed_target(h.manager, "change-1"))
    for _ in range(3):
        await queued_callbacks_ran()  # time enough for a decision that did not wait to resolve
    h.open_gate.set()
    await save

    await task

    assert revisions(h) == [("change.proposed", 1), ("change.proposed", 2)]
    assert h.manager._agents["agent-1"].state == "ready"
    assert not h.payloads(ChangeResolved)
    assert errors(h, "target changed")


@bounded
async def test_request_cancelled_while_waiting_for_the_locks(h: Harness) -> None:
    """Request changes from `ready` waits for main's lock, as every decision does; cancelled
    there, it sends nothing and leaves the change `ready`."""
    await h.run_agent(1, EDIT)
    async with h.workspace.locks(main=True):
        request = h.manager.request_changes(
            "change-1", "Make it 4.", **observed_target(h.manager, "change-1")
        )
        await queued_callbacks_ran()
        assert not request.done()  # it waits for main's lock
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request

    assert h.payloads(ChangeResolved) == []
    await h.manager.approve(
        "change-1", None, **observed_target(h.manager, "change-1")
    )  # still `ready`
    assert h.payloads(ChangeResolved)[-1].merge == "merged"
