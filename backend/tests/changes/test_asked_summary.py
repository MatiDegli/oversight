"""Asked-for claims derive from published rows, survive replay and never become questions."""

from dataclasses import replace

import pytest
from changes_helpers import EDIT
from test_review_card_lifecycle import FakeDecisions, enable, settled
from test_review_cards import direct_row
from test_summary import evidence

from oversight.changes import _proposal
from oversight.changes._behavior import BehaviorDiff, FunctionBehavior
from oversight.changes._facts import build_change_facts, validate_change_facts
from oversight.changes._summary import AskedFor, SummaryEvidence, build_summary
from oversight_contracts.review import ChangeFacts, RoleUsage


def test_no_classification_is_byte_identical():
    captured = SummaryEvidence(
        task="Do it.", changed_paths=("m.py",), changed_nodes=("m:f",), added_test_ids=()
    )
    assert build_summary(captured, {}, revision=1) == [
        "Task: Do it.",
        "Changed: 1 files, 1 nodes; tests added: 0; tests run: unknown",
        "Other results: not compared; cost unknown",
    ]


@pytest.mark.parametrize("size", [1, 8, 50])
@pytest.mark.parametrize("name_size", [1, 35, 300])
def test_many_nodes_keep_complete_names_counts_and_caps(size, name_size):
    nodes = tuple(AskedFor(f"m:{'x' * name_size}_{i}", i + 1, 48) for i in range(size))
    captured = replace(
        evidence(),
        summary_evidence=SummaryEvidence(task="task " * 100, asked_for=nodes, questions=2),
    )
    facts = build_change_facts(captured)
    assert facts.summary is not None
    line = facts.summary[2]
    assert all(len(part) <= 240 for part in facts.summary)
    assert sum(map(len, facts.summary)) <= 720
    shown = [node for node in nodes if node.node_id in line]
    assert shown == list(nodes[: len(shown)])
    for node in shown:
        assert f"{node.node_id} ({node.differing_count} of 48 inputs differ)" in line
    if len(shown) < size:
        assert f"+{size - len(shown)} more" in line
    assert "…" not in line and line.endswith("; 2 questions; cost unknown")


def test_classified_facts_summary_is_validated_against_captured_evidence():
    summary = SummaryEvidence(
        asked_for=(AskedFor("compute_price", 3, 48),),
        questions=2,
        usage=(RoleUsage(role="agent", model="fake", round=0, cost_usd=0.0123, unknown_calls=0),),
    )
    captured = replace(evidence(), summary_evidence=summary)
    facts = build_change_facts(captured)
    assert facts.summary is not None
    assert facts.summary[2] == (
        "Other results: asked for: compute_price (3 of 48 inputs differ); 2 questions; cost $0.0123"
    )
    with pytest.raises(ValueError, match="summary"):
        validate_change_facts(facts.model_copy(update={"summary": ["Forged"]}), captured)


@pytest.mark.anyio
async def test_mixed_classifications_use_public_counts_and_keep_deadline_problem(h, monkeypatch):
    cards, client = enable(h, monkeypatch)
    original = client.classify_difference

    async def classify(input, *, deadline):
        client.choice = {
            "m:a": "asked_for",
            "m:b": "not_asked_for",
            "m:c": "asked_for",
        }.get(input.difference.node_id, "asked_for")
        if input.difference.node_id == "m:d":
            raise TimeoutError
        return await original(input, deadline=deadline)

    async def probe(ctx, rec, *, head, revision):
        return BehaviorDiff(
            revision,
            tuple(
                FunctionBehavior(node, 48, index + 2, (direct_row(repeated=True),))
                for index, node in enumerate(("m:d", "m:c", "m:b", "m:a"))
            ),
        )

    client.classify_difference = classify
    monkeypatch.setattr(_proposal, "probe_behavior", probe)
    await h.run_agent(1, EDIT)
    await settled(cards)
    first, final = h.payloads(ChangeFacts)
    assert first.summary[2] == "Other results: not compared; cost unknown"
    assert final.summary[2] == (
        "Other results: asked for: m:a (5 of 48 inputs differ), "
        "m:c (3 of 48 inputs differ); 1 question; cost unknown"
    )
    assert [card.node_id for card in final.review_cards] == ["m:b"]
    [job] = cards.jobs.values()
    assert job.asked_for == [AskedFor("m:a", 5, 48), AskedFor("m:c", 3, 48)]
    assert any("m:d: deadline" in problem for problem in final.problems)
    cards.validate(job, final)


@pytest.mark.parametrize("choice", ["asked_for", "not_asked_for", "cannot_tell"])
@pytest.mark.anyio
async def test_card_update_rejects_forged_summary_even_without_cards(h, monkeypatch, choice):
    cards, _ = enable(h, monkeypatch, FakeDecisions(choice))
    await h.run_agent(1, EDIT)
    await settled(cards)
    [job] = cards.jobs.values()
    final = h.payloads(ChangeFacts)[-1]
    forged = final.model_copy(update={"summary": ["Forged"]})
    with pytest.raises(ValueError, match="summary"):
        cards.validate(job, forged)
    before = h.events()
    with pytest.raises(ValueError, match="summary"):
        await cards.update(job.capture.target, {"summary": ["Forged"]})
    assert h.events() == before and job.facts == final


@pytest.mark.anyio
async def test_zero_classifications_leaves_summary_unchanged(h, monkeypatch):
    from oversight.changes._review_cards import ReviewCardLimits

    cards, client = enable(h, monkeypatch, limits=ReviewCardLimits(0))
    await h.run_agent(1, EDIT)
    await settled(cards)
    first, final = h.payloads(ChangeFacts)
    assert first.summary == final.summary and not client.calls
    [job] = cards.jobs.values()
    assert job.asked_for == [] and job.summary_evidence.questions is None
