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
        await asyncio.gather(*list(cards.tasks), return_exceptions=True)


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
    assert first.summary[:2] == final.summary[:2] and first.test_runs == final.test_runs
    assert first.complete is final.complete is False
    assert h.manager._agents["agent-1"].state == "ready"
    assert len(client.calls) == 1 and client.calls[0][0].confirmed_examples == ()
    assert client.calls[0][0].task == "Do it."
    if question:
        [card] = final.review_cards
        assert card.question == question and card.answer is card.answered_by is None
        [evidence] = [e for e in h.events() if e.seq == card.evidence_seq]
        assert evidence.payload == first and evidence.seq < h.events()[-1].seq
        assert final.summary[2] == "Other results: 1 question; cost unknown"
    else:
        assert final.review_cards == []
    if choice == "asked_for":
        assert final.summary[2] == (
            f"Other results: asked for: {VALUE} (1 of 1 inputs differ); 0 questions; cost unknown"
        )
    if choice == "missing":
        assert any("invalid_response" in problem for problem in final.problems)
        assert first.summary == final.summary
        assert next(iter(cards.jobs.values())).asked_for == []


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
    first = h.payloads(ChangeFacts)[0]
    with pytest.raises(ValueError, match="measurements"):
        await cards.publish(first)
    receipt = job.latest
    events = len(h.log)
    assert (
        await cards.publish(
            latest.model_copy(
                update={
                    "review_cards": [],
                    "problems": ["Another producer's diagnostics"],
                    "summary": first.summary,
                }
            )
        )
        == receipt
    )
    assert len(h.log) == events
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
    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    [job] = cards.jobs.values()
    assert job.status == "failed" and not job.preparing and job.task is None
    assert await h.main_head() != h.base_commit


@pytest.mark.parametrize(
    "updates",
    [
        {"test_runs": [999]},
        {"edited_paths": ["shop/other.py"]},
        {"behavior_diff": None},
        {"measured_cone": []},
        {"shape": []},
        {"shape_complete": True},
        {"complete": True, "problems": []},
    ],
)
@pytest.mark.parametrize("failed", [False, True])
async def test_changed_republication_fails_before_emitting(h, monkeypatch, updates, failed):
    cards, client = enable(h, monkeypatch)
    await h.run_agent(1, EDIT)
    await settled(cards)
    [job] = cards.jobs.values()
    facts, receipt = job.facts, job.latest
    assert facts is not None
    if failed:
        job.status = "failed"
    status = job.status
    events = len(h.log)
    changed = ChangeFacts.model_validate(facts.model_dump() | updates)
    with pytest.raises(ValueError, match="measurements"):
        await cards.publish(changed)
    assert len(h.log) == events and len(client.calls) == 1
    assert job.facts == facts and job.latest == receipt and job.status == status


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
    assert next(iter(cards.jobs.values())).asked_for == []
    assert h.payloads(ChangeFacts)[0].summary == h.payloads(ChangeFacts)[-1].summary


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
    assert job.asked_for == [] and h.payloads(ChangeFacts)[0].summary == final.summary
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


async def test_agent_edit_without_a_new_proposal_invalidates_inflight_classification(
    h, monkeypatch
):
    cards, client = enable(h, monkeypatch, FakeDecisions(hold=True, late=True))
    await h.open(1)
    assert await h.edit(1, *EDIT)
    assert await h.manager.proposed("agent-1", "Done")
    async with asyncio.timeout(5):
        await client.entered.wait()
    assert await h.edit(1, "shop/mod.py", "return 3", "return 4")
    await settled(cards)
    assert len(h.payloads(ChangeFacts)) == 1
    assert h.manager._agents["agent-1"].revision == 1


async def test_total_deadline_and_one_logical_call_per_node(h, monkeypatch):
    cards, client = enable(
        h, monkeypatch, FakeDecisions(hold=True), ReviewCardLimits(8, 0.02, 0.01)
    )

    async def probe(ctx, rec, *, head, revision):
        return BehaviorDiff(
            revision,
            tuple(
                FunctionBehavior(node, 1, 1, (direct_row(repeated=True),))
                for node in (VALUE, "shop.mod:twice")
            ),
        )

    monkeypatch.setattr(_proposal, "probe_behavior", probe)
    await h.run_agent(1, EDIT)
    await settled(cards)
    [job] = cards.jobs.values()
    assert len(client.calls) == job.consumed == 1
    assert job.status == "truncated" and any(
        "remaining nodes unclassified" in p for p in job.problems
    )


async def test_unrelated_main_advance_keeps_the_captured_target(h, monkeypatch):
    from changes_helpers import commit_on_main

    cards, client = enable(h, monkeypatch, FakeDecisions(hold=True))
    await h.run_agent(1, EDIT)
    async with asyncio.timeout(5):
        await client.entered.wait()
    [job] = cards.jobs.values()
    target = job.capture.target
    await commit_on_main(h, "shop/__init__.py", "", "# Unrelated main edit\n")
    client.release.set()
    await settled(cards)
    assert job.facts is not None and job.facts.main_commit == target.main_commit
    assert job.facts.review_cards


async def test_concurrent_other_producer_update_survives_classification(h, monkeypatch):
    cards, client = enable(h, monkeypatch, FakeDecisions(hold=True))
    await h.run_agent(1, EDIT)
    async with asyncio.timeout(5):
        await client.entered.wait()
    [job] = cards.jobs.values()
    await h.manager.update_facts(job.capture.target, {"shape": [], "shape_complete": True})
    client.release.set()
    await settled(cards)
    assert job.facts is not None and job.facts.shape_complete and job.facts.review_cards


async def test_same_revision_cannot_reset_budget_under_different_main_context(h, monkeypatch):
    from oversight.changes._facts import FactsEvidence

    cards, client = enable(h, monkeypatch)
    await h.run_agent(1, EDIT)
    await settled(cards)
    [job] = cards.jobs.values()
    foreign = job.capture.target.model_copy(update={"main_commit": "other"})
    evidence = FactsEvidence("agent-1", foreign, None, (), (), (), {}, {})
    with pytest.raises(ValueError, match="different target"):
        cards.prepare(h.manager._agents["agent-1"], evidence)
    assert len(client.calls) == 1


@pytest.mark.parametrize("choice", ["not_asked_for", "asked_for"])
async def test_failed_final_publication_can_be_republished_without_new_calls(
    h, monkeypatch, choice
):
    cards, client = enable(h, monkeypatch, FakeDecisions(choice, hold=True))
    await h.run_agent(1, EDIT)
    async with asyncio.timeout(5):
        await client.entered.wait()
    h.fail_once = ChangeFacts
    client.release.set()
    await settled(cards)
    [job] = cards.jobs.values()
    assert job.status == "failed" and len(h.payloads(ChangeFacts)) == 1
    first = h.payloads(ChangeFacts)[0]
    await cards.publish(first)
    await cards.publish(first)
    assert len(client.calls) == 1 and len(h.payloads(ChangeFacts)) == 2
    if choice == "asked_for":
        assert job.asked_for and "asked for:" in h.payloads(ChangeFacts)[-1].summary[2]
    else:
        assert h.payloads(ChangeFacts)[-1].review_cards


async def test_serialized_final_publication_precedes_the_invalidating_edit(h, monkeypatch):
    cards, client = enable(h, monkeypatch, FakeDecisions(hold=True))
    await h.open(1)
    assert await h.edit(1, *EDIT)
    assert await h.manager.proposed("agent-1", "Done")
    async with asyncio.timeout(5):
        await client.entered.wait()
    h.gate = ChangeFacts
    client.release.set()
    async with asyncio.timeout(5):
        await h.at_gate.wait()
    editing = asyncio.create_task(h.edit(1, "shop/mod.py", "return 3", "return 4"))
    await asyncio.sleep(0)
    h.open_gate.set()
    assert await editing
    await settled(cards)
    events = h.events()
    final_seq = next(
        e.seq for e in events if e.type == "change.facts_recorded" and e.payload.review_cards
    )
    edit_seq = max(e.seq for e in events if e.type == "change.updated")
    assert final_seq < edit_seq


@pytest.mark.parametrize("budget", [2, 8, 9])
async def test_logical_budget_is_distinct_from_contractual_card_cap(h, monkeypatch, budget):
    cards, client = enable(h, monkeypatch, limits=ReviewCardLimits(budget))

    async def probe(ctx, rec, *, head, revision):
        return BehaviorDiff(
            revision,
            tuple(
                FunctionBehavior(f"m:f{i}", 1, 1, (direct_row(repeated=True, call=f"f(x={i})"),))
                for i in reversed(range(9))
            ),
        )

    monkeypatch.setattr(_proposal, "probe_behavior", probe)
    await h.run_agent(1, EDIT)
    await settled(cards)
    final = h.payloads(ChangeFacts)[-1]
    assert final.review_cards is not None
    assert len(client.calls) == budget and len(final.review_cards) == min(8, budget)
    assert [c.node_id for c in final.review_cards] == [
        c[0].difference.node_id for c in client.calls[:8]
    ]
    assert any("limit" in p for p in final.problems)
