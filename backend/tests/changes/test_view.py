"""Archived views from the committed scripted level-2 run, with no workspace reads."""

from pathlib import Path

import pytest
from pydantic import TypeAdapter
from test_policy_profiles import policy_inputs

from oversight.changes import ChangeNotFound, build_change_view
from oversight.changes._policy import assess
from oversight.events import EVENT_TYPES, EventPayload, fold_stages, fold_usage, read_jsonl
from oversight_contracts.events import ServerEvent
from oversight_contracts.policy import ChangeAssessment
from oversight_contracts.review import ChangeFacts, ChangeTarget, CrossReview

ADAPTER = TypeAdapter(ServerEvent)
RECORDING = Path(__file__).resolve().parents[3] / "fixtures/events/scripted-level2.jsonl"


@pytest.fixture
def events() -> list[ServerEvent]:
    return read_jsonl(RECORDING)


def append(events: list[ServerEvent], payload: EventPayload) -> list[ServerEvent]:
    envelope = ADAPTER.validate_python(
        {
            "seq": events[-1].seq + 1,
            "ts": events[-1].ts,
            "type": EVENT_TYPES[type(payload)],
            "payload": payload.model_dump(),
        }
    )
    return [*events, envelope]


def evidence(events: list[ServerEvent]):
    view = build_change_view("change-2", events)
    facts = view.facts
    assert facts is not None
    target = facts.model_dump(include=set(ChangeTarget.model_fields))
    review = CrossReview.model_validate(
        target
        | {
            "author_model": "author",
            "reviewer_model": "reviewer",
            "reviewer_provider": "fake",
            "reasoning": None,
            "verdict": "clean",
            "findings": [],
            "complete": True,
            "problems": [],
        }
    )
    assessment = ChangeAssessment.model_validate(
        assess(policy_inputs(), "safe-merge-v1").model_dump() | target | {"agent_id": "agent-2"}
    )
    return facts, review, assessment


def test_recorded_proposal_revision_and_resolution_keep_one_event_horizon(events):
    proposed = build_change_view("change-2", events[:53])
    assert proposed.proposal is not None
    assert proposed.seq == 53 and proposed.proposal.revision == 1
    assert proposed.facts is not None and proposed.resolution is None
    requested = build_change_view("change-2", events[:55])
    assert requested.resolution is not None
    assert requested.resolution.decision == "changes_requested"
    assert requested.revision_rounds == 1 and requested.facts is None
    revised = build_change_view("change-2", events[:76])
    assert revised.proposal is not None and revised.facts is not None
    assert revised.proposal.revision == 2 and revised.facts.revision == 2
    assert revised.revision_rounds == 1
    final = build_change_view("change-2", events)
    assert final.proposal is not None and final.facts is not None and final.resolution is not None
    assert final.seq == events[-1].seq
    assert final.proposal.revision == 3 and final.facts.revision == 3
    assert final.revision_rounds == 1  # A person's edit also increments the proposal revision.
    assert final.resolution.decision == "approved" and final.resolution.merge == "merged"
    assert final.usage == fold_usage(events).by_change["change-2"].usage
    latest = {
        event.payload.file.path: event.payload.file
        for event in events
        if event.type == "change.updated" and event.payload.change_id == "change-2"
    }
    assert {file.path: file for file in final.files} == latest
    assert all(file.original and file.modified for file in final.files)


@pytest.mark.parametrize("partial", ["suffix", "gap"])
def test_incomplete_history_never_invents_rounds_or_costs(events, partial):
    inventory = events[50:] if partial == "suffix" else [*events[:10], *events[11:]]
    view = build_change_view("change-2", inventory)
    assert view.proposal is not None and view.resolution is not None
    assert view.proposal.revision == 3 and view.resolution.decision == "approved"
    assert view.usage is None and view.revision_rounds is None
    assert view.stages is None
    assert view.seq == events[-1].seq


@pytest.mark.parametrize("cost", [None, 0.0])
def test_unknown_cost_is_unknown_and_a_recorded_zero_is_preserved(events, cost):
    inventory = [
        event.model_copy(update={"payload": event.payload.model_copy(update={"cost_usd": cost})})
        if event.type == "usage.tick" and event.payload.change_id == "change-2"
        else event
        for event in events
    ]
    view = build_change_view("change-2", inventory)
    assert view.usage is not None and view.usage.by_role is not None
    assert view.usage.cost_usd == cost
    assert view.usage.unknown_calls == sum(
        e.type == "usage.tick" and e.payload.change_id == "change-2" and cost is None
        for e in events
    )
    assert all(bucket.cost_usd == cost for bucket in view.usage.by_role)


@pytest.mark.parametrize("kind", ["absent", "unassigned", "missing_model", "invalid_cost"])
def test_unavailable_usage_is_never_a_fabricated_zero(events, kind):
    inventory = []
    for event in events:
        if event.type == "usage.tick":
            update = {
                "absent": {"change_id": "change-99"},
                "unassigned": {"change_id": None},
                "missing_model": {"model": None},
                "invalid_cost": {"cost_usd": -1},
            }[kind]
            event = event.model_copy(update={"payload": event.payload.model_copy(update=update)})
        inventory.append(event)
    view = build_change_view("change-2", inventory)
    if kind == "missing_model":
        assert view.usage is not None
        assert view.usage.cost_usd is None
    else:
        assert view.usage is None
    assert view.revision_rounds == 1


@pytest.mark.parametrize("identifier", ["change-99", "not-an-id", ""])
def test_unknown_change_ids_raise_not_found(events, identifier):
    with pytest.raises(ChangeNotFound):
        build_change_view(identifier, events)


def test_matching_evidence_survives_merge_and_later_main_commits(events):
    facts, review, assessment = evidence(events)
    inventory = append(append(events, review), assessment)
    view = build_change_view("change-2", inventory)
    assert (view.facts, view.review, view.assessment) == (facts, review, assessment)


@pytest.mark.parametrize(
    "field,value",
    [
        ("change_id", "change-99"),
        ("revision", 2),
        ("base_commit", "other-base"),
        ("head_commit", "other-head"),
        ("main_commit", "other-main"),
        ("candidate_tree", "other-tree"),
    ],
)
@pytest.mark.parametrize("kind", ["review", "assessment"])
def test_delayed_foreign_evidence_cannot_replace_current_target(events, field, value, kind):
    facts, review, assessment = evidence(events)
    payload = review if kind == "review" else assessment
    payload = type(payload).model_validate(payload.model_dump() | {field: value})
    view = build_change_view("change-2", append(events, payload))
    assert view.facts == facts
    assert view.review is None and view.assessment is None


def test_current_facts_replace_old_tree_evidence_without_changing_proposal(events):
    events = events[:83]
    facts, review, assessment = evidence(events)
    inventory = append(append(events, review), assessment)
    replacement = ChangeFacts.model_validate(facts.model_dump() | {"candidate_tree": "new-tree"})
    view = build_change_view("change-2", append(inventory, replacement))
    assert view.facts == replacement and view.review is None and view.assessment is None


def test_combined_candidate_assessment_supersedes_head_tree_evidence(events):
    events = events[:83]
    _, review, assessment = evidence(events)
    combined = ChangeAssessment.model_validate(
        assessment.model_dump() | {"candidate_tree": "combined-tree"}
    )
    inventory = append(append(events, review), combined)
    view = build_change_view("change-2", inventory)
    assert view.assessment == combined and view.facts is None and view.review is None


def test_terminal_resolution_retains_its_final_assessment(events):
    prefix = events[:83]
    _, _, assessment = evidence(prefix)
    combined = ChangeAssessment.model_validate(
        assessment.model_dump() | {"candidate_tree": "combined-tree"}
    )
    resolved = next(
        e.payload
        for e in events
        if e.type == "change.resolved"
        and e.payload.change_id == "change-2"
        and e.payload.decision == "approved"
    )
    inventory = append(prefix, resolved.model_copy(update={"assessment": combined}))
    view = build_change_view("change-2", inventory)
    assert view.assessment == combined and view.facts is None


@pytest.mark.parametrize("through", [55, 58, 78])
def test_request_changes_or_edit_invalidates_even_late_old_facts(events, through):
    old_facts = next(
        event.payload
        for event in events
        if event.type == "change.facts_recorded" and event.payload.change_id == "change-2"
    )
    view = build_change_view("change-2", append(events[:through], old_facts))
    assert view.facts is None and view.review is None and view.assessment is None


def test_main_advancing_invalidates_open_candidate_but_keeps_archive(events):
    graph = next(e for e in reversed(events) if e.type == "graph.delta")
    open_events = append(events[:83], graph.payload)
    view = build_change_view("change-2", open_events)
    assert view.resolution is not None
    assert view.resolution.decision == "changes_requested"
    assert view.facts is None
    assert build_change_view("change-2", events).facts is not None


def test_usage_and_error_receipts_alone_do_not_establish_a_change(events):
    ticks = [e for e in events if e.type == "usage.tick" and e.payload.change_id == "change-2"]
    with pytest.raises(ChangeNotFound):
        build_change_view("change-2", ticks)


def test_stages_match_fold_and_expected_recording(events):
    stages = build_change_view("change-2", events).stages
    assert stages is not None and stages == fold_stages(events, "change-2")
    assert stages.agent_work.model_dump() == dict(ms=13925, known_ms=13925, intervals=2, open=False)
    assert stages.checks.model_dump() == dict(ms=2045, known_ms=2045, intervals=3, open=False)
    assert stages.waiting_for_person.model_dump() == dict(
        ms=None, known_ms=2577, intervals=3, open=False
    )
    assert stages.test_runs.model_dump() == dict(ms=3044, known_ms=3044, intervals=11, open=False)


def test_every_known_change_prefix_builds_without_raising(events):
    for n in range(1, len(events) + 1):
        known = {e.payload.change_id for e in events[:n] if e.type == "agent.status"}
        for change in known:
            view = build_change_view(change, events[:n])
            assert view.seq == n and view.stages == fold_stages(events[:n], change)
