"""Outcome groups cover reproducible inputs without expanding the public row cap."""

import time
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from changes_helpers import EDIT
from decision_helpers import observed_target
from test_behavior_publication import comparison_fixture
from test_review_card_lifecycle import FakeDecisions, enable, settled

from oversight.changes import _proposal
from oversight.changes._answers import InvalidAnswers, validate_answers
from oversight.changes._behavior import BehaviorDiff, _compare
from oversight.changes._behavior_publish import capture_behavior, project_behavior
from oversight.changes._facts import FactsEvidence
from oversight.changes._review_cards import QUESTION, UNCERTAIN, selection
from oversight.workspace import BehaviorObservation
from oversight_contracts.events import ChangeResolved
from oversight_contracts.review import ChangeFacts


def value(number):
    return {"kind": "value", "value": ["int", hex(number)], "fingerprint": f"v{number}"}


def raised(name, message="failure"):
    return {"kind": "raised", "class": name, "message": message, "fingerprint": name + message}


async def measure(pairs, *, calls=None, repeat="stable"):
    rec, inputs, found = comparison_fixture(len(pairs))
    if calls is not None:
        inputs["sample:f"] = replace(
            inputs["sample:f"],
            inputs=tuple(
                replace(item, call=call)
                for item, call in zip(inputs["sample:f"].inputs, calls, strict=True)
            ),
        )

    def observed(results):
        return {"sample:f": BehaviorObservation(tuple(results))}

    results = [observed([old for old, _ in pairs]), observed([new for _, new in pairs])]
    for old, new in pairs:
        results.extend(
            [
                observed([old]),
                observed([value(999)])
                if repeat == "unstable"
                else {"sample:f": BehaviorObservation(problem="unavailable")}
                if repeat == "incomplete"
                else observed([new]),
            ]
        )
    worker = AsyncMock(side_effect=results)
    await _compare(
        cast(Any, SimpleNamespace(run_behavior=worker)),
        rec,
        cast(Any, ["base", "head"]),
        inputs,
        found,
        time.monotonic() + 30,
        set(),
    )
    assert worker.await_count == 2 + 2 * sum(old != new for old, new in pairs)
    return found[0]


async def publish(h, monkeypatch, functions, *, choice="not_asked_for"):
    client = FakeDecisions(choice)
    if isinstance(choice, tuple):
        classify = client.classify_difference

        async def sequence(input, *, deadline):
            client.choice = choice[len(client.calls)]
            return await classify(input, deadline=deadline)

        client.classify_difference = sequence
    cards, client = enable(h, monkeypatch, client)

    async def probe(ctx, rec, *, head, revision):
        return BehaviorDiff(revision, tuple(functions))

    monkeypatch.setattr(_proposal, "probe_behavior", probe)
    await h.run_agent(1, EDIT)
    await settled(cards)
    [job] = cards.jobs.values()
    facts = h.payloads(ChangeFacts)[-1]
    assert job.status == "finished"
    cards.validate(job, facts)
    return cards, client, job, facts


def problems(facts):
    return [
        p
        for p in facts.problems
        if p.startswith("review cards:") and not p.startswith("review cards: finished;")
    ]


@pytest.mark.anyio
async def test_twenty_six_value_differences_have_one_classified_witness(h, monkeypatch):
    function = await measure([(value(i), value(i + 26)) for i in range(26)])
    _, client, _, facts = await publish(h, monkeypatch, [function])
    [node] = facts.behavior_diff
    [card] = facts.review_cards
    assert node.differing_count == 26 and len(node.rows) == 8
    assert len(client.calls) == 1 and card.example in node.rows
    assert card.question == QUESTION + " Returns a different value, for 26 of 26 differing inputs."
    assert not problems(facts)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "choice,base", [("not_asked_for", QUESTION), ("cannot_tell", UNCERTAIN + ".")]
)
async def test_each_typed_outcome_kind_has_its_own_card(h, monkeypatch, choice, base):
    pairs = [
        (value(0), value(1)),
        (value(2), raised("ValueError")),
        (raised("TypeError"), value(3)),
        (raised("OSError"), raised("RuntimeError")),
    ]
    function = await measure(pairs)
    _, client, _, facts = await publish(h, monkeypatch, [function], choice=choice)
    suffixes = [
        "Returns a different value",
        "Now raises ValueError",
        "No longer raises TypeError",
        "Raises RuntimeError instead of OSError",
    ]
    assert len(client.calls) == len(facts.review_cards) == 4
    assert {c.question for c in facts.review_cards} == {
        base + f" {s}, for 1 of 4 differing inputs." for s in suffixes
    }
    assert not problems(facts)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "choice,question",
    [
        (
            "cannot_tell",
            "Oversight could not tell whether this was asked for. "
            "Now raises ValueError, for 1 of 4 differing inputs.",
        ),
        (
            "not_asked_for",
            "Was this behavior change asked for? Now raises ValueError, "
            "for 1 of 4 differing inputs.",
        ),
    ],
)
async def test_group_question_sentences_validate_and_round_trip(h, monkeypatch, choice, question):
    function = await measure(
        [(value(0), raised("ValueError")), *[(value(i), value(i + 4)) for i in range(1, 4)]]
    )
    cards, _, job, facts = await publish(h, monkeypatch, [function], choice=choice)
    current = facts.review_cards
    [card] = [c for c in current if "Now raises ValueError" in c.question]
    assert card.question == question
    cards.validate(job, facts)
    yes = [c.model_copy(update={"answer": "yes", "answered_by": "person"}) for c in current]
    assert validate_answers(current, list(reversed(yes))) == yes
    await h.manager.reject("change-1", None, answers=yes, **observed_target(h.manager, "change-1"))
    assert h.payloads(ChangeResolved)[0].answers == yes


@pytest.mark.anyio
async def test_value_and_raised_groups_cover_different_counts(h, monkeypatch):
    pairs = [(value(i), value(i + 30)) for i in range(14)]
    pairs += [(value(i), raised("ValueError")) for i in range(12)]
    _, client, _, facts = await publish(h, monkeypatch, [await measure(pairs)])
    assert len(client.calls) == len(facts.review_cards) == 2
    assert {c.question for c in facts.review_cards} == {
        QUESTION + " Returns a different value, for 14 of 26 differing inputs.",
        QUESTION + " Now raises ValueError, for 12 of 26 differing inputs.",
    }
    assert not problems(facts)


@pytest.mark.anyio
async def test_smallest_group_witness_beyond_first_eight_is_retained(h, monkeypatch):
    pairs = [(value(i), value(i + 26)) for i in range(26)]
    calls = [f"f(x={100 + i})" for i in range(25)] + ["f(x=0)"]
    function = await measure(pairs, calls=calls)
    _, _, _, facts = await publish(h, monkeypatch, [function])
    [card] = facts.review_cards
    assert card.example.inputs == "f(x=0)"
    assert [r.inputs for r in facts.behavior_diff[0].rows] == ["f(x=0)", *calls[:7]]
    assert not problems(facts)


@pytest.mark.anyio
async def test_nine_groups_across_nodes_share_eight_classifications(h, monkeypatch):
    function = await measure([(value(i), value(i + 26)) for i in range(26)])
    functions = [replace(function, node_id=f"m:f{i}") for i in range(9)]
    _, client, _, facts = await publish(h, monkeypatch, functions)
    assert len(client.calls) == len(facts.review_cards) == 8
    [problem] = problems(facts)
    assert "1 groups omitted by classification limit" in problem
    assert "26 differing inputs unclassified" in problem


@pytest.mark.anyio
@pytest.mark.parametrize("fault", ["absent", "unstable", "incomplete"])
async def test_group_requires_its_retained_complete_witness(h, monkeypatch, fault):
    function = await measure([(value(i), value(i + 26)) for i in range(26)])
    rows = function.rows
    if fault == "absent":
        rows = rows[1:]
    else:
        rows = (
            replace(
                rows[0],
                **({"unstable": True} if fault == "unstable" else {"repeat_complete": False}),
            ),
            *rows[1:],
        )
    _, client, _, facts = await publish(h, monkeypatch, [replace(function, rows=rows)])
    assert not client.calls and not facts.review_cards
    assert any("26" in p and "not retained" in p for p in problems(facts))


@pytest.mark.anyio
@pytest.mark.parametrize("repeat", ["unstable", "incomplete"])
async def test_nonreproducible_differences_never_join_groups(h, monkeypatch, repeat):
    function = await measure([(value(i), value(i + 26)) for i in range(26)], repeat=repeat)
    _, client, _, facts = await publish(h, monkeypatch, [function])
    assert function.differing == 0 and not client.calls and not facts.review_cards
    assert any("26" in p and "unstable or incomplete" in p for p in problems(facts))


@pytest.mark.anyio
async def test_group_questions_round_trip_answers_and_reject_forgery(h, monkeypatch):
    function = await measure([(value(0), value(1)), (value(2), raised("ValueError"))])
    cards, _, job, facts = await publish(h, monkeypatch, [function])
    current = facts.review_cards
    assert len(current) == 2 and len({c.question for c in current}) == 2
    yes = [c.model_copy(update={"answer": "yes", "answered_by": "person"}) for c in current]
    assert validate_answers(current, list(reversed(yes))) == yes
    for answers in (
        [yes[0], yes[0]],
        [yes[0].model_copy(update={"question": QUESTION})],
        [yes[0].model_copy(update={"question": yes[0].question.replace("1 of 2", "2 of 2")})],
    ):
        with pytest.raises(InvalidAnswers):
            validate_answers(current, answers)
    for changed in (
        [current[0], current[0]],
        [
            current[0],
            current[0].model_copy(
                update={"question": current[0].question.replace(QUESTION, UNCERTAIN)}
            ),
        ],
        [current[0].model_copy(update={"question": QUESTION})],
        [current[0].model_copy(update={"question": current[1].question})],
    ):
        with pytest.raises(ValueError):
            cards.validate(job, facts.model_copy(update={"review_cards": changed}))
    await h.manager.reject("change-1", None, answers=yes, **observed_target(h.manager, "change-1"))
    assert h.payloads(ChangeResolved)[0].answers == yes


@pytest.mark.anyio
async def test_uncovered_differences_stay_unavailable(h, monkeypatch):
    function = await measure([(value(i), value(i + 26)) for i in range(26)])
    _, _, _, facts = await publish(h, monkeypatch, [replace(function, differing=27, compared=27)])
    [problem] = problems(facts)
    assert "1 differing observations not retained" in problem
    assert "26 of 27 differing inputs" in facts.review_cards[0].question


@pytest.mark.anyio
async def test_group_metadata_is_private_and_detached():
    function = await measure([(value(0), raised("ValueError"))])
    captured = capture_behavior(BehaviorDiff(1, (function,)))
    public = project_behavior(captured, revision=1)
    assert public is not None
    assert set(public[0].model_dump()) == {
        "node_id",
        "compared_count",
        "differing_count",
        "rows",
        "not_probed",
    }
    function.rows[0].after["class"] = "FORGED"
    from test_measured_cone import TARGET

    source = FactsEvidence("agent-1", TARGET, None, (), (), (), {}, {}, behavior_evidence=captured)
    [sample] = selection(source, 8).samples
    assert sample.group is not None
    assert sample.group.after_exception == "ValueError"
    assert project_behavior(captured, revision=1) == public


@pytest.mark.anyio
async def test_unavailable_classification_keeps_full_group_count(h, monkeypatch):
    function = await measure([(value(i), value(i + 26)) for i in range(26)])
    _, client, _, facts = await publish(h, monkeypatch, [function], choice="missing")
    assert len(client.calls) == 1 and not facts.review_cards
    [problem] = problems(facts)
    assert "invalid_response" in problem and "26 differing inputs unclassified" in problem


@pytest.mark.anyio
@pytest.mark.parametrize(
    "choices,covered", [(("asked_for", "asked_for"), 26), (("asked_for", "not_asked_for"), 14)]
)
async def test_asked_for_summary_counts_only_classified_groups(h, monkeypatch, choices, covered):
    function = await measure(
        [(value(i), value(i + 30)) for i in range(14)]
        + [(value(i), raised("ValueError")) for i in range(12)]
    )
    _, client, job, facts = await publish(h, monkeypatch, [function], choice=choices)
    assert len(client.calls) == 2 and len(job.asked_for) == 1
    assert job.asked_for[0].differing_count == covered
    assert f"sample:f ({covered} of 26 inputs differ)" in facts.summary[2]
    assert not problems(facts)


@pytest.mark.anyio
async def test_exception_names_split_groups_but_messages_do_not(h, monkeypatch):
    pairs = [
        (value(i), raised(name, message))
        for i, (name, message) in enumerate(
            [("ValueError", "one"), ("ValueError", "two"), ("TypeError", "one")]
        )
    ]
    _, _, _, facts = await publish(h, monkeypatch, [await measure(pairs)])
    assert {c.question for c in facts.review_cards} == {
        QUESTION + " Now raises ValueError, for 2 of 3 differing inputs.",
        QUESTION + " Now raises TypeError, for 1 of 3 differing inputs.",
    }
    assert not problems(facts)


@pytest.mark.anyio
async def test_grouping_uses_typed_kind_despite_exception_words_in_values(h, monkeypatch):
    text = {"kind": "value", "value": ["str", "Now raises ValueError"], "fingerprint": "text"}
    pairs = [(value(0), text), (value(1), raised("ValueError"))]
    _, client, _, facts = await publish(h, monkeypatch, [await measure(pairs)])
    assert len(client.calls) == 2
    assert {c.question for c in facts.review_cards} == {
        QUESTION + " Returns a different value, for 1 of 2 differing inputs.",
        QUESTION + " Now raises ValueError, for 1 of 2 differing inputs.",
    }
    assert not problems(facts)
