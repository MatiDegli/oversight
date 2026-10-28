"""Observed decision targets and terminal publication failures around actual Git effects."""

import pytest
from changes_helpers import EDIT, Harness
from decision_helpers import observed_target

from oversight.changes import WrongState
from oversight.events import EventRejected
from oversight_contracts.events import ChangeResolved, ErrorReport

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("verb", ["approve", "reject", "request_changes"])
async def test_stale_guard_refuses_before_reservation_or_effect(h: Harness, verb: str) -> None:
    await h.run_agent(1, EDIT)
    before = len(h.workspace.calls), h.store.last_seq
    guard = observed_target(h.manager, "change-1")
    with pytest.raises(WrongState):
        getattr(h.manager, verb)("change-1", "Review", **{**guard, "head_commit": "stale"})
    assert (len(h.workspace.calls), h.store.last_seq) == before
    assert h.manager._agents["agent-1"].state == "ready"


async def test_never_proposed_reject_uses_recorded_base_after_main_moves(h: Harness) -> None:
    await h.open(1)
    h.manager.run_ended("agent-1", None)
    base = h.manager.agent_base("agent-1")
    assert base is not None
    await h.run_agent(2, EDIT)
    await h.manager.approve("change-2", None, **observed_target(h.manager, "change-2"))
    assert await h.main_head() != base
    with pytest.raises(WrongState):
        h.manager.reject("change-1", None, revision=0, head_commit=await h.main_head())
    await h.manager.reject("change-1", None, revision=0, head_commit=base)
    resolution = h.payloads(ChangeResolved)[-1]
    assert (resolution.revision, resolution.head_commit, resolution.decided_by) == (
        0,
        base,
        "person",
    )


@pytest.mark.parametrize("verb", ["approve", "request_changes", "reject"])
async def test_terminal_rejection_is_attempted_once_and_never_closes(
    h: Harness, monkeypatch: pytest.MonkeyPatch, verb: str
) -> None:
    await h.run_agent(1, EDIT)
    main = await h.main_head()
    attempts = []
    original = h.publish

    async def reject_resolution(payload):
        if isinstance(payload, ChangeResolved):
            attempts.append(payload)
            raise EventRejected("semantic refusal")
        return await original(payload)

    monkeypatch.setattr(h.manager._ctx, "publish", reject_resolution)
    result = await getattr(h.manager, verb)(
        "change-1", "Review", **observed_target(h.manager, "change-1")
    )
    assert result is None
    rec = h.manager._agents["agent-1"]
    assert rec.state == "delivery_failed" and rec.delivery_rejected
    assert not rec.cleaned and "resolved" not in rec.sent
    assert h.worktree_dir("agent-1").is_dir()
    await h.manager.aclose()
    assert len(attempts) == 1
    assert rec.requests == 0
    assert await h.main_head() != main if verb == "approve" else await h.main_head() == main
    assert rec.evidence is not None and attempts[0].head_commit == rec.evidence.head_commit
    assert h.payloads(ErrorReport)
    with pytest.raises(WrongState):
        h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))


async def prepare_conflict(h: Harness) -> None:
    await h.run_agent(1, EDIT)
    read = await h.workspace.main.read_text("shop/mod.py")
    await h.manager.save_main(
        "shop/mod.py", read.content.replace("return 1", "return 5"), read.hash
    )


async def test_terminal_rejection_after_merge_abort_keeps_conflict(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    await prepare_conflict(h)
    before = await h.main_head()
    original = h.publish
    attempts = []

    async def reject_resolution(payload):
        if isinstance(payload, ChangeResolved):
            attempts.append(payload)
            raise EventRejected("conflict delivery refused")
        return await original(payload)

    monkeypatch.setattr(h.manager._ctx, "publish", reject_resolution)
    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    rec = h.manager._agents["agent-1"]
    assert rec.state == "delivery_failed" and rec.conflict is not None
    assert rec.resolution == attempts[0] and attempts[0].merge == "conflict"
    assert await h.main_head() == before
    assert not any("approve again" in e.message for e in h.payloads(ErrorReport))
    await h.manager.aclose()
    assert len(attempts) == 1


async def test_rejected_revision_after_base_move_retains_original_evidence(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    await prepare_conflict(h)
    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    rec = h.manager._agents["agent-1"]
    original_base = rec.base_commit
    guard = observed_target(h.manager, "change-1")
    original = h.publish
    attempts = []

    async def reject_resolution(payload):
        if isinstance(payload, ChangeResolved):
            attempts.append(payload)
            raise EventRejected("revision delivery refused")
        return await original(payload)

    monkeypatch.setattr(h.manager._ctx, "publish", reject_resolution)
    assert await h.manager.request_changes("change-1", "Redo", **guard) is None
    assert rec.base_commit != original_base
    assert rec.evidence is not None and rec.evidence.base_commit == original_base
    assert rec.state == "delivery_failed" and rec.requests == 0
    assert attempts[0].head_commit == guard["head_commit"]
    await h.manager.aclose()
    assert len(attempts) == 1
