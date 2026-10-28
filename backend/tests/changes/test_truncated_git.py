"""Incomplete Git queries refuse proposals, merges and cleanup without trusting partial data."""

from dataclasses import replace
from typing import Any

import pytest
from changes_helpers import EDIT, Harness, bounded
from decision_helpers import observed_target

from oversight.workspace import ProcessResult, Tree
from oversight_contracts.events import ChangeProposed, ChangeResolved, ErrorReport

pytestmark = pytest.mark.anyio


def truncate_once(
    h: Harness, monkeypatch: pytest.MonkeyPatch, command: str, *, tree: str = "agent-1"
) -> list[tuple[str, ...]]:
    real = h.workspace.git
    hits: list[tuple[str, ...]] = []

    async def git(target: Tree, *args: str, **kwargs: Any) -> ProcessResult:
        result = await real(target, *args, **kwargs)
        if not hits and (target.agent_id or "main") == tree and args[0] == command:
            hits.append(args)
            return replace(
                result, stdout=result.stdout + "\n[... 100 bytes omitted ...]\n", truncated=True
            )
        return result

    monkeypatch.setattr(h.workspace, "git", git)
    return hits


async def assert_main_unchanged(h: Harness, pre: str) -> None:
    assert await h.main_head() == pre
    assert (await h.main_git("status", "--porcelain")).stdout == ""
    assert (await h.main_git("rev-parse", "-q", "--verify", "MERGE_HEAD")).returncode == 1


@pytest.mark.parametrize("command", ["status", "rev-parse", "diff"])
@bounded
async def test_proposal_refuses_truncated_query(
    h: Harness, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    tree = await h.open(1)
    assert await h.edit(1, *EDIT)
    # Keep both HEADs fixed so even a failed diff leaves the worktree exactly as it started.
    head = await tree.commit_all("Prepare change", h.workspace.agent_author("agent-1"))
    content = await tree.read_text(EDIT[0])
    pre, snapshot = await h.main_head(), h.manager.snapshot
    before = len(h.types())
    hits = truncate_once(h, monkeypatch, command)

    assert await h.finish(1) is False

    assert len(hits) == 1
    [error] = h.payloads(ErrorReport)
    assert (error.source, error.fatal) == ("git", True)
    assert f"git {command} failed: output was truncated" in error.message
    assert h.types()[before:] == ["error"]
    assert h.payloads(ChangeProposed) == []
    assert h.manager.snapshot == snapshot
    await assert_main_unchanged(h, pre)
    assert await tree.head() == head
    assert await tree.read_text(EDIT[0]) == content


@pytest.mark.parametrize("command", ["rev-parse", "status"])
@bounded
async def test_approval_refuses_truncated_worktree_query(
    h: Harness, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    tree = await h.run_agent(1, EDIT)
    head, content = await tree.head(), await tree.read_text(EDIT[0])
    pre, snapshot = await h.main_head(), h.manager.snapshot
    hits = truncate_once(h, monkeypatch, command)

    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))

    assert len(hits) == 1
    [error] = h.payloads(ErrorReport)
    assert f"git {command} failed: output was truncated" in error.message
    assert h.payloads(ChangeResolved) == []
    assert "graph.delta" not in h.types()
    assert h.manager.snapshot == snapshot
    await assert_main_unchanged(h, pre)
    assert await tree.head() == head
    assert await tree.read_text(EDIT[0]) == content
    await h.manager.approve(
        "change-1", None, **observed_target(h.manager, "change-1")
    )  # the failed query left the change reviewable
    assert h.payloads(ChangeResolved)[0].merge == "merged"


@bounded
async def test_truncated_conflict_paths_abort_merge_and_preserve_main(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = await h.run_agent(1, EDIT)
    read = await h.workspace.main.read_text(EDIT[0])
    await h.workspace.main.write_text(
        EDIT[0], read.content.replace("return 1", "return 5"), expected_hash=read.hash
    )
    await h.workspace.main.commit_all("Edit on main", h.workspace.user_author())
    head = await tree.head()
    pre, snapshot = await h.main_head(), h.manager.snapshot
    content = await h.workspace.main.read_text(EDIT[0])
    hits = truncate_once(h, monkeypatch, "diff", tree="main")

    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))

    assert len(hits) == 1
    [error] = h.payloads(ErrorReport)
    assert "git diff failed: output was truncated" in error.message
    assert "main is unchanged" in error.message
    assert h.payloads(ChangeResolved) == []
    assert h.manager.snapshot == snapshot
    await assert_main_unchanged(h, pre)
    assert await h.workspace.main.read_text(EDIT[0]) == content
    assert await tree.head() == head


@bounded
async def test_truncated_worktree_listing_does_not_delete_worktree_or_branch(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = await h.run_agent(1, EDIT)
    head, content = await tree.head(), await tree.read_text(EDIT[0])
    pre = await h.main_head()
    hits = truncate_once(h, monkeypatch, "worktree", tree="main")

    await h.manager.reject("change-1", None, **observed_target(h.manager, "change-1"))

    assert len(hits) == 1
    [error] = h.payloads(ErrorReport)
    assert "git worktree failed: output was truncated" in error.message
    assert tree.root.exists()
    assert await h.has_branch("agent-1")
    assert await tree.head() == head
    assert await tree.read_text(EDIT[0]) == content
    await assert_main_unchanged(h, pre)
