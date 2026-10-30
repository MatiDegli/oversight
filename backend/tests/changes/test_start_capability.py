"""Live session facts, ordered refusal reasons and the code-only activation latch."""

import asyncio
from dataclasses import replace
from itertools import product
from unittest.mock import Mock

import pytest
from changes_helpers import EDIT, Harness
from test_assessment import full_evidence, make_clock, propose, settled, terminals, wait
from test_mode import select
from test_policy_profiles import policy_inputs
from test_rules_transaction import configure_transaction

from oversight.changes import ChangeManager, WrongState, _mode, _rules, _transaction
from oversight.changes._support import RulesFailure
from oversight.events import parse_needs_you
from oversight.settings import Settings
from oversight_contracts.events import ChangeResolved, ErrorReport
from oversight_contracts.policy import MergeModeState

pytestmark = pytest.mark.anyio
LATCH_REASON = "Rules mode is closed until Revert, protected paths and follow-main land"
C_REASON = "Network isolation C is unavailable for this session"


def configured(h: Harness, settings: Settings) -> None:
    h._manager = ChangeManager(
        h.workspace,
        h.publish,
        main_graph=h.main_graph,
        main_snapshot=h.manager.snapshot,
        settings=settings,
    )


def notices(h):
    return [r for r in h.payloads(ErrorReport) if r.source == "server" and r.change_id is None]


async def test_ordered_truth_table_refuses_without_any_event(h, monkeypatch):
    # Every combination still refuses with the real, closed latch, even after confirmed C.
    settings = Settings(agent_model="fake", review_model="fake-b", cross_review=True)
    for born, invalid, suspended, suspect, review_on, same in product((False, True), repeat=6):
        monkeypatch.setattr(h.workspace._isolation, "_checked", True)
        monkeypatch.setattr(h.workspace._isolation, "_born", born)
        monkeypatch.setattr(h.workspace._isolation, "_invalidated", invalid)
        h.workspace.integrity.rules_suspended = suspended
        h.workspace.integrity.main_suspect = suspect
        configured(
            h, replace(settings, cross_review=review_on, review_model="fake" if same else "fake-b")
        )
        expected = (
            C_REASON
            if not born or invalid
            else "Rules authority is suspended until restart"
            if suspended
            else "Main's integrity is uncertain until restart"
            if suspect
            else "Cross-review is off"
            if not review_on
            else "The reviewer model must differ from each author model"
            if same
            else LATCH_REASON
        )
        before = list(h.log)
        with pytest.raises(WrongState) as refused:
            await select(h, "merge_when_safe", 0)
        assert str(refused.value) == expected
        assert h.manager._ctx.mode.capability() == _mode.StartCapability(False, expected)
        assert h.manager.merge_mode() == MergeModeState()
        assert h.log == before
    h.workspace.integrity.main_suspect = False


@pytest.mark.parametrize("slot", ["A", "B", "C", "D", "E", "F"])
async def test_reviewer_differs_from_every_offered_author(h, slot):
    await h.workspace.check_isolation()
    values = {"cross_review": True, "review_model": "muse-spark-open", "max_agents": 6}
    values["agent_model" + ("" if slot == "A" else f"_{slot.lower()}")] = "muse-spark-open"
    configured(h, Settings(**values))
    assert h.manager._ctx.mode.capability().reason == (
        "The reviewer model must differ from each author model"
    )


async def test_reads_never_probe_construct_clients_or_consult_environment(h, monkeypatch):
    await h.workspace.check_isolation()
    configured(h, Settings(cross_review=True))
    forbidden = Mock(side_effect=AssertionError("Capability read performed work"))
    for obj, name in (
        (h.workspace, "check_isolation"),
        (h.workspace._isolation, "check"),
        (h.workspace._isolation, "verify_effect"),
        (h.workspace, "git"),
    ):
        monkeypatch.setattr(obj, name, forbidden)
    monkeypatch.setattr("oversight.workspace.capability.check_effect", forbidden)
    monkeypatch.setattr("oversight.review_config.configured_reviewer", forbidden)
    monkeypatch.setattr("oversight.review_config.OpenAIClient", forbidden)
    monkeypatch.setattr(Settings, "load", forbidden)
    monkeypatch.setenv("RULES_ACTIVATION", "true")
    monkeypatch.setenv("OVERSIGHT_RULES_ACTIVATION", "true")
    assert _mode.RULES_ACTIVATION is False
    for _ in range(3):
        assert h.manager._ctx.mode.capability() == _mode.StartCapability(False, LATCH_REASON)
    h.workspace._isolation.invalidate("Lost after startup")
    assert h.manager._ctx.mode.capability() == _mode.StartCapability(False, C_REASON)
    forbidden.assert_not_called()


async def arm_production_port(h, monkeypatch):
    # Only tests open the code latch to exercise authority loss beyond it.
    make_clock(h, monkeypatch)
    await h.workspace.check_isolation()
    settings = Settings(cross_review=True)
    monkeypatch.setattr(_mode, "RULES_ACTIVATION", True)
    h.manager._ctx.mode = _mode.ModeController(
        availability=lambda: _mode.start_capability(h.workspace, settings)
    )
    await select(h, "merge_when_safe", 0)
    full_evidence(h, policy_inputs())
    await propose(h)


async def test_invalidation_refuses_capture_and_every_wait_needs_you_once(h, monkeypatch):
    await arm_production_port(h, monkeypatch)
    await h.open(2)
    assert await h.edit(2, *EDIT)
    assert await h.manager.proposed("agent-2", "Ready")
    h.workspace._isolation.invalidate("Lost after proposal")
    h.manager.run_ended("agent-1", None)
    h.manager.run_ended("agent-2", None)
    await settled(h)
    with pytest.raises(RulesFailure) as refused:
        _rules.capture(h.manager._ctx, wait(h), wait(h).latest)
    assert refused.value.code == "isolation_unavailable"
    # Concurrent waits and repeated barriers share the same session notice.
    for _ in range(3):
        failure = _rules.availability(h.manager._ctx)
        assert failure is not None and failure[0] == "isolation_unavailable"
    await asyncio.gather(
        *[t for t in h.manager._ctx.state.tasks if t is not asyncio.current_task()]
    )
    assert len(terminals(h)) == 2
    for report in terminals(h):
        note = parse_needs_you(report.message)
        assert note is not None and note.code == "isolation_unavailable"
    assert len(notices(h)) == 1 and not notices(h)[0].fatal
    assert C_REASON in notices(h)[0].message
    assert h.manager.merge_mode() == MergeModeState(mode="merge_when_safe", revision=1)
    assert not h.payloads(ChangeResolved)


@pytest.mark.parametrize("loss", ["isolation", "suspended"])
async def test_loss_refuses_transaction_barrier(h, monkeypatch, loss):
    await arm_production_port(h, monkeypatch)
    configure_transaction(h, monkeypatch)
    barrier = _transaction.Transaction.barriers
    reached = []
    decisions = []
    decide = h.manager._decide

    def tracked_decision(record, work):
        task = decide(record, work)
        decisions.append(task)
        return task

    async def lose_at_barrier(tx):
        reached.append(True)
        if loss == "isolation":
            h.workspace._isolation.invalidate("Lost after suite")
        else:
            h.workspace.integrity.rules_suspended = True
        await barrier(tx)

    monkeypatch.setattr(h.manager, "_decide", tracked_decision)
    monkeypatch.setattr(_transaction.Transaction, "barriers", lose_at_barrier)
    h.manager.run_ended("agent-1", None)
    await settled(h)
    await asyncio.gather(*decisions)
    assert reached == [True]
    assert not h.payloads(ChangeResolved)
    assert len(notices(h)) == 1
    assert h.manager.merge_mode().mode == "merge_when_safe"
    assert len(terminals(h)) == 1
    note = parse_needs_you(terminals(h)[0].message)
    assert note is not None
    assert note.code == ("isolation_unavailable" if loss == "isolation" else "policy_invalid")
