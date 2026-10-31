"""Admission uses confirmed run events, not merely a contract-shaped reference."""

from typing import Any

import pytest
from pydantic import TypeAdapter

from oversight.agents import AnsweringError, validate_answering
from oversight.events import read_jsonl
from oversight.settings import REPO_ROOT
from oversight_contracts.commands import StartAgent
from oversight_contracts.events import ServerEvent

ADAPTER = TypeAdapter(ServerEvent)
RUN = read_jsonl(REPO_ROOT / "fixtures/events/handwritten-level1.jsonl")[0]
assert RUN.type == "run.started"
RUN_ID = RUN.payload.run_id
TARGET = dict(
    change_id="change-90",
    revision=0,
    base_commit="base",
    head_commit="base",
    main_commit="base",
    candidate_tree="tree",
)
STATUS = dict(
    agent_id="agent-90",
    change_id="change-90",
    slot="A",
    task="t",
    model="fake",
    state="halted",
    step=0,
    max_steps=30,
    detail=None,
    base_commit="base",
)
FACTS = dict(
    **TARGET,
    declared=None,
    edited_paths=[],
    test_runs=[],
    integrity=[],
    added_tests=[],
    run_findings=[],
    zone=None,
    reach=None,
    complete=True,
    problems=[],
    behavior_diff=[
        dict(
            node_id="m:f",
            compared_count=1,
            differing_count=1,
            rows=[dict(inputs="x=1", before="int:1", after="int:2")],
        )
    ],
)


def event(seq: int, kind: str, payload: dict[str, Any]) -> ServerEvent:
    return ADAPTER.validate_python(dict(seq=seq, ts=RUN.ts, type=kind, payload=payload))


def log(*extra: tuple[str, dict[str, Any]]) -> list[ServerEvent]:
    return [
        RUN,
        event(2, "agent.status", STATUS),
        *[event(i, kind, payload) for i, (kind, payload) in enumerate(extra, 3)],
    ]


def notice(code: str = "policy_invalid", **changes: Any) -> dict[str, Any]:
    return dict(
        source="server",
        agent_id="agent-90",
        change_id="change-90",
        fatal=False,
        message=f"Needs you — revision=0; head=base; code={code}; reason=Unavailable.",
        **changes,
    )


def reference(kind: str = "checks_unavailable", **changes: Any) -> Any:
    data = dict(kind=kind, change_id="change-90", revision=0, head_commit="base", evidence_seq=3)
    if kind == "review_card":
        data.update(node_id="m:f", question="Keep this?")
    if kind == "lead_plan":
        data = dict(kind=kind, agent_id="agent-90", revision=1, evidence_seq=3)
    data.update(changes)
    return StartAgent.model_validate(dict(task="t", answering=data)).answering


def validate(ref: Any, events: list[ServerEvent]) -> None:
    validate_answering(ref, events, run_id=RUN_ID)


@pytest.mark.parametrize(
    "code",
    [
        "policy_invalid",
        "reviewer_missing",
        "reviewer_same",
        "reviewer_credentials",
        "author_halted",
        "review_timeout",
        "review_invalid",
        "review_truncated",
        "review_empty",
        "review_api_failed",
        "isolation_unavailable",
    ],
)
def test_terminal_unavailable_checks_are_one_entry(code: str) -> None:
    events = log(("error", notice(code)), ("error", notice("review_timeout")))
    validate(reference(), events)
    with pytest.raises(AnsweringError, match="identity"):
        validate(reference(evidence_seq=4), events)


@pytest.mark.parametrize(
    "code",
    [
        "suite_failed",
        "git_failed",
        "main_moved",
        "integrity_invalid",
        "cancelled",
        "ledger",
        "round_exhausted",
        "spend_invalid",
        "target_changed",
        "main_suspect",
        "worktree_unverifiable",
        "unproposed_edits",
        "delivery_failed",
        "unchanged_failure",
        "reach_unknown",
    ],
)
def test_other_terminal_errors_do_not_establish_check_unavailability(code: str) -> None:
    with pytest.raises(AnsweringError):
        validate(reference(), log(("error", notice(code))))


@pytest.mark.parametrize("kind", ["follow_main", "risky_area", "lead_plan", "examples_before_code"])
def test_absent_producers_are_explicit(kind: str) -> None:
    with pytest.raises(AnsweringError, match="producer unavailable") as caught:
        validate(reference(kind), log(("error", notice())))
    assert caught.value.code == "wrong_state"


@pytest.mark.parametrize(
    "field,value",
    [
        ("evidence_seq", 99),
        ("evidence_seq", 2),
        ("change_id", "change-91"),
        ("head_commit", "other"),
        ("revision", 1),
    ],
)
def test_reference_must_identify_the_exact_entry(field: str, value: Any) -> None:
    with pytest.raises(AnsweringError) as caught:
        validate(reference(**{field: value}), log(("error", notice())))
    assert caught.value.code == "invalid_request"


def test_another_run_is_invalid() -> None:
    with pytest.raises(AnsweringError, match="run"):
        validate_answering(reference(), log(("error", notice())), run_id="another-run")


def card_log(*, cards: list[dict[str, Any]] | None = None) -> list[ServerEvent]:
    card = dict(node_id="m:f", question="Keep this?", evidence_seq=3)
    return log(
        ("change.facts_recorded", FACTS),
        ("change.facts_recorded", dict(FACTS, review_cards=cards or [card])),
    )


def test_card_retains_measurement_seq_and_requires_corresponding_facts() -> None:
    validate(reference("review_card"), card_log())
    with pytest.raises(AnsweringError):
        validate(reference("review_card", evidence_seq=4), card_log())
    events = card_log()
    events[2] = event(3, "error", notice())
    with pytest.raises(AnsweringError):
        validate(reference("review_card"), events)


@pytest.mark.parametrize("field,value", [("node_id", "other"), ("question", "Other?")])
def test_card_identity_is_exact(field: str, value: str) -> None:
    with pytest.raises(AnsweringError):
        validate(reference("review_card", **{field: value}), card_log())


def test_duplicate_cards_are_ambiguous() -> None:
    card = dict(node_id="m:f", question="Keep this?", evidence_seq=3)
    with pytest.raises(AnsweringError, match="unique"):
        validate(reference("review_card"), card_log(cards=[card, card]))


def test_card_replacement_and_resolution_close_the_entry() -> None:
    events = card_log()
    events.append(event(5, "change.facts_recorded", dict(FACTS, review_cards=[])))
    with pytest.raises(AnsweringError) as caught:
        validate(reference("review_card"), events)
    assert caught.value.code == "wrong_state"
    events = log(("error", notice()))
    events.append(
        event(
            4,
            "change.resolved",
            dict(
                change_id="change-90",
                agent_id="agent-90",
                revision=0,
                head_commit="base",
                decision="rejected",
                decided_by="person",
                comment=None,
                merge=None,
                conflict=None,
                reason=None,
                merge_commit=None,
            ),
        )
    )
    with pytest.raises(AnsweringError) as caught:
        validate(reference(), events)
    assert caught.value.code == "wrong_state"


def test_revision_zero_needs_a_registered_base() -> None:
    events = log(("error", notice()))
    events[1] = event(2, "agent.status", dict(STATUS, base_commit="other"))
    with pytest.raises(AnsweringError):
        validate(reference(), events)


def proposed(revision: int, head: str) -> dict[str, Any]:
    source = next(
        e
        for e in read_jsonl(REPO_ROOT / "fixtures/events/handwritten-level1.jsonl")
        if e.type == "change.proposed"
    )
    return source.payload.model_dump() | dict(
        change_id="change-90",
        agent_id="agent-90",
        revision=revision,
        base_commit="base",
        head_commit=head,
    )


def test_proposal_replacement_requires_the_current_candidate() -> None:
    events = log(("error", notice()), ("change.proposed", proposed(1, "head-1")))
    with pytest.raises(AnsweringError) as caught:
        validate(reference(), events)
    assert caught.value.code == "wrong_state"
    events.append(
        event(
            5,
            "error",
            dict(
                notice(),
                message=(
                    "Needs you — revision=1; head=head-1; code=reviewer_missing; "
                    "reason=Unavailable."
                ),
            ),
        )
    )
    current = reference(revision=1, head_commit="head-1", evidence_seq=5)
    validate(current, events)
    events.append(event(6, "change.proposed", proposed(2, "head-2")))
    with pytest.raises(AnsweringError) as caught:
        validate(current, events)
    assert caught.value.code == "wrong_state"


@pytest.mark.parametrize(
    "updates",
    [
        dict(change_id="change-91"),
        dict(agent_id="agent-91"),
        dict(source="git"),
        dict(message="unknown check"),
        dict(message="A suite failed"),
    ],
)
def test_notice_provenance_is_not_inferred_from_free_text(updates: dict[str, Any]) -> None:
    with pytest.raises(AnsweringError):
        validate(reference(), log(("error", dict(notice(), **updates))))


@pytest.mark.parametrize(
    "updates",
    [
        dict(revision=1),
        dict(change_id="change-91"),
        dict(head_commit="different", base_commit="different"),
        dict(main_commit="different"),
        dict(candidate_tree="different"),
        dict(behavior_diff=[]),
    ],
)
def test_card_measurement_must_match_all_candidate_facts(updates: dict[str, Any]) -> None:
    events = card_log()
    events[2] = event(3, "change.facts_recorded", dict(FACTS, **updates))
    with pytest.raises(AnsweringError):
        validate(reference("review_card"), events)


def test_answered_card_and_post_resolution_status_cannot_reopen_entry() -> None:
    events = card_log(
        cards=[
            dict(
                node_id="m:f",
                question="Keep this?",
                evidence_seq=3,
                answer="yes",
                answered_by="person",
            )
        ]
    )
    with pytest.raises(AnsweringError):
        validate(reference("review_card"), events)
    from answering_helpers import resolution

    events = log(
        ("error", notice()),
        ("change.resolved", resolution()),
        ("agent.status", STATUS),
        ("error", notice()),
    )
    with pytest.raises(AnsweringError) as caught:
        validate(reference(evidence_seq=6), events)
    assert caught.value.code == "invalid_request"


def test_invalid_duplicate_card_cannot_hide_ambiguity() -> None:
    cards = [
        dict(node_id="m:f", question="Keep this?", evidence_seq=3),
        dict(node_id="m:f", question="Keep this?", evidence_seq=99),
    ]
    with pytest.raises(AnsweringError, match="unique"):
        validate(reference("review_card"), card_log(cards=cards))


def test_transient_unknown_checks_and_failed_suite_are_not_unavailability() -> None:
    thresholds = {
        "combined_tests": dict(outcome="passed", passed_min=1, failed_max=0, skipped_max=0),
        "small_cone": dict(
            nodes_max=5,
            omitted_by_depth_max=0,
            omitted_by_limit_max=0,
            parse_errors_max=0,
            unresolved_removed_max=0,
        ),
        "small_change": dict(files_min=1, files_max=3, lines_total_max=60),
        "known_spend": dict(budget_fraction_lt=0.5, unknown_costs_max=0, ledger_ok=True),
        "existing_tests_untouched": dict(modified_existing_max=0, deleted_existing_max=0),
    }
    assessment = dict(
        change_id="change-90",
        agent_id="agent-90",
        revision=1,
        base_commit="base",
        head_commit="head-1",
        main_commit="base",
        policy_version="safe-merge-v1",
        mode="merge_when_safe",
        mode_revision=0,
        rules=[
            dict(rule=key, threshold=value, result="unknown", evidence=[])
            for key, value in thresholds.items()
        ],
    )
    events = log(("change.proposed", proposed(1, "head-1")), ("change.assessed", assessment))
    with pytest.raises(AnsweringError):
        validate(reference(revision=1, head_commit="head-1", evidence_seq=4), events)
    failed = proposed(1, "head-1")
    failed["tests"].update(
        outcome="failed", passed=0, failed=1, failed_tests=["test_example.py::test_x"]
    )
    with pytest.raises(AnsweringError):
        validate(reference(revision=1, head_commit="head-1"), log(("change.proposed", failed)))
