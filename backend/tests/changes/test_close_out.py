"""Close-out and cleanup when one step fails: a publish cut short, a rescan that raises, tests that
cannot start, and a worktree directory that is already gone."""

import asyncio
import shutil
from pathlib import Path

import pytest
from changes_helpers import ADD_THRICE, Harness, bounded

from oversight.workspace import SuiteRun, Tree, WorkspaceError
from oversight_contracts.events import ChangeProposed, ChangeResolved, ErrorReport
from oversight_contracts.graph import GraphDelta, GraphSnapshot

pytestmark = pytest.mark.anyio


@bounded
async def test_publish_cut_short_sends_the_rest_once(h: Harness) -> None:
    await h.run_agent(1, ADD_THRICE)
    h.fail_once = GraphDelta

    await h.manager.approve("change-1", None)

    assert h.types()[-3:] == ["change.resolved", "graph.delta", "agent.status"]
    assert h.types().count("change.resolved") == 1
    assert h.manager.snapshot.version == h.payloads(GraphDelta)[0].to_version
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

    await h.manager.approve("change-1", None)

    [error] = h.payloads(ErrorReport)
    assert (error.source, error.fatal) == ("graph", False)
    assert "graph.delta" not in h.types()
    assert h.manager.snapshot is first
    await h.run_agent(2, ("shop/mod.py", "return 1", "return 4"))
    await h.manager.approve("change-2", None)
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

    await h.manager.reject("change-1", None)

    assert h.payloads(ErrorReport) == []
    assert not await h.has_branch("agent-1")
    listing = (await h.main_git("worktree", "list", "--porcelain")).stdout
    assert str(tree.root) not in listing
