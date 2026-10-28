"""Reject: a discard that leaves main as it was, from a proposal or from a halt with none."""

import asyncio

import pytest
from changes_helpers import Harness, bounded
from decision_helpers import observed_target

from oversight.agents import RunEnd
from oversight.changes import WrongState
from oversight_contracts.events import AgentStatus, ChangeResolved, ErrorReport

pytestmark = pytest.mark.anyio


@bounded
async def test_reject_discards_and_leaves_main(h: Harness) -> None:
    await h.run_agent(1, ("shop/mod.py", "return 1", "return 3"))
    pre, snapshot = await h.main_head(), h.manager.snapshot

    await h.manager.reject("change-1", "Not this way.", **observed_target(h.manager, "change-1"))

    assert await h.main_head() == pre
    assert h.manager.snapshot is snapshot
    assert not h.worktree_dir("agent-1").exists()
    assert not await h.has_branch("agent-1")
    assert h.types()[-2:] == ["change.resolved", "agent.status"]
    [resolved] = h.payloads(ChangeResolved)
    assert (resolved.decision, resolved.comment, resolved.revision) == (
        "rejected",
        "Not this way.",
        1,
    )
    assert (resolved.merge, resolved.merge_commit) == (None, None)
    assert "graph.delta" not in h.types()


@bounded
async def test_reject_after_a_halt_with_no_proposal(h: Harness) -> None:
    """The runtime's _RootLost guard: the first scan found the root swapped for a symlink, so the
    run halts without calling `proposed`. The change is unproposed and Reject closes revision 0."""
    tree = await h.open(1)
    moved = tree.root.with_name("moved")
    await asyncio.to_thread(tree.root.rename, moved)
    await asyncio.to_thread(tree.root.symlink_to, moved)
    h.manager.run_ended("agent-1", RunEnd("halted", "The worktree cannot be used.", 0, None))
    with pytest.raises(WrongState):
        h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))

    await h.manager.reject("change-1", None, **observed_target(h.manager, "change-1"))

    assert "change.proposed" not in h.types()
    [resolved] = h.payloads(ChangeResolved)
    assert (resolved.decision, resolved.revision) == ("rejected", 0)
    assert h.payloads(AgentStatus)[-1].state == "done"
    [error] = h.payloads(ErrorReport)  # the workspace's guard refuses the swapped root
    assert (error.source, error.fatal) == ("git", False)
    assert moved.exists()
