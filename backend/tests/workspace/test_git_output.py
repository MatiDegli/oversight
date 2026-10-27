"""Truncated Git output cannot become structured workspace data."""

from unittest.mock import AsyncMock

import pytest

from oversight.workspace import ProcessResult, Workspace, WorkspaceError

pytestmark = pytest.mark.anyio

MARKER = "\n[... 100 bytes omitted ...]\n"


@pytest.mark.parametrize("command", ["diff", "status", "rev-parse"])
@pytest.mark.parametrize("stream", ["stdout", "stderr"])
async def test_git_ok_rejects_successful_truncated_output(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, command: str, stream: str
) -> None:
    result = ProcessResult(
        returncode=0,
        stdout=MARKER if stream == "stdout" else "valid output",
        stderr=MARKER if stream == "stderr" else "",
        timed_out=False,
        truncated=True,
        duration_ms=1,
    )
    git = AsyncMock(return_value=result)
    monkeypatch.setattr(workspace, "git", git)

    with pytest.raises(WorkspaceError, match=f"^git {command} failed: output was truncated$"):
        await workspace.git_ok(workspace.main, command)

    git.assert_awaited_once()


@pytest.mark.parametrize("operation", ["head", "create_worktree", "cleanup_merged_worktree"])
async def test_truncated_query_prevents_workspace_mutation(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    tree = await workspace.create_worktree("agent-1")
    pre = await workspace.main.head()
    real = workspace.git
    git = AsyncMock(return_value=ProcessResult(0, "", MARKER, False, True, 1))
    with monkeypatch.context() as patch:
        patch.setattr(workspace, "git", git)
        with pytest.raises(WorkspaceError, match="output was truncated"):
            if operation == "head":
                await tree.head()
            elif operation == "create_worktree":
                await workspace.create_worktree("agent-2")
            else:
                await workspace.cleanup_merged_worktree("agent-1", delete_branch=True)

    assert git.await_count == 1  # no worktree add/remove or branch deletion after the query
    assert workspace.worktree("agent-1") is tree
    assert tree.root.exists()
    assert not (workspace.runtime_root / "worktrees" / "agent-2").exists()
    assert await workspace.main.head() == pre
    async with workspace.locks(main=True):
        assert (await real(workspace.main, "status", "--porcelain")).stdout == ""
        assert (
            await real(workspace.main, "rev-parse", "--verify", "--quiet", "refs/heads/agent-1")
        ).returncode == 0
        assert (
            await real(workspace.main, "rev-parse", "--verify", "--quiet", "refs/heads/agent-2")
        ).returncode == 1
