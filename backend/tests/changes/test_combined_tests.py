"""R5: when main moved since the change's base, Approve tests the merge before committing it."""

import pytest
from changes_helpers import Harness, bounded

from oversight.workspace import SuiteRun, Tree, WorkspaceError
from oversight_contracts.events import ChangeResolved, ErrorReport

pytestmark = pytest.mark.anyio

EDIT = ("shop/mod.py", "return 1", "return 3")
STRICT = "from shop.mod import twice\n\n\ndef test_twice() -> None:\n    assert twice() == 2\n"
LOOSE = "from shop.mod import twice\n\n\ndef test_twice() -> None:\n    assert twice() > 0\n"


async def add_on_main(h: Harness, path: str, content: str) -> str:
    """Commit a new file on main, as an earlier approval or a save on main would."""
    workspace = h.workspace
    async with workspace.locks(main=True):
        await workspace.main.write_text(path, content, expected_hash=None)
        await workspace.git_ok(workspace.main, "add", "-A")
        await workspace.git_ok(workspace.main, "commit", "-qm", f"Add {path}")
    return await h.main_head()


async def assert_main_clean(h: Harness) -> None:
    assert (await h.main_git("status", "--porcelain")).stdout == ""
    assert (await h.main_git("rev-parse", "-q", "--verify", "MERGE_HEAD")).returncode == 1


@bounded
async def test_combined_tests_after_main_moved(h: Harness) -> None:
    # Each side passes alone; together twice() is 6, and main's new test wants 2.
    await h.run_agent(1, EDIT)
    pre = await add_on_main(h, "tests/test_twice.py", STRICT)

    await h.manager.approve("change-1", "Ship it.")

    assert await h.main_head() == pre
    await assert_main_clean(h)
    [resolved] = h.payloads(ChangeResolved)
    assert (resolved.decision, resolved.merge, resolved.comment) == (
        "approved",
        "conflict",
        "Ship it.",
    )
    conflict = resolved.conflict
    assert conflict is not None and conflict.tests is not None
    assert (conflict.kind, conflict.paths, conflict.tests.outcome) == ("tests", [], "failed")
    assert conflict.tests.failed_tests == ["tests/test_twice.py::test_twice"]
    assert "graph.delta" not in h.types()
    await h.manager.reject("change-1", None)
    assert [r.decision for r in h.payloads(ChangeResolved)] == ["approved", "rejected"]


@bounded
async def test_combined_tests_passing_merge(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    await h.run_agent(1, EDIT)
    pre = await add_on_main(h, "tests/test_twice.py", LOOSE)
    ran: list[str] = []
    real = h.workspace.run_tests

    async def recording(tree: Tree, *, timeout_s: float) -> SuiteRun:
        ran.append(tree.agent_id or "main")
        return await real(tree, timeout_s=timeout_s)

    monkeypatch.setattr(h.workspace, "run_tests", recording)

    await h.manager.approve("change-1", None)

    assert ran == ["main"]
    [resolved] = h.payloads(ChangeResolved)
    assert (resolved.merge, resolved.merge_commit) == ("merged", await h.main_head())
    parents = (await h.main_git("rev-list", "--parents", "-n", "1", "HEAD")).stdout.split()[1:]
    assert parents[0] == pre
    await assert_main_clean(h)
    assert not h.worktree_dir("agent-1").exists()


@bounded
async def test_no_combined_tests_when_main_did_not_move(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    await h.run_agent(1, EDIT)
    ran: list[str] = []

    async def refuse(tree: Tree, *, timeout_s: float) -> SuiteRun:
        ran.append(tree.agent_id or "main")
        raise AssertionError("no tests on main: its HEAD is the change's base")

    monkeypatch.setattr(h.workspace, "run_tests", refuse)
    await h.manager.approve("change-1", None)
    assert ran == []
    assert h.payloads(ChangeResolved)[0].merge == "merged"


@bounded
async def test_combined_tests_that_cannot_run_leave_main_unchanged(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    await h.run_agent(1, EDIT)
    pre = await add_on_main(h, "tests/test_twice.py", LOOSE)

    async def broken(tree: Tree, *, timeout_s: float) -> SuiteRun:
        raise WorkspaceError("the demo venv is missing")

    monkeypatch.setattr(h.workspace, "run_tests", broken)
    await h.manager.approve("change-1", None)

    assert await h.main_head() == pre
    await assert_main_clean(h)
    assert h.payloads(ChangeResolved) == []
    assert any("the demo venv is missing" in e.message for e in h.payloads(ErrorReport))
    monkeypatch.undo()
    await h.manager.approve("change-1", None)  # still `ready`
    assert h.payloads(ChangeResolved)[0].merge == "merged"
