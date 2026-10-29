"""Human answers travel with a decision and are bound to confirmed current cards."""

import pytest
from changes_helpers import EDIT, TWICE, VALUE
from decision_helpers import observed_target
from test_review_card_lifecycle import FakeDecisions, enable, settled
from test_review_cards import direct_row

from oversight.changes import InvalidAnswers, WrongState, _proposal
from oversight.changes._behavior import BehaviorDiff, FunctionBehavior
from oversight_contracts.events import ChangeResolved

pytestmark = pytest.mark.anyio


async def questions(h, monkeypatch, client=None):
    cards, client = enable(h, monkeypatch, client)

    async def committed(payload):
        receipt = await h.publish(payload)
        h.manager.rules_received(receipt)
        return receipt

    cards._publish = committed

    async def probe(ctx, rec, *, head, revision):
        return BehaviorDiff(
            revision,
            tuple(
                FunctionBehavior(node, 1, 1, (direct_row(repeated=True),))
                for node in (VALUE, TWICE)
            ),
        )

    monkeypatch.setattr(_proposal, "probe_behavior", probe)
    await h.run_agent(1, EDIT)
    return cards, client


def published(cards):
    job = list(cards.jobs.values())[-1]
    assert job.facts is not None and job.facts.review_cards is not None
    return job.facts.review_cards


def answer(card, value="yes"):
    return card.model_copy(deep=True, update={"answer": value, "answered_by": "person"})


@pytest.mark.parametrize("value", [None, "no", "unsure"])
async def test_approve_refuses_open_questions_without_taking_or_publishing(h, monkeypatch, value):
    cards, _ = await questions(h, monkeypatch)
    await settled(cards)
    current = published(cards)
    answers = [answer(current[0])]
    if value is not None:
        answers.append(answer(current[1], value))
    before = list(h.log)
    rec = h.manager._agents["agent-1"]
    generation = rec.card_generation
    with pytest.raises(WrongState, match=current[1].question):
        h.manager.approve(
            "change-1", None, answers=answers, **observed_target(h.manager, "change-1")
        )
    assert h.log == before and rec.state == "ready" and rec.decision == 0
    assert rec.card_generation == generation and not rec.person_acted and rec.evidence is None
    assert await h.main_head() == h.base_commit


async def test_pending_questions_refuse_approve_without_cancelling_job(h, monkeypatch):
    cards, client = await questions(h, monkeypatch, FakeDecisions(hold=True))
    await client.entered.wait()
    try:
        before = list(h.log)
        with pytest.raises(WrongState, match="questions are still being prepared"):
            h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
        assert h.log == before and not h.manager._agents["agent-1"].person_acted
    finally:
        client.release.set()
        await settled(cards)


@pytest.mark.parametrize(
    "fault",
    ["node_id", "question", "evidence_seq", "example", "duplicate", "principal", "unanswered"],
)
@pytest.mark.parametrize("verb", ["approve", "request_changes", "reject"])
async def test_invalid_answer_is_refused_before_any_effect(h, monkeypatch, fault: str, verb: str):
    cards, _ = await questions(h, monkeypatch)
    await settled(cards)
    card = published(cards)[0]
    forged = answer(card)
    updates = {
        "node_id": "foreign:f",
        "question": "Forged question?",
        "evidence_seq": card.evidence_seq + 100,
        "example": None,
        "principal": None,
        "unanswered": None,
    }
    field: str = {"principal": "answered_by", "unanswered": "answer"}.get(fault, fault)
    if fault != "duplicate":
        forged = forged.model_copy(update={field: updates[fault]})
    answers = [forged, answer(card, "no")] if fault == "duplicate" else [forged]
    before = list(h.log)
    with pytest.raises(InvalidAnswers) as error:
        getattr(h.manager, verb)(
            "change-1", "Review", answers=answers, **observed_target(h.manager, "change-1")
        )
    assert forged.node_id in str(error.value) and forged.question in str(error.value)
    assert h.log == before and h.manager._agents["agent-1"].state == "ready"
    assert h.manager._agents["agent-1"].decision == 0


async def test_yes_to_every_question_merges_and_records_in_server_card_order(h, monkeypatch):
    cards, _ = await questions(h, monkeypatch)
    await settled(cards)
    current = published(cards)
    submitted = [answer(card) for card in reversed(current)]
    task = h.manager.approve(
        "change-1", "Accepted", answers=submitted, **observed_target(h.manager, "change-1")
    )
    submitted.clear()
    await task
    [resolved] = h.payloads(ChangeResolved)
    assert resolved.merge == "merged" and await h.main_head() != h.base_commit
    assert resolved.answers == [answer(card) for card in current]
    assert resolved.answers[0] is not current[0]
    assert all(card.answer is None for card in current)


@pytest.mark.parametrize("verb,value", [("request_changes", "no"), ("reject", "unsure")])
async def test_open_questions_do_not_block_other_decisions(h, monkeypatch, verb, value):
    cards, _ = await questions(h, monkeypatch)
    await settled(cards)
    accepted = [answer(published(cards)[1], value)]
    await getattr(h.manager, verb)(
        "change-1", "Review", answers=accepted, **observed_target(h.manager, "change-1")
    )
    [resolved] = h.payloads(ChangeResolved)
    assert resolved.answers == accepted and await h.main_head() == h.base_commit


async def test_another_revision_cannot_answer_current_cards(h, monkeypatch):
    cards, _ = await questions(h, monkeypatch)
    await settled(cards)
    old = [answer(card) for card in published(cards)]
    await h.manager.request_changes("change-1", "Again", **observed_target(h.manager, "change-1"))
    assert await h.edit(1, "shop/mod.py", "return 3", "return 4")
    assert await h.finish(1)
    await settled(cards)
    before = list(h.log)
    with pytest.raises(InvalidAnswers, match=old[0].node_id):
        h.manager.reject("change-1", None, answers=old, **observed_target(h.manager, "change-1"))
    assert h.log == before and h.manager._agents["agent-1"].revision == 2


async def test_late_result_cannot_change_resolution_answers(h, monkeypatch):
    cards, client = await questions(h, monkeypatch, FakeDecisions(hold=True, late=True))
    await client.entered.wait()
    await h.manager.reject("change-1", None, **observed_target(h.manager, "change-1"))
    before = list(h.log)
    client.release.set()
    await settled(cards)
    assert h.log == before and h.payloads(ChangeResolved)[0].answers is None


@pytest.mark.parametrize("status", ["failed", "truncated", "finished"])
async def test_no_published_cards_keeps_manual_approve_behavior(h, monkeypatch, status):
    cards, _ = await questions(h, monkeypatch, FakeDecisions("asked_for"))
    await settled(cards)
    list(cards.jobs.values())[-1].status = status
    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    [resolved] = h.payloads(ChangeResolved)
    assert resolved.merge == "merged" and resolved.answers is None


async def test_cards_from_another_head_are_not_current_answers(h, monkeypatch):
    cards, _ = await questions(h, monkeypatch)
    await settled(cards)
    await h.run_agent(2, ("shop/mod.py", "return 1", "return 4"))
    await settled(cards)
    first, second = cards.jobs.values()
    assert first.capture.target.head_commit != second.capture.target.head_commit
    foreign = [answer(card) for card in published(cards)]
    before = list(h.log)
    with pytest.raises(InvalidAnswers, match=foreign[0].question):
        h.manager.reject(
            "change-1", None, answers=foreign, **observed_target(h.manager, "change-1")
        )
    assert h.log == before and h.manager._agents["agent-1"].state == "ready"


async def test_published_cards_remain_required_after_failed_approval(h, monkeypatch):
    cards, _ = await questions(h, monkeypatch)
    await settled(cards)
    yes = [answer(card) for card in published(cards)]
    h.workspace.arm("merge", mode="timeout")
    await h.manager.approve("change-1", None, answers=yes, **observed_target(h.manager, "change-1"))
    rec = h.manager._agents["agent-1"]
    assert rec.state == "ready" and not h.payloads(ChangeResolved)
    before = list(h.log)
    with pytest.raises(WrongState, match="Approve needs Yes"):
        h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    assert h.log == before
    await h.manager.approve("change-1", None, answers=yes, **observed_target(h.manager, "change-1"))
    assert h.payloads(ChangeResolved)[0].answers == yes


async def test_late_card_update_preserves_accepted_answers(h, monkeypatch):
    cards, _ = await questions(h, monkeypatch)
    await settled(cards)
    [job] = cards.jobs.values()
    accepted = [answer(published(cards)[0], "unsure")]
    await h.manager.reject(
        "change-1", None, answers=accepted, **observed_target(h.manager, "change-1")
    )
    before = list(h.log)
    with pytest.raises(ValueError, match="no longer current"):
        await h.manager.update_facts(job.capture.target, {"review_cards": []})
    await cards._finish(job)
    assert h.log == before and h.manager._agents["agent-1"].state == "closed"
    assert h.payloads(ChangeResolved)[0].answers == accepted


@pytest.mark.parametrize("verb", ["approve", "request_changes", "reject"])
@pytest.mark.parametrize("answers", [None, []])
async def test_decisions_off_preserve_null_or_explicitly_empty_answers(h, verb, answers):
    await h.run_agent(1, EDIT)
    assert h.manager._ctx.cards is None
    await getattr(h.manager, verb)(
        "change-1", "Review", answers=answers, **observed_target(h.manager, "change-1")
    )
    assert h.payloads(ChangeResolved)[0].answers == answers
