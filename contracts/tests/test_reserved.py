"""Contract 1.3 reserves wire formats and invariants without enabling level 3."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from oversight_contracts import CONTRACT_VERSION
from oversight_contracts.commands import (
    CreatePlan,
    PlanCreated,
    PlanRevision,
    RejectPlan,
    SetMergeMode,
    UpdatePlan,
)
from oversight_contracts.common import PlanId
from oversight_contracts.events import ChangeResolved, ServerEvent
from oversight_contracts.plans import Plan
from oversight_contracts.policy import ChangeAssessment, EvidenceRef, MergeModeState, RuleAssessment

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = json.loads((ROOT / "fixtures/contracts/reserved-1.3.json").read_text())
EVENTS = TypeAdapter(ServerEvent)


def assessment(**updates: Any) -> dict[str, Any]:
    return {**copy.deepcopy(EXAMPLES[0]["payload"]), **updates}


def plan(**updates: Any) -> dict[str, Any]:
    return {**copy.deepcopy(EXAMPLES[2]["payload"]), **updates}


def resolution(**updates: Any) -> dict[str, Any]:
    return {
        "change_id": "change-1",
        "agent_id": "agent-1",
        "revision": 1,
        "decision": "approved",
        "reason": None,
        "comment": None,
        "merge": "merged",
        "merge_commit": "merged",
        "conflict": None,
        "decided_by": "rules",
        "reasons": ["All five rules passed"],
        "assessment": assessment(),
        **updates,
    }


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda e: e["type"])
def test_reserved_events_round_trip_with_required_type_and_serialized_keys(example: Any) -> None:
    event = EVENTS.validate_python(example)
    assert json.loads(EVENTS.dump_json(event)) == example
    assert EVENTS.validate_json(EVENTS.dump_json(event)) == event
    with pytest.raises(ValidationError):
        EVENTS.validate_python({k: v for k, v in example.items() if k != "type"})
    with pytest.raises(ValidationError):
        EVENTS.validate_python({**example, "payload": {**example["payload"], "extra": True}})


@pytest.mark.parametrize(
    "log", sorted((ROOT / "fixtures/events").glob("*.jsonl")), ids=lambda p: p.name
)
def test_all_older_recordings_validate_and_get_serialized_defaults(log: Path) -> None:
    defaults = {
        "run.started": {"merge_mode": None},
        "agent.status": {"plan_id": None},
        "change.resolved": {"decided_by": None, "reasons": [], "assessment": None},
    }
    for line in log.read_text().splitlines():
        raw = json.loads(line)
        event = EVENTS.validate_json(line)
        dumped = json.loads(EVENTS.dump_json(event))
        assert dumped == {
            **raw,
            "payload": {**defaults.get(raw["type"], {}), **raw["payload"]},
        }


def test_defaults_and_request_response_shapes() -> None:
    assert CONTRACT_VERSION == "1.3.0"
    assert MergeModeState().model_dump() == {"mode": "ask_me", "revision": 0}
    assert SetMergeMode(mode="ask_me", revision=0).model_dump() == {"mode": "ask_me", "revision": 0}
    assert CreatePlan(task="Split this").task == "Split this"
    assert PlanCreated(plan_id="plan-1").plan_id == "plan-1"
    assert RejectPlan(revision=1).model_dump() == {"revision": 1, "comment": None}
    assert PlanRevision(revision=1).revision == 1
    assert Plan.model_validate(plan(state="planning", subtasks=None)).subtasks is None
    minimal = {key: plan()[key] for key in ("plan_id", "revision", "task", "graph_version")}
    assert Plan.model_validate(minimal).model_dump() == plan(state="planning", subtasks=None)
    subtasks = [{k: v for k, v in task.items() if k != "agent_id"} for task in plan()["subtasks"]]
    request = UpdatePlan.model_validate(
        {"revision": 1, "graph_version": "v1", "subtasks": subtasks}
    )
    assert len(request.subtasks) == 2
    with pytest.raises(ValidationError):
        UpdatePlan.model_validate({"revision": 1, "graph_version": "v1", "subtasks": subtasks[:1]})


@pytest.mark.parametrize("bad", ["plan-0", "plan-01", "plan-x", "", "agent-1"])
def test_plan_ids_are_positive_counters(bad: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(PlanId).validate_python(bad)


@pytest.mark.parametrize(
    "model,data",
    [
        (CreatePlan, {"task": ""}),
        (SetMergeMode, {"mode": "auto", "revision": 0}),
        (SetMergeMode, {"mode": "ask_me", "revision": -1}),
        (PlanRevision, {"revision": 0}),
        (RejectPlan, {"revision": -1}),
        (Plan, plan(graph_version="")),
        (Plan, plan(revision=0)),
        (ChangeAssessment, assessment(base_commit="")),
        (ChangeAssessment, assessment(head_commit="")),
        (ChangeAssessment, assessment(main_commit="")),
        (ChangeAssessment, assessment(policy_version="")),
        (ChangeAssessment, assessment(revision=0)),
        (ChangeAssessment, assessment(mode_revision=-1)),
    ],
)
def test_invalid_requests_and_assessment_identifiers_are_rejected(model: Any, data: Any) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(data)


@pytest.mark.parametrize("data", [{}, {"path": "shop.py"}, {"event_seq": 0}, {"git_object": ""}])
def test_evidence_requires_a_positive_event_or_nonempty_git_object(data: Any) -> None:
    with pytest.raises(ValidationError):
        EvidenceRef.model_validate(data)


@pytest.mark.parametrize(
    "data",
    [
        {"event_seq": 1},
        {"git_object": "head"},
        {"event_seq": 1, "git_object": "head", "path": "shop.py"},
    ],
)
def test_evidence_can_name_either_or_both_sources(data: Any) -> None:
    EvidenceRef.model_validate(data)


@pytest.mark.parametrize("index", range(5))
@pytest.mark.parametrize("field", ["observed", "threshold"])
@pytest.mark.parametrize("mutation", ["missing", "extra", "wrong_type"])
def test_rule_input_shapes_are_checked(index: int, field: str, mutation: str) -> None:
    rule = assessment()["rules"][index]
    key = next(iter(rule[field]))
    if mutation == "missing":
        del rule[field][key]
    elif mutation == "extra":
        rule[field]["unexpected"] = 0
    else:
        rule[field][key] = [] if key == "outcome" else "invalid"
    with pytest.raises(ValidationError):
        RuleAssessment.model_validate(rule)


@pytest.mark.parametrize("index", range(5))
def test_fixed_threshold_values_are_checked(index: int) -> None:
    rule = assessment()["rules"][index]
    key = next(iter(rule["threshold"]))
    rule["threshold"][key] = "failed" if key == "outcome" else 99
    with pytest.raises(ValidationError):
        RuleAssessment.model_validate(rule)


@pytest.mark.parametrize(
    "rule_id,field,key,value",
    [
        ("small_change", "observed", "files", True),
        ("small_cone", "observed", "nodes", "1"),
        ("combined_tests", "observed", "passed", True),
        ("known_spend", "threshold", "ledger_ok", 1),
        ("combined_tests", "threshold", "passed_min", 1.0),
    ],
)
def test_rule_inputs_reject_coercible_types(rule_id: str, field: str, key: str, value: Any) -> None:
    rule = next(rule for rule in assessment()["rules"] if rule["rule"] == rule_id)
    rule[field][key] = value
    with pytest.raises(ValidationError):
        RuleAssessment.model_validate(rule)


@pytest.mark.parametrize("result", ["pass", "fail"])
def test_missing_evidence_cannot_produce_a_known_result(result: str) -> None:
    rule = {**assessment()["rules"][0], "evidence": [], "result": result}
    with pytest.raises(ValidationError):
        RuleAssessment.model_validate(rule)
    assert RuleAssessment.model_validate({**rule, "result": "unknown"})


def test_missing_observations_and_failed_test_execution_are_unknown() -> None:
    rule = assessment()["rules"][0]
    assert RuleAssessment.model_validate({**rule, "observed": None, "result": "unknown"})
    for result in ("pass", "fail"):
        with pytest.raises(ValidationError):
            RuleAssessment.model_validate({**rule, "observed": None, "result": result})
    for outcome in ("error", "timeout"):
        failed = {**rule, "observed": {**rule["observed"], "outcome": outcome}}
        with pytest.raises(ValidationError):
            RuleAssessment.model_validate(failed)
        assert RuleAssessment.model_validate({**failed, "result": "unknown"})


@pytest.mark.parametrize(
    "rules",
    [
        [],
        assessment()["rules"][:4],
        assessment()["rules"] + assessment()["rules"][:1],
        [assessment()["rules"][0]] * 5,
    ],
)
def test_assessments_require_all_five_rules_once(rules: Any) -> None:
    with pytest.raises(ValidationError):
        ChangeAssessment.model_validate(assessment(rules=rules))


@pytest.mark.parametrize(
    "updates",
    [
        {"decision": "rejected", "merge": None, "merge_commit": None},
        {"assessment": None},
        {"assessment": assessment(mode="ask_me")},
        {"change_id": "change-2"},
        {"agent_id": "agent-2"},
        {"revision": 2},
        {"reasons": []},
        {"reasons": [""]},
        {"reasons": ["   "]},
    ],
)
def test_rules_decisions_require_matching_pass_assessments_and_reasons(updates: Any) -> None:
    with pytest.raises(ValidationError):
        ChangeResolved.model_validate(resolution(**updates))


@pytest.mark.parametrize("index", range(5))
@pytest.mark.parametrize("result", ["fail", "unknown"])
def test_any_nonpassing_rule_blocks_a_rules_decision(index: int, result: str) -> None:
    data = resolution()
    data["assessment"]["rules"][index]["result"] = result
    with pytest.raises(ValidationError):
        ChangeResolved.model_validate(data)


def test_rules_decision_carries_the_premerge_assessment_even_on_conflict() -> None:
    approved = ChangeResolved.model_validate(resolution())
    assert ChangeResolved.model_validate_json(approved.model_dump_json()) == approved
    conflict = resolution(
        merge="conflict",
        merge_commit=None,
        conflict={"kind": "merge", "paths": ["shop.py"], "tests": None},
    )
    assert ChangeResolved.model_validate(conflict).assessment == approved.assessment


@pytest.mark.parametrize("state", ["planning", "starting", "completed", "rejected", "failed"])
def test_plan_can_represent_generation_failure_or_completion_without_merges(state: str) -> None:
    assert Plan.model_validate(plan(state=state, subtasks=None)).state == state


def test_proposed_and_running_plan_invariants() -> None:
    with pytest.raises(ValidationError):
        Plan.model_validate(plan(subtasks=None))
    tasks = plan()["subtasks"]
    for ids in [(None, None), ("agent-1", None), ("agent-1", "agent-1")]:
        for task, agent_id in zip(tasks, ids, strict=True):
            task["agent_id"] = agent_id
        with pytest.raises(ValidationError):
            Plan.model_validate(plan(state="running", subtasks=tasks))
    for task, agent_id in zip(tasks, ["agent-1", "agent-2"], strict=True):
        task["agent_id"] = agent_id
    assert Plan.model_validate(plan(state="running", subtasks=tasks)).subtasks


def test_generated_schema_keeps_defaults_and_request_optional_fields() -> None:
    definitions = json.loads((ROOT / "contracts/generated/oversight.schema.json").read_text())[
        "$defs"
    ]
    assert definitions["MergeModeState"]["properties"]["mode"] == {
        "allOf": [{"$ref": "#/$defs/MergeMode"}],
        "default": "ask_me",
    }
    assert definitions["SetMergeMode"]["required"] == ["mode", "revision"]
    assert definitions["RejectPlan"]["required"] == ["revision"]
    assert definitions["PlanCreated"]["required"] == ["plan_id"]
    assert definitions["PlanRevision"]["required"] == ["revision"]
    assert definitions["CreatePlan"]["required"] == ["task"]
    assert definitions["UpdatePlan"]["required"] == ["revision", "graph_version", "subtasks"]


def test_generated_types_preserve_two_typed_subtasks() -> None:
    typescript = (ROOT / "contracts/generated/contracts.ts").read_text()
    assert "subtasks: [PlanSubtask, PlanSubtask] | null;" in typescript
    assert "subtasks: [SubtaskDraft, SubtaskDraft];" in typescript
    definitions = json.loads((ROOT / "contracts/generated/oversight.schema.json").read_text())[
        "$defs"
    ]
    subtasks = definitions["Plan"]["properties"]["subtasks"]["anyOf"][0]
    assert subtasks["minItems"] == subtasks["maxItems"] == 2
    assert subtasks["prefixItems"] == [{"$ref": "#/$defs/PlanSubtask"}] * 2
