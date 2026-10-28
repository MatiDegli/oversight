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
