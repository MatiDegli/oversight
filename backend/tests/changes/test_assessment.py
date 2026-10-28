"""Scheduler authority and deadlines, using committed receipts and manual monotonic time."""

import asyncio
from dataclasses import replace
from unittest.mock import Mock

import pytest
from changes_helpers import EDIT, NOW, Harness, queued_callbacks_ran
from test_mode import available, select
from test_policy_profiles import PROFILE, policy_inputs

from oversight.changes import ChangeManager, RulesEvidence, _assessment
from oversight.changes._facts import IntegrityStatus
from oversight.changes._policy import SpendSnapshot, assess
from oversight.changes._proposal import record_proposal
from oversight.changes._state import APPROVABLE
from oversight.events import NeedsYouWait, format_needs_you, parse_needs_you
from oversight_contracts.events import ChangeProposed, ChangeResolved, ErrorReport, SuiteExecution
from oversight_contracts.policy import POLICY_RULES, ChangeAssessment, EvidenceRef
from oversight_contracts.review import CrossReview, ReviewFinding

pytestmark = pytest.mark.anyio


@pytest.fixture
def ready():
    return policy_inputs()


class ManualClock:
    def __init__(self):
        self.now = 0.0
        self.registrations = []
        self.calls = Mock(wraps=assess)

    def __call__(self):
        return self.now

    async def sleep_until(self, deadline):
        future = asyncio.get_running_loop().create_future()
        self.registrations.append((deadline, future))
        await future

    def advance(self, now):
        self.now = now
        for deadline, future in self.registrations:
            if deadline <= now and not future.done():
                future.set_result(None)


def make_clock(h: Harness, monkeypatch: pytest.MonkeyPatch):
    clock = ManualClock()
    h._manager = ChangeManager(
        h.workspace,
        h.publish,
        main_graph=h.main_graph,
        main_snapshot=h.manager.snapshot,
        clock=lambda: NOW,
        monotonic=clock,
        sleep_until=clock.sleep_until,
    )
    monkeypatch.setattr(_assessment, "assess", clock.calls)
    return clock


@pytest.fixture
def clock(h: Harness, monkeypatch: pytest.MonkeyPatch):
    return make_clock(h, monkeypatch)


async def settled(h):
    for _ in range(3):
        await queued_callbacks_ran()
    tasks = [w.task for w in h.manager._ctx.assessment.waits.values() if w.task is not None]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    timers = [
        w.timer
        for w in h.manager._ctx.assessment.waits.values()
        if w.timer is not None and w.timer.cancelling()
    ]
    await asyncio.gather(*timers, return_exceptions=True)


def rec(h):
    return h.manager._ctx.state.changes["change-1"]


def head(h):
    value = rec(h).head_commit
    assert value is not None
    return value


def captured_mode(h):
    value = rec(h).proposal_mode
    assert value is not None
    return value


def latest(h):
    value = wait(h).latest
    assert value is not None
    return value


def timer(h):
    value = wait(h).timer
    assert value is not None
    return value


def wait(h):
    return h.manager._ctx.assessment.waits["change-1"]


def terminals(h):
    return [p for p in h.payloads(ErrorReport) if parse_needs_you(p.message)]


def full_evidence(h, ready, mutate=lambda inputs: inputs):
    def supply(inputs):
        identity = {
            name: getattr(inputs, name)
            for name in (
                "change_id",
                "agent_id",
                "revision",
                "base_commit",
                "head_commit",
                "main_commit",
                "candidate_tree",
                "mode",
                "mode_revision",
            )
        }
        review = ready.review.model_copy(
            update={k: v for k, v in identity.items() if k in CrossReview.model_fields}
        )
        reference = EvidenceRef(event_seq=wait(h).proposal_seq)
        complete = replace(
            ready,
            **identity,
            review=review,
            suite=None,
            evidence={rule: (reference,) for rule in POLICY_RULES[PROFILE]},
        )
        return RulesEvidence(mutate(complete))

    h.manager._ctx.ports.rules_evidence = supply


async def propose(h):
    await h.open(1)
    assert await h.edit(1, *EDIT)
    assert await h.manager.proposed("agent-1", "Ready")


async def test_ask_me_and_later_activation_start_no_authority(h: Harness, clock):
    await h.run_agent(1, EDIT)
    assert captured_mode(h).mode == "ask_me"
    review = policy_inputs().review
    assert review is not None
    for payload in (
        review.model_copy(update={"base_commit": rec(h).base_commit, "head_commit": head(h)}),
        h.payloads(SuiteExecution)[0].model_copy(update={"phase": "combined"}),
    ):
        receipt = await h.publish(payload)
        h.manager.rules_received(receipt)
    for event in h.log:
        h.manager.rules_received(event)
    h.manager._ctx.assessment.wake(rec(h))
    await settled(h)
    assert clock.calls.call_count == 0 and clock.registrations == []
    available(h)
    await select(h, "merge_when_safe", 0)
    h.manager._ctx.assessment.wake(rec(h))
    await settled(h)
    assert clock.calls.call_count == 0 and clock.registrations == []


async def test_first_ready_deadline_is_fixed_and_normalized(h: Harness, clock, ready):
    available(h)
    await select(h, "merge_when_safe", 0)
    await propose(h)
    assert clock.calls.call_count == 0 and clock.registrations == []
    clock.advance(10)
    h.manager.run_ended("agent-1", None)
    await settled(h)
    assert wait(h).deadline == 130 and len(clock.registrations) == 1
    assert clock.calls.call_count == 1
    assert next(r for r in latest(h).rules if r.rule == "test_integrity").result == "pass"
    clock.advance(30)
    for _ in range(2):
        # A new committed review wakes; delivering the same receipt twice does not.
        review = ready.review.model_copy(
            update=dict(
                change_id=rec(h).change_id,
                revision=rec(h).revision,
                base_commit=rec(h).base_commit,
                head_commit=head(h),
            )
        )
        receipt = await h.publish(review)
        h.manager.rules_received(receipt)
        await settled(h)
        calls = clock.calls.call_count
        h.manager.rules_received(receipt)
        await settled(h)
        assert clock.calls.call_count == calls
    assert wait(h).deadline == 130 and len(clock.registrations) == 1
    clock.advance(129.999)
    await queued_callbacks_ran()
    assert terminals(h) == []
    clock.advance(130)
    await timer(h)
    [terminal] = terminals(h)
    assert terminal.message == format_needs_you(
        1,
        head(h),
        "policy_invalid",
        "Required rules evidence was not confirmed within 120 seconds.",
    )
    assert wait(h).terminal_seq is not None
    h.manager._ctx.assessment.wake(rec(h))
    clock.advance(300)
    await settled(h)
    assert len(terminals(h)) == 1


MISSING = [
    ("small_cone", "cone"),
    ("small_change", "files"),
    ("known_spend", "spend"),
    ("existing_tests_untouched", "modified_existing_test_paths"),
    ("cross_review", "review"),
    ("test_integrity", "integrity"),
    ("added_tests_fail_before", "added_tests_inventory"),
]


@pytest.mark.parametrize("rule,field", MISSING)
async def test_each_missing_prerequisite_waits_without_admission(
    h: Harness, clock, ready, rule, field
):
    available(h)
    await select(h, "merge_when_safe", 0)
    full_evidence(h, ready, lambda inputs: replace(inputs, **{field: None}))
    admission = Mock(side_effect=AssertionError("reserved with missing evidence"))
    h.manager._ctx.ports.rules_admission = admission
    await h.run_agent(1, EDIT)
    calls = len(h.workspace.calls)
    await settled(h)
    [assessment] = h.payloads(ChangeAssessment)
    assert {r.rule for r in assessment.rules if r.result == "unknown"} == {rule, "combined_tests"}
    assert clock.calls.call_count == 1 and len(clock.registrations) == 1
    assert admission.call_count == 0 and len(h.workspace.calls) == calls
    assert rec(h).state == "ready" and rec(h).taken == 0 and not rec(h).rules_consumed
    assert not any(e.phase == "combined" for e in h.payloads(SuiteExecution))
    assert terminals(h) == []


@pytest.mark.parametrize(
    "veto",
    [
        "small_cone",
        "small_change",
        "known_spend",
        "existing_tests_untouched",
        "cross_review",
        "test_integrity",
    ],
)
async def test_proven_veto_exits_immediately(h: Harness, clock, ready, veto):
    def mutation(inputs):
        changes = {
            "small_cone": dict(cone=inputs.cone.model_copy(update={"omitted_by_depth": 1})),
            "small_change": dict(lines_added=61),
            "known_spend": dict(
                spend=SpendSnapshot(spent_usd=0.5, budget_usd=1, unknown_costs=0, ledger_ok=True)
            ),
            "existing_tests_untouched": dict(modified_existing_test_paths=("tests/test_mod.py",)),
            "cross_review": dict(
                review=inputs.review.model_copy(
                    update={"findings": [ReviewFinding(severity="BLOCKING", text="Veto")]}
                )
            ),
            "test_integrity": dict(
                integrity_status=IntegrityStatus(complete=False, prohibited_paths=("conftest.py",))
            ),
        }
        return replace(inputs, **changes[veto])

    available(h)
    await select(h, "merge_when_safe", 0)
    full_evidence(h, ready, mutation)
    await h.run_agent(1, EDIT)
    await settled(h)
    assert any(r.rule == veto and r.result == "fail" for r in latest(h).rules)
    assert not wait(h).active and timer(h).cancelled()
    await h.manager._ctx.assessment.terminal(wait(h), "Late veto callback.")
    clock.advance(121)
    await settled(h)
    assert terminals(h) == [] and not rec(h).rules_consumed


async def test_permanent_configuration_and_closed_admission_are_visible(h: Harness, clock, ready):
    available(h)
    await select(h, "merge_when_safe", 0)
    full_evidence(h, ready)
    await propose(h)
    calls = len(h.workspace.calls)
    # The evaluator may run while another task owns main. It never waits for that lock.
    async with h.workspace.locks(main=True):
        h.manager.run_ended("agent-1", None)
        await settled(h)
    assert "reservation is not connected" in terminals(h)[0].message
    assert len(h.workspace.calls) == calls and not rec(h).rules_consumed
    assert rec(h).state == "ready" and rec(h).taken == 0
    assert next(r for r in latest(h).rules if r.rule == "combined_tests").result == "unknown"


async def test_permanent_evidence_failure_does_not_wait_for_deadline(h: Harness, clock):
    available(h)
    await select(h, "merge_when_safe", 0)
    h.manager._ctx.ports.rules_evidence = lambda inputs: RulesEvidence(
        inputs, "Configuration cannot supply review identity."
    )
    await h.run_agent(1, EDIT)
    await settled(h)
    assert "Configuration cannot supply" in terminals(h)[0].message
    assert clock.now == 0 and timer(h).cancelled()


@pytest.mark.parametrize("action", ["take", "resolution", "revision", "mode", "terminal", "close"])
async def test_invalidation_cancels_timer_and_stale_callback(h: Harness, clock, action):
    available(h)
    await select(h, "merge_when_safe", 0)
    await h.run_agent(1, EDIT)
    await settled(h)
    old = wait(h)
    assert old.timer is not None
    clock.advance(10)
    if action == "take":
        h.manager._take("change-1", APPROVABLE, "human decision")
    elif action == "resolution":
        await h.manager.reject("change-1", None, revision=1, head_commit=head(h))
    elif action == "revision":
        proposal = h.payloads(ChangeProposed)[0].model_copy(update={"revision": 2})
        h.gate = ChangeProposed
        pending = asyncio.create_task(record_proposal(h.manager._ctx, rec(h), proposal))
        await h.at_gate.wait()
        await queued_callbacks_ran()
        assert old.timer.cancelled()
        h.open_gate.set()
        await pending
        assert wait(h).deadline == 130
    elif action == "mode":
        await select(h, "ask_me", 1)
    elif action == "terminal":
        receipt = await h.publish(
            ErrorReport(
                source="server",
                change_id="change-1",
                agent_id="agent-1",
                fatal=False,
                message=format_needs_you(1, head(h), "ledger", "Stopped."),
            )
        )
        h.manager.rules_received(receipt, generation=old.target.generation)
    else:
        async with asyncio.timeout(0.5):
            await h.manager.aclose()
    await queued_callbacks_ran()
    assert old.timer.cancelled()
    # An already awakened callback is separately guarded, independent of cancellation.
    clock.advance(120)
    await h.manager._ctx.assessment.expire(old)
    await h.manager._ctx.assessment.terminal(old, "Already awakened stale callback.")
    assert len(terminals(h)) == (1 if action == "terminal" else 0)


@pytest.mark.parametrize(
    "guard",
    [
        "ready",
        "consumed",
        "resolution",
        "revision",
        "head",
        "base",
        "capture",
        "mode_revision",
        "closing",
        "generation",
        "main",
        "suspended",
    ],
)
async def test_awakened_expiry_revalidates_every_authority_field(h: Harness, clock, guard):
    available(h)
    await select(h, "merge_when_safe", 0)
    await h.run_agent(1, EDIT)
    await settled(h)
    old = wait(h)
    assert old.timer is not None
    if guard == "ready":
        rec(h).state = "working"
    elif guard == "consumed":
        rec(h).rules_consumed.add(1)
    elif guard == "resolution":
        rec(h).resolution = ChangeResolved(
            change_id="change-1",
            agent_id="agent-1",
            revision=1,
            decision="rejected",
            reason=None,
            comment=None,
            merge=None,
            merge_commit=None,
            conflict=None,
        )
    elif guard == "revision":
        rec(h).revision = 2
    elif guard == "head":
        rec(h).head_commit = "other"
    elif guard == "base":
        rec(h).base_commit = "other"
    elif guard == "capture":
        rec(h).proposal_mode = captured_mode(h).model_copy(update={"revision": 9})
    elif guard == "mode_revision":
        h.manager._ctx.mode._state = h.manager._ctx.mode.snapshot().model_copy(
            update={"revision": 2}
        )
    elif guard == "main":
        h.manager._ctx.state.snapshot = h.manager.snapshot.model_copy(update={"commit": "other"})
    elif guard == "suspended":
        h.manager._ctx.mode._suspended = True
    elif guard == "closing":
        h.manager._ctx.state.closing = True
    else:
        h.manager._ctx.assessment.waits["change-1"] = replace(
            old, target=NeedsYouWait("change-1", 1, old.target.head, 999)
        )
    clock.advance(120)
    await old.timer
    assert terminals(h) == []


async def test_terminal_race_acknowledges_only_winner(h: Harness, clock):
    available(h)
    await select(h, "merge_when_safe", 0)
    await h.run_agent(1, EDIT)
    await settled(h)
    h.gate = ErrorReport
    clock.advance(120)
    await h.at_gate.wait()
    other = ErrorReport(
        source="server",
        change_id="change-1",
        agent_id="agent-1",
        fatal=False,
        message=format_needs_you(1, head(h), "ledger", "Winner."),
    )
    receipt = await h.publish(other)
    h.manager.rules_received(receipt)
    await queued_callbacks_ran()
    assert wait(h).terminal_seq == receipt.seq and len(terminals(h)) == 1
    h.open_gate.set()
    await h.manager.aclose()
