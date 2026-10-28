"""Close-out and cleanup when one step fails: a publish cut short, failing until a later retry, or
left running by cancels, a rescan that raises, tests that cannot start, and a worktree directory
that is already gone."""

import asyncio
import shutil
from pathlib import Path

import pytest
from changes_helpers import ADD_THRICE, Harness, bounded, cancel_twice
from decision_helpers import observed_target

from oversight.changes import WrongState
from oversight.workspace import SuiteRun, Tree, WorkspaceError
from oversight_contracts.events import AgentStatus, ChangeProposed, ChangeResolved, ErrorReport
from oversight_contracts.graph import GraphDelta, GraphSnapshot

pytestmark = pytest.mark.anyio


@bounded
async def test_publish_cut_short_sends_the_rest_once(h: Harness) -> None:
    await h.run_agent(1, ADD_THRICE)
    h.fail_once = GraphDelta

    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))

    assert h.types()[-3:] == ["change.resolved", "graph.delta", "agent.status"]
    assert h.types().count("change.resolved") == 1
    assert h.manager.snapshot.version == h.payloads(GraphDelta)[0].to_version
    assert not await h.has_branch("agent-1")


@bounded
async def test_each_event_gets_two_tries(h: Harness) -> None:
    """The resolution fails once and the delta fails once: each is tried twice, in order, so the
    approval closes out whole, with no `error`, and nothing goes twice."""
    await h.run_agent(1, ADD_THRICE)
    h.fail_script = [ChangeResolved, GraphDelta]

    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))

    assert h.fail_script == []
    assert h.types()[-3:] == ["change.resolved", "graph.delta", "agent.status"]
    assert h.payloads(ErrorReport) == []
    assert h.types().count("change.resolved") == 1
    assert not await h.has_branch("agent-1")


@bounded
async def test_cancel_during_a_publish_records_what_went_out(h: Harness) -> None:
    """Cancelled while the store's receipt for the delta is on its way: the delta counts as sent
    and the snapshot moves, so the next close-out sends only `done` and the chain holds."""
    await h.run_agent(1, ADD_THRICE)
    h.hold = GraphDelta
    approval = h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    await h.held.wait()  # committed, its receipt not yet back
    approval.cancel()
    h.release.set()

    with pytest.raises(asyncio.CancelledError):
        await approval

    assert h.types()[-2:] == ["change.resolved", "graph.delta"]
    await h.open(2)
    # `types` checks each delta's `from_version`: a resent delta would start from v0 at v1.
    assert h.types()[-3:] == ["change.resolved", "graph.delta", "agent.status"]
    [delta] = h.payloads(GraphDelta)
    assert h.manager.snapshot.version == delta.to_version
    assert not await h.has_branch("agent-1")


@bounded
async def test_second_cancel_still_records_what_went_out(h: Harness) -> None:
    """Cancelled twice while the store's receipt for the delta is on its way: the approval ends
    first, the publish stays on the change and records its outcome, so the next close-out sends
    only `done` and the chain holds."""
    await h.run_agent(1, ADD_THRICE)
    h.hold = GraphDelta
    approval = h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    await h.held.wait()  # committed, its receipt not yet back
    await cancel_twice(approval)  # returns before `release`: the second cancel ended the wait
    h.release.set()

    await h.open(2)

    # `types` checks each delta's `from_version`: a resent delta would start from v0 at v1.
    assert h.types()[-3:] == ["change.resolved", "graph.delta", "agent.status"]
    [delta] = h.payloads(GraphDelta)
    assert h.manager.snapshot.version == delta.to_version
    assert not await h.has_branch("agent-1")


@bounded
async def test_merge_waits_for_a_publish_left_running(h: Harness) -> None:
    """agent-2's approval passes its retries, then queues on main's lock behind agent-1's, whose
    delta is held and which is cancelled twice: agent-2's merge waits for that receipt, so its
    delta starts from agent-1's, and agent-1's `done` follows at the next `open`."""
    first = h.manager.snapshot
    await h.run_agent(1, ADD_THRICE)
    await h.run_agent(2, ("shop/mod.py", "return 1", "return 4"))
    h.hold = GraphDelta
    approval = h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    second = h.manager.approve("change-2", None, **observed_target(h.manager, "change-2"))
    await h.held.wait()
    await cancel_twice(approval)
    h.release.set()

    await second
    await h.open(3)

    one, two = h.payloads(GraphDelta)  # `events()` checks that they chain
    assert (one.from_version, two.to_version) == (first.version, h.manager.snapshot.version)
    assert [s.agent_id for s in h.payloads(AgentStatus)] == ["agent-2", "agent-1"]
    assert [r.merge for r in h.payloads(ChangeResolved)] == ["merged", "merged"]
    assert not await h.has_branch("agent-1")


@bounded
async def test_rescan_failure_keeps_the_snapshot(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    scan, failed = h.main_graph.scan, list[str]()

    def scan_once_failing(commit: str) -> GraphSnapshot:
        if not failed:
            failed.append(commit)
            raise OSError("an armed scan failure")
        return scan(commit)

    monkeypatch.setattr(h.main_graph, "scan", scan_once_failing)
    first = h.manager.snapshot
    await h.run_agent(1, ADD_THRICE)

    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))

    [error] = h.payloads(ErrorReport)
    assert (error.source, error.fatal) == ("graph", False)
    assert "graph.delta" not in h.types()
    assert h.manager.snapshot is first
    await h.run_agent(2, ("shop/mod.py", "return 1", "return 4"))
    await h.manager.approve("change-2", None, **observed_target(h.manager, "change-2"))
    [delta] = h.payloads(GraphDelta)  # both merges, from what the UI holds
    assert delta.from_version == first.version
    assert [r.merge for r in h.payloads(ChangeResolved)] == ["merged", "merged"]


@bounded
async def test_tests_that_cannot_start_still_propose(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_tests(tree: Tree, *, timeout_s: float) -> SuiteRun:
        raise WorkspaceError("an armed test start failure")

    monkeypatch.setattr(h.workspace, "run_tests", no_tests)
    await h.open(1)

    assert await h.finish(1)

    [proposal] = h.payloads(ChangeProposed)
    assert proposal.tests.outcome == "error"
    [error] = h.payloads(ErrorReport)
    assert (error.source, error.fatal) == ("tests", False)


@bounded
async def test_cleanup_prunes_a_worktree_already_gone(h: Harness) -> None:
    tree = await h.run_agent(1, ("shop/mod.py", "return 1", "return 3"))
    await asyncio.to_thread(shutil.rmtree, Path(tree.root))

    await h.manager.reject("change-1", None, **observed_target(h.manager, "change-1"))

    assert h.payloads(ErrorReport) == []
    assert not await h.has_branch("agent-1")
    listing = (await h.main_git("worktree", "list", "--porcelain")).stdout
    assert str(tree.root) not in listing


@bounded
async def test_close_out_stays_pending_until_sent(h: Harness) -> None:
    """Both tries of the resolution fail: nothing goes out, the worktree and branch stay, and the
    next `open` sends the resolution and `done`, then cleans up."""
    await h.run_agent(1, ("shop/mod.py", "return 1", "return 3"))
    h.fail_once, h.fail_times = ChangeResolved, 2

    await h.manager.reject("change-1", "Not this.", **observed_target(h.manager, "change-1"))

    assert h.worktree_dir("agent-1").exists()
    assert await h.has_branch("agent-1")
    assert h.payloads(ChangeResolved) == []
    [error] = h.payloads(ErrorReport)
    assert "close-out of change-1 is incomplete" in error.message
    with pytest.raises(WrongState):
        h.manager.reject("change-1", None, **observed_target(h.manager, "change-1"))

    await h.open(2)

    [resolved] = h.payloads(ChangeResolved)
    assert (resolved.decision, resolved.comment) == ("rejected", "Not this.")
    assert h.types()[-2:] == ["change.resolved", "agent.status"]
    assert not h.worktree_dir("agent-1").exists()
    assert not await h.has_branch("agent-1")


@pytest.mark.parametrize(
    ("failures", "deltas", "done"),
    [(2, 2, ["agent-1", "agent-2"]), (4, 1, ["agent-2", "agent-1"])],
)
@bounded
async def test_pending_delta_is_retried_or_superseded(
    h: Harness, failures: int, deltas: int, done: list[str]
) -> None:
    """agent-1's delta fails twice in its approval. With 2 failures, agent-2's approval sends it
    first; with 4, it fails there too, so agent-2's delta, from the graph the UI holds, covers both
    merges and agent-1's is never sent; its `done` follows at the next `open`."""
    first = h.manager.snapshot
    await h.run_agent(1, ADD_THRICE)
    await h.run_agent(2, ("shop/mod.py", "return 1", "return 4"))
    h.fail_once, h.fail_times = GraphDelta, failures

    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    await h.manager.approve("change-2", None, **observed_target(h.manager, "change-2"))
    await h.open(3)

    sent = h.payloads(GraphDelta)  # `events()` checks that they chain
    assert (len(sent), sent[0].from_version) == (deltas, first.version)
    assert sent[-1].to_version == h.manager.snapshot.version
    assert [s.agent_id for s in h.payloads(AgentStatus)] == done
    assert [r.merge for r in h.payloads(ChangeResolved)] == ["merged", "merged"]
    assert not await h.has_branch("agent-1")
