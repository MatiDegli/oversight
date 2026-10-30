"""Selection requires private row witnesses and orders only actually observed examples."""

from dataclasses import replace
from typing import Any

import pytest
from test_measured_cone import TARGET, evidence, invocation, suite

from oversight.changes._behavior import BehaviorDiff, BehaviorRow, FunctionBehavior
from oversight.changes._behavior_publish import capture_behavior
from oversight.changes._measured import capture_review_samples, project_measured
from oversight.changes._review_samples import Observation, select


def sample(node="m:f", inputs="x", before="old", after="new", **kwargs):
    values = inputs, before, after
    return Observation(node, values, kwargs.pop("identity", values), **kwargs)


def test_minimum_dedup_and_other_differences_are_explicit():
    small, big = sample(inputs="a"), sample(inputs="long")
    result = select((big, small, small), 8)
    assert result.samples == (small,)
    assert result.problems == ("review cards: 1 other differences unclassified",)


def test_utf8_total_length_lexical_and_node_ties():
    values = (
        sample("z", "é"),
        sample("a", "aa"),
        sample("b", "a", "longer"),
        sample("d", "a"),
        sample("c", "a"),
    )
    assert [s.node_id for s in select(values, 8).samples] == ["c", "d", "b", "a", "z"]
    assert select(values, 8) == select(tuple(reversed(values)), 8)


@pytest.mark.parametrize("limit", [0, 1, 3, 8])
def test_nine_nodes_share_one_limit(limit):
    result = select(tuple(sample(f"m:f{i}") for i in range(9)), limit)
    assert len(result.samples) == limit
    assert f"{9 - limit} nodes omitted" in result.problems[0]


def test_truncated_text_does_not_merge_distinct_identities():
    first = sample(identity=("x", "hash-1", "hash-2"))
    second = sample(identity=("x", "hash-1", "hash-3"))
    result = select((first, second), 8)
    assert len(result.samples) == 1
    assert "1 other differences" in result.problems[0]


def direct_row(*, repeated=False, equal_inputs=True, unstable=False, call="f(x=1)"):
    return BehaviorRow(
        call,
        {"kind": "value", "value": ["int", "0x1"], "fingerprint": "1" * 64},
        {"kind": "value", "value": ["int", "0x2"], "fingerprint": "2" * 64},
        unstable,
        repeated,
        equal_inputs,
    )


def captured(*rows):
    result = capture_behavior(BehaviorDiff(1, (FunctionBehavior("m:f", 2, 1, rows),)))
    assert result is not None
    return tuple(row.observation for row in result.functions[0].rows)


@pytest.mark.parametrize(
    "row",
    [
        direct_row(),
        direct_row(repeated=True, equal_inputs=False),
        direct_row(repeated=True, unstable=True),
        direct_row(repeated=True, call="x" * 1025),
    ],
)
def test_unstable_false_or_counts_do_not_certify_a_particular_row(row):
    [observed] = captured(row)
    assert observed is not None and observed.excluded
    assert not select((observed,), 8).samples


def test_mixed_direct_rows_retain_independent_completion_witness():
    missing, stable = captured(direct_row(), direct_row(repeated=True))
    assert missing is not None and stable is not None
    assert select((missing, stable), 8).samples == (stable,)


def test_missing_typed_fingerprint_cannot_generate_question():
    row = direct_row(repeated=True)
    row.after.pop("fingerprint")
    [observed] = captured(row)
    assert observed is not None and observed.excluded == "typed identity unavailable"


def test_callers_require_four_complete_reports_and_retained_typed_row():
    old, new = [invocation()], [invocation('["value",["int","0x2"]]')]
    partial = evidence(old, new)
    assert not capture_review_samples(partial)
    measured = evidence(old, new, suite("base", old), suite("head", new))
    [observed] = capture_review_samples(measured)
    assert observed.excluded is None
    assert observed.example() == project_measured(measured, TARGET)[0].rows[0]
    incomplete = replace(
        measured, suites=(*measured.suites[:3], suite("head", new, complete=False))
    )
    assert not select(capture_review_samples(incomplete), 8).samples
    assert not capture_review_samples(evidence(old, old))


def test_caller_truncation_keeps_full_identity_but_excludes_sample():
    old = [invocation(inputs="x" * 2049)]
    new = [invocation('["value",["int","0x2"]]', inputs="x" * 2049)]
    measured = evidence(old, new, suite("base", old), suite("head", new))
    [observed] = capture_review_samples(measured)
    assert observed.excluded == "display sample truncated"
    assert len(observed.identity[0]) == 2049


def test_identical_typed_observation_deduplicates_across_real_source_adapters():
    from oversight.changes._review_identity import bound_inputs

    inputs = '["dict",[[["str","x"],["int","0x1"]]]]'
    old, new = [invocation(inputs=inputs)], [invocation('["value",["int","0x2"]]', inputs=inputs)]
    measured = evidence(old, new, suite("base", old), suite("head", new))
    [caller] = capture_review_samples(measured)
    [direct] = captured(replace(direct_row(repeated=True), input_identity=bound_inputs({"x": 1})))
    assert direct is not None and direct.identity == caller.identity
    caller = replace(caller, node_id=direct.node_id)
    result = select((direct, caller), 8)
    assert len(result.samples) == 1 and not result.problems


def test_omitted_default_changes_do_not_certify_equal_typed_inputs():
    from oversight.changes import behavior_inputs

    old = "def f(x=1): return x\n"
    new = "def f(x=2): return x\n"
    tests = "from pkg.mod import f\nf()\n"
    result = behavior_inputs(
        "pkg.mod",
        "f",
        {"pkg/mod.py": old, "test.py": tests},
        {"pkg/mod.py": new, "test.py": tests},
    )
    omitted = next(item for item in result.inputs if item.call == "f()")
    assert omitted.before == omitted.after and omitted.input_identity is None
    assert all(item.input_identity is not None for item in result.inputs if item.call != "f()")


def test_worker_truncation_marker_cannot_certify_a_display_sample():
    row = direct_row(repeated=True)
    row.after["value"] = ["str", "prefix", 5000, "cut"]
    [observed] = captured(row)
    assert observed is not None and observed.excluded == "typed display incomplete"


def test_review_card_settings_load_and_validate(tmp_path, monkeypatch):
    from oversight.settings import Settings

    monkeypatch.setenv("OVERSIGHT_REVIEW_CARD_CLASSIFICATIONS", "3")
    monkeypatch.setenv("OVERSIGHT_REVIEW_CARD_CALL_S", "2.5")
    monkeypatch.setenv("OVERSIGHT_REVIEW_CARD_TOTAL_S", "7")
    settings = Settings.load(env_file=tmp_path / "missing")
    assert (
        settings.review_card_classifications,
        settings.review_card_call_s,
        settings.review_card_total_s,
    ) == (3, 2.5, 7)
    invalid: tuple[dict[str, Any], ...] = (
        {"review_card_classifications": -1},
        {"review_card_call_s": 0},
        {"review_card_total_s": float("nan")},
    )
    for kwargs in invalid:
        with pytest.raises(ValueError):
            Settings(**kwargs)


def test_unstable_display_rows_do_not_hide_unretained_stable_differences():
    from oversight.changes._facts import FactsEvidence
    from oversight.changes._review_cards import selection

    behavior = capture_behavior(
        BehaviorDiff(
            1,
            (
                FunctionBehavior(
                    "m:f", 9, 1, tuple(direct_row(repeated=True, unstable=True) for _ in range(8))
                ),
            ),
        )
    )
    source = FactsEvidence("agent-1", TARGET, None, (), (), (), {}, {}, behavior_evidence=behavior)
    result = selection(source, 8)
    assert not result.samples and any("not retained" in problem for problem in result.problems)


def test_answers_reject_duplicated_server_identity():
    from oversight.changes._answers import InvalidAnswers, validate_answers
    from oversight_contracts.review import ReviewCard

    card = ReviewCard(node_id="m:f", question="server question", evidence_seq=1)
    answer = card.model_copy(update={"answer": "yes", "answered_by": "person"})
    with pytest.raises(InvalidAnswers, match="duplicated"):
        validate_answers([card, card], [answer])
