"""The proposal: a commit on the base, the tests on it, the cone on the change's version."""

import asyncio
from threading import current_thread, main_thread

import pytest
from changes_helpers import TWICE, VALUE, Harness, bounded, queued_callbacks_ran
from decision_helpers import observed_target

from oversight.changes import _diff
from oversight_contracts.events import AgentStatus, ChangeProposed, ChangeResolved, ChangeUpdated

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
async def test_edits_and_proposal_reuse_counts_and_preserve_totals(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    await h.open(1)
    with _diff._cache_lock:
        _diff._counts.clear()
    calls = []
    direct = _diff._count_lines

    def observed(original: str | None, modified: str | None) -> tuple[int, int]:
        assert current_thread() is not main_thread()
        calls.append((original, modified))
        return direct(original, modified)

    monkeypatch.setattr(_diff, "_count_lines", observed)
    assert await h.edit(1, "shop/mod.py", "return 1", "return 3")
    assert await h.edit(1, "tests/test_mod.py", "value() > 0", "value() >= 0")
    assert await h.edit(1, "tests/test_mod.py", "value() >= 0", "value() >= 1")
    assert len(calls) == 3  # unchanged files and each edit's totals reuse the same pair
    assert await h.finish(1)
    assert len(calls) == 3  # proposal statistics reuse the live counts too

    updates = h.payloads(ChangeUpdated)
    assert [(e.files_changed, e.lines_added, e.lines_removed) for e in updates] == [
        (1, 1, 1),
        (2, 2, 2),
        (2, 2, 2),
    ]
    [proposal] = h.payloads(ChangeProposed)
    assert (proposal.lines_added, proposal.lines_removed) == (2, 2)
    assert [(f.path, f.lines_added, f.lines_removed) for f in proposal.files] == [
        ("shop/mod.py", 1, 1),
        ("tests/test_mod.py", 1, 1),
    ]


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
    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    assert "change.resolved" in h.types()


@bounded
async def test_cancel_during_the_proposal_publish(h: Harness) -> None:
    """R7: the store took revision 1, then the run was cancelled before the publish returned; the
    record still matches the store, so the change is ready and approves as revision 1."""
    await h.open(1)
    assert await h.edit(1, "shop/mod.py", "return 1", "return 3")
    h.hold = ChangeProposed
    run = asyncio.create_task(h.manager.proposed("agent-1", "Done."))
    await h.held.wait()
    run.cancel()
    await queued_callbacks_ran()
    assert not run.done()  # it waits for the publish's end
    h.release.set()
    with pytest.raises(asyncio.CancelledError):
        await run
    h.manager.run_ended("agent-1", None, steps=4)

    [proposal] = h.payloads(ChangeProposed)
    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    [resolved] = h.payloads(ChangeResolved)
    assert (resolved.revision, resolved.merge) == (proposal.revision, "merged") == (1, "merged")
    assert h.payloads(AgentStatus)[-1].step == 4
