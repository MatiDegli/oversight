"""The proposal: a commit on the base, the tests on it, the cone on the change's version."""

import pytest
from changes_helpers import TWICE, VALUE, Harness, bounded

from oversight_contracts.events import ChangeProposed, ChangeUpdated

pytestmark = pytest.mark.anyio


@bounded
async def test_proposal_commits_on_the_base_and_runs_the_tests(h: Harness) -> None:
    await h.open(1)
    assert await h.edit(1, "shop/mod.py", "return 1", "return 3")
    assert await h.finish(1, summary="Made value 3.")

    [proposal] = h.payloads(ChangeProposed)
    assert (proposal.revision, proposal.summary, proposal.task) == (1, "Made value 3.", "Do it.")
    assert proposal.base_commit == h.base_commit != proposal.head_commit
    parent = await h.main_git("rev-parse", f"{proposal.head_commit}^")
    assert parent.stdout.strip() == h.base_commit
    assert (proposal.tests.outcome, proposal.tests.passed) == ("passed", 1)
    assert [(f.path, f.status, f.lines_added, f.lines_removed) for f in proposal.files] == [
        ("shop/mod.py", "modified", 1, 1)
    ]
    assert (proposal.lines_added, proposal.lines_removed) == (1, 1)
    assert proposal.impact.seeds == [VALUE]
    assert [(e.node_id, e.distance) for e in proposal.impact.entries] == [(TWICE, 1)]
    # Already sent in full by the edit, so not republished.
    assert len(h.payloads(ChangeUpdated)) == 1
    assert all(c.timeout_s == 29.0 for c in h.workspace.calls if c.tree == "agent-1")


@bounded
async def test_breaking_edit_fails_the_tests(h: Harness) -> None:
    await h.open(1)
    assert await h.edit(1, "shop/mod.py", "return 1", "return 0")
    assert await h.finish(1)

    [proposal] = h.payloads(ChangeProposed)
    assert (proposal.tests.outcome, proposal.tests.failed) == ("failed", 1)
    assert proposal.tests.failed_tests == ["tests/test_mod.py::test_value"]


@bounded
async def test_empty_run_is_proposed_at_its_base(h: Harness) -> None:
    await h.open(1)
    assert await h.finish(1)

    [proposal] = h.payloads(ChangeProposed)
    assert proposal.head_commit == proposal.base_commit == h.base_commit
    assert (proposal.files, proposal.impact.seeds) == ([], [])


@bounded
async def test_halted_run_proposes_too(h: Harness) -> None:
    await h.open(1)
    assert await h.edit(1, "shop/mod.py", "return 1", "return 3")
    assert await h.finish(1, summary=None)

    [proposal] = h.payloads(ChangeProposed)
    assert proposal.summary is None
    await h.manager.approve("change-1", None)
    assert "change.resolved" in h.types()
