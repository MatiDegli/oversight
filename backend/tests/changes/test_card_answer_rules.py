"""Rules authority yields once to the person when questions are pending or unresolved."""

import pytest
from test_assessment import full_evidence, make_clock, rec, settled, terminals, wait
from test_card_answers import questions
from test_mode import available, select
from test_policy_profiles import policy_inputs
from test_review_card_lifecycle import FakeDecisions
from test_review_card_lifecycle import settled as cards_settled

from oversight.changes import _approval
from oversight.events import parse_needs_you
from oversight_contracts.events import ChangeResolved

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("status", ["pending", "open", "failed", "truncated", "empty", "off"])
async def test_rules_wait_once_for_questions_and_keep_empty_or_off_behavior(h, monkeypatch, status):
    clock = make_clock(h, monkeypatch)
    available(h)
    await select(h, "merge_when_safe", 0)
    full_evidence(h, policy_inputs())
    admitted = []

    async def approve(ctx, reservation):
        admitted.append(reservation)

    monkeypatch.setattr(_approval, "approve_rules", approve)
    # Prepare a proposal, but release runtime readiness only after fixing the job state.
    end = h.manager.run_ended
    monkeypatch.setattr(h.manager, "run_ended", lambda *a, **kw: None)
    client = FakeDecisions(
        "not_asked_for" if status == "open" else "asked_for", hold=status == "pending"
    )
    cards, _ = await questions(h, monkeypatch, client)
    if status != "pending":
        await cards_settled(cards)
        job = list(cards.jobs.values())[-1]
        if status in {"failed", "truncated"}:
            job.status = status
    if status == "off":
        h.manager._ctx.cards = None
    # The test producer uses the harness publisher; feed its committed facts to the scheduler.
    for receipt in h.log:
        h.manager.rules_received(receipt)
    monkeypatch.setattr(h.workspace._isolation, "_born", True)
    monkeypatch.setattr(h.workspace._isolation, "_checked", True)
    try:
        end("agent-1", None)
        await settled(h)
        if status in {"empty", "off"}:
            assert len(admitted) == 1 and terminals(h) == []
            assert admitted[0].evidence.answers is None
        else:
            assert admitted == [] and rec(h).state == "ready"
            [terminal] = terminals(h)
            note = parse_needs_you(terminal.message)
            assert note is not None and note.code == "policy_invalid"
            expected = {
                "pending": "still being prepared",
                "open": "person's answers",
                "failed": "could not be completed",
                "truncated": "could not be completed",
            }[status]
            assert expected in note.reason
            assert wait(h).terminal_seq is not None and not rec(h).rules_consumed
            client.release.set()
            await cards_settled(cards)
            clock.advance(121)
            h.manager._ctx.assessment.wake(rec(h))
            await settled(h)
            assert len(terminals(h)) == 1 and admitted == []
        assert not h.payloads(ChangeResolved) and await h.main_head() == h.base_commit
    finally:
        client.release.set()
        await cards_settled(cards)
