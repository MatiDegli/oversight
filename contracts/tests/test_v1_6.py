"""Approved 1.6 identifiers and run-local inbox references, without runtime admission."""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from oversight_contracts.commands import AgentCreated, MissingSetting, StartAgent
from oversight_contracts.events import AgentStatus, RunStarted, SlotInfo
from oversight_contracts.queue import QueuedAgentTask
from oversight_contracts.review import NodeBehaviorDiff

ROOT = Path(__file__).resolve().parents[2]
STATUS = dict(
    agent_id="agent-1",
    slot="A",
    change_id="change-1",
    task="Answer the decision",
    model="fake",
    state="working",
    step=1,
    max_steps=30,
    detail=None,
)
RUN = json.loads((ROOT / "fixtures/events/handwritten-level1.jsonl").read_text().splitlines()[0])[
    "payload"
]
SLOT_MODELS = [
    (StartAgent, dict(task="work"), "slot"),
    (AgentCreated, dict(agent_id="agent-1", change_id="change-1"), "slot"),
    (MissingSetting, dict(name="META_API_KEY", problem="missing"), "slots"),
    (SlotInfo, dict(default_model="fake"), "slot"),
    (RunStarted, RUN, "slots"),
    (AgentStatus, STATUS, "slot"),
    (QueuedAgentTask, dict(task="work", model=None, zone=None), "slot"),
]


def slot_payload(model: Any, base: Any, field: str, slot: Any) -> dict[str, Any]:
    value = slot
    if model is MissingSetting:
        value = [slot]
    elif model is RunStarted:
        value = [dict(slot=slot, default_model="fake")]
    return {**base, field: value}


@pytest.mark.parametrize("model,base,field", SLOT_MODELS, ids=lambda v: getattr(v, "__name__", ""))
@pytest.mark.parametrize("slot", list("ABCDEF"))
def test_each_slot_bearing_model_accepts_a_through_f(
    model: Any, base: Any, field: str, slot: str
) -> None:
    payload = slot_payload(model, base, field, slot)
    parsed = model.model_validate(payload)
    assert model.model_validate_json(parsed.model_dump_json()) == parsed
    assert parsed.model_dump(mode="json")[field] == payload[field]


@pytest.mark.parametrize("model,base,field", SLOT_MODELS, ids=lambda v: getattr(v, "__name__", ""))
@pytest.mark.parametrize("slot", ["G", *list("abcdef"), "", "AA", "1", 1, True, {}])
def test_each_slot_bearing_model_rejects_outside_the_closed_enum(
    model: Any, base: Any, field: str, slot: Any
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(slot_payload(model, base, field, slot))


CHANGE = dict(change_id="change-1", revision=0, head_commit="base", evidence_seq=1)
CARD = dict(**CHANGE, kind="review_card", node_id="shop.pricing:compute_price", question="Keep it?")
REFS = [
    CARD,
    *[
        dict(**CHANGE, kind=kind)
        for kind in ("checks_unavailable", "follow_main", "risky_area", "examples_before_code")
    ],
    dict(kind="lead_plan", agent_id="agent-2", revision=1, evidence_seq=1),
]


@pytest.mark.parametrize("ref", REFS, ids=lambda ref: ref["kind"])
@pytest.mark.parametrize(
    "state",
    ["starting", "working", "ready_for_review", "revising", "paused", "halted", "done", "stopped"],
)
def test_answering_round_trips_request_status_and_event_unchanged(ref: Any, state: str) -> None:
    from pydantic import TypeAdapter

    from oversight_contracts.events import ServerEvent

    request = StartAgent.model_validate(dict(task=STATUS["task"], answering=ref))
    assert request.answering is not None
    status = AgentStatus.model_validate({**STATUS, "state": state, "answering": request.answering})
    wire = dict(seq=2, ts="2026-10-28T20:00:00.000Z", type="agent.status", payload=status)
    adapter = TypeAdapter(ServerEvent)
    event = adapter.validate_python(wire)
    serialized = json.loads(adapter.dump_json(event))
    assert serialized["payload"]["answering"] == ref
    assert request.model_dump(mode="json")["answering"] == ref
    assert adapter.validate_json(adapter.dump_json(event)) == event


@pytest.mark.parametrize("model,base", [(StartAgent, dict(task="work")), (AgentStatus, STATUS)])
@pytest.mark.parametrize("attachment", [{}, dict(answering=None)])
def test_omitted_and_null_answering_serialize_as_null(
    model: Any, base: Any, attachment: Any
) -> None:
    parsed = model.model_validate({**base, **attachment})
    assert parsed.model_dump(mode="json")["answering"] is None
    assert model.model_validate_json(parsed.model_dump_json()) == parsed


@pytest.mark.parametrize("model,base", [(StartAgent, dict(task="work")), (AgentStatus, STATUS)])
@pytest.mark.parametrize("ref", REFS, ids=lambda ref: ref["kind"])
def test_every_reference_identity_field_is_required(model: Any, base: Any, ref: Any) -> None:
    for field in ref:
        incomplete = {key: value for key, value in ref.items() if key != field}
        with pytest.raises(ValidationError):
            model.model_validate({**base, "answering": incomplete})


@pytest.mark.parametrize("model,base", [(StartAgent, dict(task="work")), (AgentStatus, STATUS)])
@pytest.mark.parametrize("ref", REFS, ids=lambda ref: ref["kind"])
def test_reference_rejects_bad_identity_and_cross_variant_extras(
    model: Any, base: Any, ref: Any
) -> None:
    invalid = [dict(kind="unknown"), dict(evidence_seq=0), dict(evidence_seq=-1)]
    invalid += [dict(revision=-1), dict(revision=0 if ref["kind"] == "lead_plan" else -2)]
    if ref["kind"] == "lead_plan":
        invalid += [dict(agent_id="agent-0"), dict(change_id="change-1"), dict(plan_id="plan-1")]
    else:
        invalid += [dict(change_id="change-0"), dict(head_commit=""), dict(agent_id="agent-1")]
    if ref["kind"] == "review_card":
        invalid += [dict(question=""), dict(plan_id="plan-1")]
    else:
        invalid += [dict(node_id="a:f"), dict(question="Keep it?")]
    for update in invalid:
        with pytest.raises(ValidationError):
            model.model_validate({**base, "answering": {**ref, **update}})


@pytest.mark.parametrize(
    "update",
    [
        dict(question="A different question?"),
        dict(evidence_seq=2),
        dict(change_id="change-2"),
        dict(revision=1),
        dict(head_commit="new-head"),
    ],
)
def test_cards_sharing_a_node_keep_their_question_evidence_and_candidate(update: Any) -> None:
    first = StartAgent.model_validate(dict(task="answer", answering=CARD))
    other = StartAgent.model_validate(dict(task="answer", answering={**CARD, **update}))
    assert first.answering != other.answering
    assert other.model_dump(mode="json")["answering"] == {**CARD, **update}


def test_save_file_unchanged_and_plan_cardinality_is_one_to_six() -> None:
    from oversight_contracts.commands import SaveFile, UpdatePlan
    from oversight_contracts.plans import SubtaskDraft

    with pytest.raises(ValidationError):
        SaveFile.model_validate(dict(path="a.py", content="new", base_hash=None))
    subtask = dict(task="work", zone=dict(node_ids=[], paths=[]))
    for count in (0, 7):
        with pytest.raises(ValidationError):
            UpdatePlan.model_validate(
                dict(revision=1, graph_version="v1", subtasks=[subtask] * count)
            )
    assert (
        len(
            UpdatePlan.model_validate(
                dict(revision=1, graph_version="v1", subtasks=[subtask] * 2)
            ).subtasks
        )
        == 2
    )
    with pytest.raises(ValidationError):
        SubtaskDraft.model_validate({**subtask, "depends_on": [6]})


@pytest.mark.anyio
async def test_versioned_fixture_validates_input_and_normalized_schema(tmp_path: Path) -> None:
    from pydantic import TypeAdapter

    from oversight.workspace import run_process
    from oversight_contracts.events import ServerEvent

    adapter = TypeAdapter(ServerEvent)
    raw = json.loads((ROOT / "fixtures/contracts/slots-answering-1.6.json").read_text())
    events = [adapter.validate_python(row) for row in raw]
    statuses = [event.payload for event in events if event.type == "agent.status"]
    assert {status.slot for status in statuses} == set("ABCDEF")
    assert [status.model_dump(mode="json")["answering"] for status in statuses] == REFS
    records = []
    for event in events:
        assert adapter.validate_json(adapter.dump_json(event)) == event
        if event.type == "agent.status":
            request = StartAgent.model_validate(
                dict(
                    task=event.payload.task,
                    slot=event.payload.slot,
                    answering=event.payload.answering,
                )
            )
            assert request.answering == event.payload.answering
        records.append(dict(source="slots-answering-1.6", event=event.model_dump(mode="json")))
    serialized = tmp_path / "serialized.json"
    serialized.write_text(json.dumps(records))
    result = await run_process(
        ["node", "contracts/tests/validate_serialized.cjs", str(serialized)], cwd=ROOT, timeout_s=60
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_schema_changes_only_approved_attachments() -> None:
    baseline = json.loads((ROOT / "contracts/baselines/1.5.0.schema.json").read_text())["$defs"]
    current = json.loads((ROOT / "contracts/generated/oversight.schema.json").read_text())["$defs"]
    assert current["NonEmptyText"] == {"minLength": 1, "type": "string"}
    assert current["MessageText"] == {"minLength": 1, "maxLength": 4096, "type": "string"}
    assert set(current) - set(baseline) == {
        "AgentMessageEvent",
        "AgentMessageSent",
        "MessageText",
        "UsageCoverage",
        "UsageCostRow",
        "UsageCost",
        "UsageActor",
        "UsageHours",
        "UsageTeam",
        "UsageView",
        "StageTime",
        "SuitePhaseTime",
        "ChangeStages",
        "DecisionRef",
        "ReviewCardDecisionRef",
        "ChangeDecisionRef",
        "LeadPlanDecisionRef",
        "GrantReason",
        "GrantScope",
        "GrantRequest",
        "GrantCreated",
        "GrantRevoked",
        "GrantAuthorization",
        "GrantUse",
        "GrantCreatedEvent",
        "GrantRevokedEvent",
        "ModelAttemptFailed",
        "ModelAttemptFailedEvent",
        "ParameterExtensionDiff",
        "ConfirmExamples",
    }
    assert not set(baseline) - set(current)
    for name, definition in baseline.items():
        if name == "ChangeFacts":
            _check_parameter_extension_attachment(current[name], definition)
            continue
        plan_fields = {
            "Plan": {"subtasks"},
            "PlanSubtask": {"depends_on", "state"},
            "SubtaskDraft": {"depends_on"},
            "UpdatePlan": {"subtasks"},
            "QueueSpec": {"plan_id"},
            "EnqueueTask": {"plan_id"},
            "QueuedTask": {"plan_id", "agent_ids"},
        }
        if name in plan_fields:
            updated = deepcopy(current[name])
            updated["description"] = definition["description"]
            for field_name in plan_fields[name]:
                if field_name == "state":
                    assert updated["properties"].pop(field_name)["default"] is None
                    updated["required"].remove(field_name)
                else:
                    updated["properties"][field_name] = definition["properties"][field_name]
            assert updated == definition, name
            continue
        if name == "Limits":
            updated = deepcopy(current[name])
            descriptions = {
                "max_steps": (
                    "Tool calls per round; Request changes starts a new round with the same budget."
                ),
                "max_wall_clock_s": "Per round; Request changes starts a new round.",
            }
            for field, description in descriptions.items():
                assert updated["properties"][field]["description"] == description
                updated["properties"][field]["description"] = definition["properties"][field][
                    "description"
                ]
            assert updated == definition
            continue
        if name == "ChangeView":
            updated = deepcopy(current[name])
            field = updated["properties"].pop("stages")
            assert field == {
                "anyOf": [{"$ref": "#/$defs/ChangeStages"}, {"type": "null"}],
                "default": None,
                "description": "Raw time per stage through seq; null for incomplete history.",
            }
            updated["required"].remove("stages")
            assert updated == definition
            continue
        if name == "LeadStatus":
            updated = deepcopy(current[name])
            field = updated["properties"].pop("requested_plan_id")
            assert field["default"] is None
            assert field["anyOf"] == [{"$ref": "#/$defs/PlanId"}, {"type": "null"}]
            updated["required"].remove("requested_plan_id")
            assert updated == definition
            continue
        if name == "AgentStep":
            updated = deepcopy(current[name])
            assert updated["properties"].pop("error_kind")["default"] is None
            updated["required"].remove("error_kind")
            assert updated == definition
            continue
        if name in {"NodeBehaviorDiff", "MeasuredCaller"}:
            approved = (
                "Inputs compared on both sides with equal typed inputs over shared parameters "
                "when\n"
                "omitted candidate-only parameters have literal or resolved constant defaults."
            )
            updated = deepcopy(current[name])
            field = updated["properties"]["compared_count"]
            assert (
                field["description"]
                == NodeBehaviorDiff.model_fields["compared_count"].description
                == approved
            )
            field["description"] = definition["properties"]["compared_count"]["description"]
            assert updated == definition, name
            continue
        if name not in {
            "Slot",
            "StartAgent",
            "AgentStatus",
            "ApproveChange",
            "ChangeResolved",
            "ServerEvent",
        }:
            assert current[name] == definition, name
    assert current["Slot"]["enum"] == list("ABCDEF")
    for name, field in (("ApproveChange", "grant"), ("ChangeResolved", "grants")):
        properties = current[name]["properties"]
        assert set(properties) - set(baseline[name]["properties"]) == {field}
        assert properties[field]["default"] is None
        assert field not in current[name]["required"]
        assert {key: value for key, value in properties.items() if key != field} == baseline[name][
            "properties"
        ]
        assert {key: value for key, value in current[name].items() if key != "properties"} == {
            key: value for key, value in baseline[name].items() if key != "properties"
        }
    for name in ("StartAgent", "AgentStatus"):
        fields = current[name]["properties"]
        assert set(fields) - set(baseline[name]["properties"]) == {"answering"}
        assert fields["answering"]["default"] is None
        assert ("answering" in current[name]["required"]) == (name == "AgentStatus")
        assert {key: value for key, value in fields.items() if key != "answering"} == baseline[
            name
        ]["properties"]


def _check_parameter_extension_attachment(
    current: dict[str, Any], baseline: dict[str, Any]
) -> None:
    updated = deepcopy(current)
    assert updated["properties"].pop("parameter_extensions") == {
        "anyOf": [
            {"items": {"$ref": "#/$defs/ParameterExtensionDiff"}, "type": "array"},
            {"type": "null"},
        ],
        "default": None,
        "description": "Calls that use proposed-only parameters; null means none were recorded.",
    }
    updated["required"].remove("parameter_extensions")
    assert updated == baseline
