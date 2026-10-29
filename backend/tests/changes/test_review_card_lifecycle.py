"""Captured classification is cancellable; publication and composition retain confirmed facts."""

import asyncio
from dataclasses import replace
from typing import cast

import pytest
from changes_helpers import EDIT, VALUE
from decision_helpers import observed_target
from test_review_cards import direct_row

from oversight.agents import DecisionChoice, DecisionMissing, DecisionSelected
from oversight.changes import _proposal
from oversight.changes._behavior import BehaviorDiff, FunctionBehavior
from oversight.changes._review_cards import QUESTION, UNCERTAIN, ReviewCardLimits, ReviewCards
from oversight_contracts.review import ChangeFacts

pytestmark = pytest.mark.anyio


class FakeDecisions:
    def __init__(self, choice="not_asked_for", hold=False, late=False):
        self.choice, self.hold, self.late = choice, hold, late
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.calls = []

    async def classify_difference(self, input, *, deadline):
        self.calls.append((input, deadline))
        self.entered.set()
        if self.hold:
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                if not self.late:
                    raise
        if self.choice == "missing":
            return DecisionMissing(input.target, input.difference.node_id, "invalid_response")
        return DecisionSelected(
            input.target, input.difference.node_id, cast(DecisionChoice, self.choice)
        )


def enable(h, monkeypatch, client=None, limits=None):
    client = client or FakeDecisions()
    ctx = h.manager._ctx
    ctx.behavior_diff = True

    async def probe(ctx, rec, *, head, revision):
        return BehaviorDiff(
            revision, (FunctionBehavior(VALUE, 1, 1, (direct_row(repeated=True),)),)
        )

    monkeypatch.setattr(_proposal, "probe_behavior", probe)
    ctx.cards = ReviewCards(ctx, h.store.run_id, client, limits or ReviewCardLimits(), h.publish)
    return ctx.cards, client


async def settled(cards):
    async with asyncio.timeout(10):
        await asyncio.gather(*list(cards.tasks))


@pytest.mark.parametrize(
    "choice,question",
    [
        ("asked_for", None),
        ("not_asked_for", QUESTION),
        ("cannot_tell", UNCERTAIN),
        ("missing", None),
    ],
)
async def test_choices_use_prior_confirmed_seq_and_never_decide(h, monkeypatch, choice, question):
    cards, client = enable(h, monkeypatch, FakeDecisions(choice))
    await h.run_agent(1, EDIT)
    await settled(cards)
    first, final = h.payloads(ChangeFacts)
    assert "pending" in first.problems[-1] and "finished" in final.problems[-1]
    assert first.behavior_diff == final.behavior_diff
    assert first.summary == final.summary and first.test_runs == final.test_runs
    assert first.complete is final.complete is False
    assert h.manager._agents["agent-1"].state == "ready"
    assert len(client.calls) == 1 and client.calls[0][0].confirmed_examples == ()
    assert client.calls[0][0].task == "Do it."
    if question:
        [card] = final.review_cards
        assert card.question == question and card.answer is card.answered_by is None
        [evidence] = [e for e in h.events() if e.seq == card.evidence_seq]
        assert evidence.payload == first and evidence.seq < h.events()[-1].seq
    else:
        assert final.review_cards == []
    if choice == "missing":
        assert any("invalid_response" in problem for problem in final.problems)


async def test_republication_preserves_calls_and_latest_other_producer_data(h, monkeypatch):
    cards, client = enable(h, monkeypatch)
    await h.run_agent(1, EDIT)
    await settled(cards)
    [job] = cards.jobs.values()
    assert job.facts is not None and job.facts.review_cards is not None
    card = job.facts.review_cards[0].model_copy(update={"answer": "yes", "answered_by": "person"})
    await h.manager.update_facts(
        job.capture.target,
        {
            "review_cards": [card.model_dump()],
            "shape": [],
            "shape_complete": True,
        },
    )
    await cards._finish(job)
    latest = job.facts
    assert latest is not None and latest.review_cards is not None
    await cards.publish(h.payloads(ChangeFacts)[0])
    assert len(client.calls) == 1
    assert latest.shape_complete and latest.review_cards[0].answer == "yes"
    assert job.facts == latest


@pytest.mark.parametrize("action", ["edit", "reject", "revision", "close"])
async def test_invalidated_or_late_result_never_republishes(h, monkeypatch, action):
    cards, client = enable(h, monkeypatch, FakeDecisions(hold=True, late=True))
    await h.run_agent(1, EDIT)
    async with asyncio.timeout(5):
        await client.entered.wait()
    rec = h.manager._agents["agent-1"]
    if action == "edit":
        # The edit admission itself invalidates, even if no replacement proposal follows.
        rec.person_intervened()
    elif action == "reject":
        await h.manager.reject("change-1", None, **observed_target(h.manager, "change-1"))
    elif action == "revision":
        await h.manager.request_changes(
            "change-1", "Again", **observed_target(h.manager, "change-1")
        )
        assert await h.edit(1, "shop/mod.py", "return 3", "return 4")
        client.hold = False
        assert await h.finish(1)
    else:
        await cards.close()
    client.release.set()
    await settled(cards)
    facts = h.payloads(ChangeFacts)
    assert not any(f.revision == 1 and f.review_cards for f in facts)
    if action == "revision":
        assert facts[-1].revision == 2 and facts[-1].review_cards


async def test_no_confirmed_measurement_no_classification(h, monkeypatch):
    cards, client = enable(h, monkeypatch)
    h.fail_once = ChangeFacts
    await h.open(1)
    assert await h.edit(1, *EDIT)
    assert not await h.finish(1)
    await settled(cards)
    assert client.calls == [] and h.payloads(ChangeFacts) == []


async def test_result_target_and_node_are_checked(h, monkeypatch):
    cards, client = enable(h, monkeypatch)
    original = client.classify_difference

    async def foreign(input, *, deadline):
        outcome = await original(input, deadline=deadline)
        return replace(outcome, node_id="foreign:f")

    client.classify_difference = foreign
    await h.run_agent(1, EDIT)
    await settled(cards)
    assert h.payloads(ChangeFacts)[-1].review_cards == []
    assert "identity mismatch" in h.payloads(ChangeFacts)[-1].problems[-2]


async def test_deadline_includes_provider_wait_and_late_returns(h, monkeypatch):
    cards, client = enable(
        h, monkeypatch, FakeDecisions(hold=True, late=True), ReviewCardLimits(8, 0.01, 0.02)
    )
    await h.run_agent(1, EDIT)
    await settled(cards)
    final = h.payloads(ChangeFacts)[-1]
    assert final.review_cards == [] and any("deadline" in p for p in final.problems)
    [job] = cards.jobs.values()
    assert job.consumed == 1
    assert client.calls[0][1] <= job.deadline


@pytest.mark.parametrize("tamper", ["absent", "foreign", "example", "duplicate", "self"])
async def test_semantic_validation_rejects_invalid_evidence(h, monkeypatch, tamper):
    cards, _ = enable(h, monkeypatch)
    await h.run_agent(1, EDIT)
    await settled(cards)
    [job] = cards.jobs.values()
    assert job.facts is not None and job.latest is not None
    facts = job.facts.model_copy(deep=True)
    assert facts.review_cards is not None
    card = facts.review_cards[0]
    assert card.example is not None
    if tamper == "absent":
        facts.review_cards[0] = card.model_copy(update={"evidence_seq": 999999})
    elif tamper == "self":
        facts.review_cards[0] = card.model_copy(update={"evidence_seq": job.latest.seq})
    elif tamper == "foreign":
        event = cards.events[card.evidence_seq]
        cards.events[card.evidence_seq] = event.model_copy(
            update={
                "payload": event.payload.model_copy(update={"head_commit": "foreign"}),
            }
        )
    elif tamper == "example":
        facts.review_cards[0] = card.model_copy(
            update={
                "example": card.example.model_copy(update={"after": "invented"}),
            }
        )
    else:
        facts.review_cards.append(card)
    with pytest.raises(ValueError):
        cards.validate(job, facts)


async def test_budget_zero_has_no_calls_and_explicit_omission(h, monkeypatch):
    cards, client = enable(h, monkeypatch, limits=ReviewCardLimits(0))
    await h.run_agent(1, EDIT)
    await settled(cards)
    assert client.calls == []
    assert any("1 nodes omitted" in p for p in h.payloads(ChangeFacts)[-1].problems)
