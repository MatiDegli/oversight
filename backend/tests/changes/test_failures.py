"""Failure paths: a conflict, a timeout, a commit that fails after the merge, a failed cleanup, a
failed proposal and a broken main. Faults come from the armed workspace's `git`."""

import asyncio

import pytest
from changes_helpers import ADD_THRICE, Harness, bounded

from oversight.changes import WrongState
from oversight_contracts.events import AgentStatus, ChangeProposed, ChangeResolved, ErrorReport

pytestmark = pytest.mark.anyio

EDIT = ("shop/mod.py", "return 1", "return 3")


async def assert_main_clean_at(h: Harness, commit: str) -> None:
    assert await h.main_head() == commit
    assert (await h.main_git("status", "--porcelain")).stdout == ""
    assert (await h.main_git("rev-parse", "-q", "--verify", "MERGE_HEAD")).returncode == 1


async def commit_on_main(h: Harness) -> str:
    workspace = h.workspace
    async with workspace.locks(main=True):
        read = await workspace.main.read_text("shop/mod.py")
        changed = read.content.replace("return 1", "return 5")
        await workspace.main.write_text("shop/mod.py", changed, expected_hash=read.hash)
        await workspace.git_ok(workspace.main, "commit", "-qam", "Edit on main")
    return await h.main_head()


@bounded
async def test_conflict_is_reported_and_main_left_clean(h: Harness) -> None:
    await h.run_agent(1, EDIT)
    pre = await commit_on_main(h)

    await h.manager.approve("change-1", None)

    await assert_main_clean_at(h, pre)
    [resolved] = h.payloads(ChangeResolved)
    assert (resolved.merge, resolved.conflict and resolved.conflict.paths) == (
        "conflict",
        ["shop/mod.py"],
    )
    with pytest.raises(WrongState):
        h.manager.approve("change-1", None)
    await h.manager.reject("change-1", None)
    assert [r.decision for r in h.payloads(ChangeResolved)] == ["approved", "rejected"]
    assert "graph.delta" not in h.types()


@bounded
async def test_merge_timeout_leaves_main_unchanged_and_a_retry_merges(h: Harness) -> None:
    await h.run_agent(1, EDIT)
    pre = await h.main_head()
    h.workspace.arm("merge", mode="timeout")

    await h.manager.approve("change-1", None)

    await assert_main_clean_at(h, pre)
    [error] = h.payloads(ErrorReport)
    assert "main is unchanged" in error.message
    assert h.payloads(ChangeResolved) == []
    await h.manager.approve("change-1", None)
    assert [r.merge for r in h.payloads(ChangeResolved)] == ["merged"]


@bounded
async def test_commit_reported_as_timeout_counts_as_merged(h: Harness) -> None:
    await h.run_agent(1, EDIT)
    h.workspace.arm("commit", mode="after")

    await h.manager.approve("change-1", None)

    [resolved] = h.payloads(ChangeResolved)
    assert (resolved.merge, resolved.merge_commit) == ("merged", await h.main_head())
    assert h.types()[-3:] == ["change.resolved", "graph.delta", "agent.status"]
    assert h.payloads(ErrorReport) == []


@bounded
async def test_commit_failure_aborts_to_pre(h: Harness) -> None:
    await h.run_agent(1, EDIT)
    [proposal] = h.payloads(ChangeProposed)
    pre = await h.main_head()
    h.workspace.arm("commit")

    await h.manager.approve("change-1", None)

    await assert_main_clean_at(h, pre)
    assert h.payloads(ChangeResolved) == []
    assert "main is unchanged" in h.payloads(ErrorReport)[0].message
    await h.manager.approve("change-1", None)  # back to ready, so a retry merges
    parents = (await h.main_git("rev-list", "--parents", "-n", "1", "HEAD")).stdout.split()[1:]
    assert parents == [pre, proposal.head_commit]


@pytest.mark.parametrize("decision", ["approve", "reject"])
@bounded
async def test_cleanup_failure_still_closes(h: Harness, decision: str) -> None:
    await h.run_agent(1, ADD_THRICE)
    h.workspace.arm("branch")
    decide = h.manager.approve if decision == "approve" else h.manager.reject

    await decide("change-1", None)

    kinds = h.types()
    assert kinds.count("change.resolved") == 1
    assert kinds.count("graph.delta") == (1 if decision == "approve" else 0)
    assert [s.state for s in h.payloads(AgentStatus)] == ["done"]
    [error] = h.payloads(ErrorReport)
    assert "Cleaning up after change-1 failed" in error.message
    assert not h.worktree_dir("agent-1").exists()
    assert await h.has_branch("agent-1")
    with pytest.raises(WrongState):
        decide("change-1", None)

    await h.open(2)  # runs the pending cleanup first

    assert not await h.has_branch("agent-1")
    assert h.types().count("change.resolved") == 1


@bounded
async def test_failed_propose(h: Harness) -> None:
    await h.open(1)
    assert await h.edit(1, *EDIT)
    h.workspace.arm("commit", tree="agent-1")

    assert await h.finish(1) is False

    [error] = h.payloads(ErrorReport)
    assert (error.fatal, error.source, error.change_id) == (True, "git", "change-1")
    assert "change.proposed" not in h.types()
    with pytest.raises(WrongState):
        h.manager.approve("change-1", None)
    await h.manager.reject("change-1", None)
    [resolved] = h.payloads(ChangeResolved)
    assert (resolved.decision, resolved.revision) == ("rejected", 0)
    assert not await h.has_branch("agent-1")


@bounded
async def test_reject_from_broken(h: Harness) -> None:
    await h.run_agent(1, EDIT)
    pre = await h.main_head()
    h.workspace.arm("merge", mode="timeout")
    h.workspace.arm("status")  # the facts cannot be read

    await h.manager.approve("change-1", None)

    assert "state is unknown" in h.payloads(ErrorReport)[0].message
    with pytest.raises(WrongState):
        h.manager.approve("change-1", None)
    await h.manager.reject("change-1", "Main looked wrong.")

    [resolved] = h.payloads(ChangeResolved)
    assert (resolved.decision, resolved.revision, resolved.comment) == (
        "rejected",
        1,
        "Main looked wrong.",
    )
    assert h.types()[-1] == "agent.status"
    assert not h.worktree_dir("agent-1").exists()
    assert not await h.has_branch("agent-1")
    await assert_main_clean_at(h, pre)


@pytest.mark.parametrize("decision", ["approve", "reject"])
@bounded
async def test_decision_cancelled_before_it_starts(h: Harness, decision: str) -> None:
    """The task never runs a step, so its own `finally` never runs: the state still goes back."""
    await h.run_agent(1, EDIT)
    pre = await h.main_head()
    decide = h.manager.approve if decision == "approve" else h.manager.reject
    task = decide("change-1", None)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert task.cancelled()
    await assert_main_clean_at(h, pre)
    await h.manager.reject("change-1", "After the cancel.")  # ready again, so Reject works
    [resolved] = h.payloads(ChangeResolved)
    assert (resolved.decision, resolved.comment) == ("rejected", "After the cancel.")
    assert h.types()[-1] == "agent.status"
