"""One duration source, bounded receipt retention and observed background failures."""

import logging

import pytest
from changes_helpers import EDIT, Harness, queued_callbacks_ran
from test_assessment import head, latest, make_clock, settled, terminals, wait
from test_mode import available, select

from oversight.changes import _assessment
from oversight.events import format_needs_you
from oversight_contracts.events import ErrorReport
from oversight_contracts.policy import ChangeAssessment

pytestmark = pytest.mark.anyio
LOGGER = "oversight.changes._assessment"


@pytest.fixture
def clock(h, monkeypatch):
    return make_clock(h, monkeypatch)


async def test_duration_constant_controls_deadline_and_message(h: Harness, clock, monkeypatch):
    monkeypatch.setattr(_assessment, "EVIDENCE_TIMEOUT_S", 7)
    available(h)
    await select(h, "merge_when_safe", 0)
    await h.run_agent(1, EDIT)
    await settled(h)
    assert wait(h).deadline == 7
    clock.advance(6.999)
    await queued_callbacks_ran()
    assert terminals(h) == []
    clock.advance(7)
    timer = wait(h).timer
    assert timer is not None
    await timer
    assert terminals(h)[0].message == format_needs_you(
        1, head(h), "policy_invalid", "Required rules evidence was not confirmed within 7 seconds."
    )


async def test_receipt_cache_is_bounded_and_evicted_duplicates_do_not_wake(
    h: Harness, clock, monkeypatch
):
    monkeypatch.setattr(_assessment, "MAX_EVIDENCE_EVENTS", 8)
    available(h)
    await select(h, "merge_when_safe", 0)
    await h.run_agent(1, EDIT)
    await settled(h)
    scheduler = h.manager._ctx.assessment
    old_receipt = next(row for row in h.log if row.type == "change.proposed")
    for _ in range(20):
        await h.manager._ctx.publish(
            ErrorReport(
                source="server",
                message="Ordinary diagnostic.",
                change_id="change-1",
                agent_id="agent-1",
                fatal=False,
            )
        )
        assert len(scheduler.events) <= 8
    assert old_receipt.seq not in scheduler.events
    calls = clock.calls.call_count
    h.manager.rules_received(old_receipt)
    await settled(h)
    assert len(scheduler.events) == 8 and clock.calls.call_count == calls
    scheduler.wake(wait(h).rec)
    await settled(h)
    # Losing retained receipts can remove proof, never manufacture a pass.
    assert all(rule.result == "unknown" and not rule.evidence for rule in latest(h).rules)
    assert wait(h).deadline == 120 and len(clock.registrations) == 1


@pytest.mark.parametrize("failure", ["publish", "evidence", "timer"])
async def test_background_failures_are_retrieved_and_logged(h: Harness, clock, caplog, failure):
    caplog.set_level(logging.ERROR, logger=LOGGER)
    available(h)
    await select(h, "merge_when_safe", 0)
    if failure == "publish":
        h.fail_once = ChangeAssessment
    elif failure == "evidence":

        def broken(inputs):
            raise RuntimeError("Evidence port failed")

        h.manager._ctx.ports.rules_evidence = broken
    await h.run_agent(1, EDIT)
    if failure == "timer":
        await settled(h)
        h.fail_once = ErrorReport
        clock.advance(120)
        task = wait(h).timer
    else:
        task = wait(h).task
    assert task is not None
    for _ in range(10):
        await queued_callbacks_ran()
        if task.done():
            break
    assert task.done()
    await queued_callbacks_ran()
    # Do not await/gather the failed task: the production callback must retrieve it.
    assert not task._log_traceback
    [record] = [record for record in caplog.records if record.name == LOGGER]
    assert record.message == "Rules scheduler task failed"
    assert record.exc_info is not None and isinstance(record.exc_info[1], RuntimeError)


async def test_cancelled_tasks_do_not_log_failures(h: Harness, clock, caplog):
    caplog.set_level(logging.ERROR, logger=LOGGER)
    available(h)
    await select(h, "merge_when_safe", 0)
    await h.run_agent(1, EDIT)
    await settled(h)
    await h.manager.aclose()
    await queued_callbacks_ran()
    assert caplog.records == []
