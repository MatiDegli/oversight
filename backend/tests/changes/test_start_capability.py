"""Live session facts, ordered refusal reasons and the code-only activation latch."""

import asyncio
from dataclasses import replace
from itertools import product
from unittest.mock import AsyncMock, Mock

import pytest
from changes_helpers import EDIT, Harness
from test_assessment import full_evidence, make_clock, propose, rec, settled, terminals, wait
from test_mode import select
from test_policy_profiles import policy_inputs
from test_rules_transaction import configure_transaction

from oversight.changes import ChangeManager, WrongState, _mode, _rules, _transaction
from oversight.changes._support import RulesFailure
from oversight.events import parse_needs_you
from oversight.settings import Settings
from oversight.workspace import IsolationUnavailable, SuiteRun, SuiteUnavailable, suite_guard
from oversight_contracts.events import ChangeResolved, ErrorReport, SuiteResult
from oversight_contracts.policy import MergeModeState

pytestmark = pytest.mark.anyio
LATCH_REASON = "Rules authority is unavailable in this build"
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
    monkeypatch.setattr(_mode, "RULES_ACTIVATION", False)
    # Every combination refuses with an explicitly closed build, even after confirmed C.
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


@pytest.mark.parametrize("activation", [False, True])
async def test_reads_never_probe_construct_clients_or_consult_environment(
    h, monkeypatch, activation
):
    monkeypatch.setattr(_mode, "RULES_ACTIVATION", activation)
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
    monkeypatch.setenv("RULES_ACTIVATION", str(not activation).lower())
    monkeypatch.setenv("OVERSIGHT_RULES_ACTIVATION", str(not activation).lower())
    assert _mode.RULES_ACTIVATION is activation
    for _ in range(3):
        assert h.manager._ctx.mode.capability() == _mode.StartCapability(
            activation, "Rules prerequisites confirmed" if activation else LATCH_REASON
        )
    h.workspace._isolation.invalidate("Lost after startup")
    assert h.manager._ctx.mode.capability() == _mode.StartCapability(False, C_REASON)
    forbidden.assert_not_called()


async def arm_production_port(h, monkeypatch):
    # Exercise authority loss through the production capability port.
    clock = make_clock(h, monkeypatch)
    await h.workspace.check_isolation()
    settings = Settings(cross_review=True)
    monkeypatch.setattr(_mode, "RULES_ACTIVATION", True)
    h.manager._ctx.mode = _mode.ModeController(
        availability=lambda: _mode.start_capability(h.workspace, settings)
    )
    await select(h, "merge_when_safe", 0)
    full_evidence(h, policy_inputs())
    await propose(h)
    return clock


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


async def test_combined_suite_loss_notifies_once_even_when_another_wait_expires(h, monkeypatch):
    clock = await arm_production_port(h, monkeypatch)
    await h.open(2)
    assert await h.edit(2, *EDIT)
    assert await h.manager.proposed("agent-2", "Ready")
    policies, _, barriers, _ = configure_transaction(h, monkeypatch)
    failed = SuiteRun(
        SuiteResult(outcome="error", passed=0, failed=0, skipped=0, duration_ms=0, failed_tests=[]),
        "Scripted isolation failure",
    )
    suite = AsyncMock(side_effect=SuiteUnavailable(failed, IsolationUnavailable("Lost in suite")))
    monkeypatch.setattr(suite_guard, "run_suite", suite)
    decisions = []
    decide = h.manager._decide

    def tracked_decision(record, work):
        task = decide(record, work)
        decisions.append(task)
        return task

    monkeypatch.setattr(h.manager, "_decide", tracked_decision)
    h.manager.run_ended("agent-1", None)
    await settled(h)
    await asyncio.gather(*decisions)
    suite.assert_awaited_once()
    assert policies == ["required"] and not barriers
    assert not h.workspace.isolation.available
    assert rec(h).state == "ready"
    note = parse_needs_you(terminals(h)[0].message)
    assert note is not None and note.code == "isolation_unavailable"
    await asyncio.gather(*h.manager._ctx.state.tasks)
    assert len(notices(h)) == 1 and not notices(h)[0].fatal
    assert C_REASON in notices(h)[0].message

    # In the same revoked session, an evidence wait ends by expiry rather than a barrier.
    h.manager._ctx.ports.rules_evidence = None
    h.manager.run_ended("agent-2", None)
    await settled(h)
    second = h.manager._ctx.assessment.waits["change-2"]
    assert second.active and second.timer is not None
    clock.advance(121)
    await second.timer
    await asyncio.gather(*h.manager._ctx.state.tasks)
    assert len(terminals(h)) == 2 and len(notices(h)) == 1
    assert h.manager.merge_mode() == MergeModeState(mode="merge_when_safe", revision=1)
    assert not h.payloads(ChangeResolved)


@pytest.mark.parametrize("loss", ["isolation", "metadata"])
async def test_expiry_after_loss_emits_one_notice_without_a_barrier(h, monkeypatch, loss):
    clock = await arm_production_port(h, monkeypatch)
    h.manager._ctx.ports.rules_evidence = None
    h.manager.run_ended("agent-1", None)
    await settled(h)
    assert not terminals(h) and not notices(h)
    if loss == "isolation":
        h.workspace._isolation.invalidate("Lost while waiting for evidence")
        reason = C_REASON
    else:
        h.workspace.integrity.rules_suspended = True
        reason = "Rules authority is suspended until restart"
    pending = wait(h)
    assert pending.timer is not None
    clock.advance(121)
    await pending.timer
    await asyncio.gather(*h.manager._ctx.state.tasks)
    assert len(terminals(h)) == 1
    assert "Required rules evidence was not confirmed" in terminals(h)[0].message
    assert len(notices(h)) == 1 and not notices(h)[0].fatal
    assert reason in notices(h)[0].message
    assert h.manager.merge_mode() == MergeModeState(mode="merge_when_safe", revision=1)


async def test_unknown_reviewer_is_a_capability_refusal_and_terminal_reads_are_safe(h, monkeypatch):
    await arm_production_port(h, monkeypatch)
    settings = Settings(cross_review=True, review_model="unknown-reviewer")
    h.manager._ctx.mode._availability = lambda: _mode.start_capability(h.workspace, settings)
    reason = "The reviewer model is not configured"
    assert h.manager._ctx.mode.capability() == _mode.StartCapability(False, reason)
    # Both terminal entry points read availability; neither may raise ValueError.
    h.manager.run_ended("agent-1", None)
    await settled(h)
    clock_wait = wait(h)
    await h.manager._ctx.assessment.terminal(clock_wait, "Evidence unavailable")
    reservation = _rules.RulesReservation(clock_wait, Mock(), rec(h).decision)
    await _rules.terminal(h.manager._ctx, reservation, "policy_invalid", reason)
    await asyncio.gather(*h.manager._ctx.state.tasks)
    assert len(notices(h)) == 1 and reason in notices(h)[0].message
    assert len(terminals(h)) == 1
    assert not h.payloads(ChangeResolved)


async def test_unknown_reviewer_refuses_enable_without_event(h):
    await h.workspace.check_isolation()
    configured(h, Settings(cross_review=True, review_model="unknown-reviewer"))
    before = list(h.log)
    with pytest.raises(WrongState, match="The reviewer model is not configured"):
        await select(h, "merge_when_safe", 0)
    assert h.log == before and h.manager.merge_mode() == MergeModeState()
