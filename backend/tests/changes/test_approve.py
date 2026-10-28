"""Approve: a --no-ff merge into main, then the resolution, one delta and `done`, then cleanup."""

import pytest
from changes_helpers import ADD_THRICE, GIT_TIMEOUT_S, THRICE, Harness, bounded
from decision_helpers import observed_target

from oversight.changes import ChangeNotFound, WrongState
from oversight_contracts.events import AgentStatus, ChangeProposed, ChangeResolved
from oversight_contracts.graph import GraphDelta

pytestmark = pytest.mark.anyio


@bounded
async def test_approve_merges_with_a_merge_commit(h: Harness) -> None:
    await h.run_agent(1, ADD_THRICE)
    [proposal] = h.payloads(ChangeProposed)
    pre = await h.main_head()
    before = len(h.workspace.calls)

    await h.manager.approve("change-1", "Looks good.", **observed_target(h.manager, "change-1"))
    decision = h.workspace.calls[before:]

    merge, *parents = (await h.main_git("rev-list", "--parents", "-n", "1", "HEAD")).stdout.split()
    assert parents == [pre, proposal.head_commit]
    assert h.types()[-3:] == ["change.resolved", "graph.delta", "agent.status"]
    [resolved] = h.payloads(ChangeResolved)
    assert (resolved.decision, resolved.merge, resolved.merge_commit) == (
        "approved",
        "merged",
        merge,
    )
    assert (resolved.comment, resolved.revision) == ("Looks good.", 1)
    [delta] = h.payloads(GraphDelta)
    assert ([n.id for n in delta.nodes_added], delta.commit, delta.cause) == (
        [THRICE],
        merge,
        "merge",
    )
    assert h.manager.snapshot.version == delta.to_version
    done = h.payloads(AgentStatus)[-1]
    assert (done.state, done.step, done.model) == ("done", 3, "fake")
    assert not h.worktree_dir("agent-1").exists()
    assert not await h.has_branch("agent-1")
    # Every git call of the decision held main's lock, then the agent's, with the timeout.
    assert {c.held for c in decision} == {("main", "agent-1")}
    merges = [c for c in decision if c.args[0] in ("merge", "commit")]
    assert [c.args[:3] for c in merges] == [
        ("merge", "--no-ff", "--no-commit"),
        ("commit", "-q", "-m"),
    ]
    assert all(c.timeout_s == GIT_TIMEOUT_S for c in merges)


@bounded
async def test_early_repeated_and_unknown_approvals_fail(h: Harness) -> None:
    await h.open(1)
    with pytest.raises(WrongState):
        h.manager.approve(
            "change-1", None, **observed_target(h.manager, "change-1")
        )  # still working
    assert await h.edit(1, "shop/mod.py", "return 1", "return 3")
    assert await h.finish(1)

    task = h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    with pytest.raises(WrongState):
        h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))  # deciding
    await task
    with pytest.raises(WrongState):
        h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))  # closed
    with pytest.raises(ChangeNotFound):
        h.manager.approve("change-9", None, **observed_target(h.manager, "change-9"))
    assert len(h.payloads(ChangeResolved)) == 1


@bounded
async def test_body_only_edit_chains(h: Harness) -> None:
    first = h.manager.snapshot.version
    await h.run_agent(1, ("shop/mod.py", "return 1", "return 3"))
    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))

    [empty] = h.payloads(GraphDelta)  # graph_delta finds no change, but the version moved
    assert (empty.from_version, empty.nodes_changed, empty.edges_added) == (first, [], [])
    assert empty.to_version == h.manager.snapshot.version != first

    await h.run_agent(2, ("shop/mod.py", "return 3", "return 4"))
    await h.manager.approve("change-2", None, **observed_target(h.manager, "change-2"))
    second = h.payloads(GraphDelta)[1]
    assert second.from_version == empty.to_version
    assert [r.decision for r in h.payloads(ChangeResolved)] == ["approved", "approved"]


@bounded
async def test_empty_change_approves_as_a_merge_of_nothing(h: Harness) -> None:
    await h.open(1)
    assert await h.finish(1)
    pre = await h.main_head()

    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))

    assert await h.main_head() == pre
    [resolved] = h.payloads(ChangeResolved)
    assert (resolved.merge, resolved.merge_commit) == ("merged", pre)
    [delta] = h.payloads(GraphDelta)
    assert delta.from_version == delta.to_version
    assert not await h.has_branch("agent-1")
