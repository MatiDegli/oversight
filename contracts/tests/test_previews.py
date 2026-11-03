"""Dormant preview wire shapes and runtime invariants, without launch authority."""

import json
from pathlib import Path
from typing import get_args

import pytest
from contracts import generate
from pydantic import TypeAdapter, ValidationError

from oversight_contracts.commands import ERROR_STATUS, ErrorDetail, ErrorResponse
from oversight_contracts.common import ContractModel
from oversight_contracts.events import PreviewStatusEvent, ServerEvent
from oversight_contracts.previews import (
    PreviewCreated,
    PreviewDetails,
    PreviewId,
    PreviewLeaseToken,
    PreviewReason,
    PreviewState,
    PreviewStatus,
    PreviewTokenRequest,
    StartPreview,
)
from oversight_contracts.views import LeadViewKind, PreviewView

ROOT = Path(__file__).resolve().parents[2]
PREVIEW_ID = "preview-" + "0123456789abcdef" * 2
TOKEN = "aZ0_-" * 8 + "abc"
START = dict(run_id="run-20261103-000000", agent_id="agent-1", revision=1, head_commit="c1")
STATUS = dict(preview_id=PREVIEW_ID, **START, change_id="change-1", state="starting")
STATES = ("starting", "ready", "stopping", "stopped", "failed")
REASONS = (
    "closed",
    "lease_expired",
    "lifetime_expired",
    "mutation",
    "shutdown",
    "app_exited",
    "readiness_timeout",
    "launch_failed",
    "isolation_unavailable",
    "memory_limit",
    "cleanup_unverified",
    "worktree_changed",
    "profile_disabled",
)
ENVELOPE = dict(seq=1, ts="2026-11-03T00:00:00.000Z", type="preview.status", payload=STATUS)
MODELS = (
    (StartPreview, START),
    (PreviewCreated, dict(preview_id=PREVIEW_ID, lease_token=TOKEN)),
    (PreviewTokenRequest, dict(lease_token=TOKEN)),
    (PreviewStatus, STATUS),
    (PreviewDetails, dict(status=STATUS)),
    (PreviewView, dict(kind="preview", agent_id="agent-1")),
    (PreviewStatusEvent, ENVELOPE),
)


@pytest.mark.parametrize(("model", "data"), MODELS)
def test_shapes_frozen_extras_descriptions_and_round_trip(
    model: type[ContractModel], data: dict
) -> None:
    value = model.model_validate(data)
    assert model.model_validate_json(value.model_dump_json()) == value
    assert all(field.description for name, field in model.model_fields.items() if name != "ts")
    with pytest.raises(ValidationError, match="frozen"):
        setattr(value, next(iter(model.model_fields)), "changed")
    with pytest.raises(ValidationError, match="extra_forbidden"):
        model.model_validate(data | {"invented": "fact"})


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("reason", (None, *REASONS))
def test_every_state_reason_combination_including_nested(state: str, reason: str | None) -> None:
    data = STATUS | dict(state=state, reason=reason)
    valid = (state in ("starting", "ready")) == (reason is None)
    if valid:
        assert PreviewStatus.model_validate(data).reason == reason
        assert PreviewDetails.model_validate(dict(status=data)).status.state == state
        assert TypeAdapter(ServerEvent).validate_python(ENVELOPE | dict(payload=data))
    else:
        for model, body in (
            (PreviewStatus, data),
            (PreviewDetails, dict(status=data)),
            (PreviewStatusEvent, ENVELOPE | dict(payload=data)),
        ):
            with pytest.raises(ValidationError):
                model.model_validate(body)


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("url", (None, "http://127.0.0.1:12345/"))
def test_url_is_optional_even_when_ready_and_forbidden_otherwise(state: str, url: str | None):
    status = STATUS | dict(state=state, reason=None if state in ("starting", "ready") else "closed")
    body = dict(status=status, url=url)
    if url is None or state == "ready":
        assert PreviewDetails.model_validate(body).url == url
    else:
        with pytest.raises(ValidationError, match="only while ready"):
            PreviewDetails.model_validate(body)


@pytest.mark.parametrize(
    ("alias", "accepted", "rejected"),
    [
        (
            PreviewId,
            [PREVIEW_ID, "preview-" + "0" * 32, "preview-" + "f" * 32],
            [
                "",
                "preview-" + "a" * 31,
                "preview-" + "a" * 33,
                "preview-" + "A" * 32,
                "preview-" + "g" * 32,
                "preview-" + "a" * 32 + "\n",
                "x" + PREVIEW_ID,
            ],
        ),
        (
            PreviewLeaseToken,
            [TOKEN, "A" * 43, "_" * 43, "-" * 43, "0" * 43],
            [
                "",
                "a" * 42,
                "a" * 44,
                "a" * 42 + "=",
                "a" * 42 + "/",
                "a" * 42 + "+",
                "a" * 42 + "é",
                "a" * 43 + "\n",
            ],
        ),
        (PreviewState, list(STATES), ["", "unknown", "READY"]),
        (PreviewReason, list(REASONS), ["", "unknown", "application output", "CLOSED"]),
    ],
)
def test_four_closed_aliases(alias, accepted: list[str], rejected: list[str]) -> None:
    adapter = TypeAdapter(alias)
    for value in accepted:
        assert adapter.validate_python(value) == value
        assert adapter.validate_json(adapter.dump_json(value)) == value
    for value in rejected:
        with pytest.raises(ValidationError):
            adapter.validate_python(value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", "run-no"),
        ("agent_id", "agent-0"),
        ("change_id", "change-0"),
        ("revision", 0),
        ("revision", -1),
        ("head_commit", ""),
        ("state", "unknown"),
        ("reason", "exception text"),
    ],
)
def test_existing_identity_and_candidate_bounds(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        PreviewStatus.model_validate(STATUS | {field: value})
    if field in START:
        with pytest.raises(ValidationError):
            StartPreview.model_validate(START | {field: value})


@pytest.mark.parametrize("field", ["preview_id", "lease_token"])
def test_aliases_are_enforced_inside_route_models(field: str) -> None:
    data = dict(preview_id=PREVIEW_ID, lease_token=TOKEN)
    with pytest.raises(ValidationError):
        PreviewCreated.model_validate(data | {field: "invalid"})
    if field == "lease_token":
        with pytest.raises(ValidationError):
            PreviewTokenRequest.model_validate(dict(lease_token="invalid"))


def test_exact_route_fields_order_defaults_and_nullable_serialization() -> None:
    expected = (
        (StartPreview, ["run_id", "agent_id", "revision", "head_commit"]),
        (PreviewCreated, ["preview_id", "lease_token"]),
        (PreviewTokenRequest, ["lease_token"]),
        (
            PreviewStatus,
            [
                "preview_id",
                "run_id",
                "agent_id",
                "change_id",
                "revision",
                "head_commit",
                "state",
                "reason",
            ],
        ),
        (PreviewDetails, ["status", "url"]),
    )
    for model, keys in expected:
        assert list(model.model_fields) == keys
    status = PreviewStatus.model_validate(STATUS)
    details = PreviewDetails(status=status)
    assert status.model_dump()["reason"] is None
    assert details.model_dump()["url"] is None
    assert '"reason":null' in details.model_dump_json()
    assert '"url":null' in details.model_dump_json()
    for model, optional in ((PreviewStatus, "reason"), (PreviewDetails, "url")):
        assert optional not in model.model_json_schema(mode="validation")["required"]
        assert optional in model.model_json_schema(mode="serialization")["required"]
    with pytest.raises(ValidationError):
        PreviewDetails.model_validate(dict(status=STATUS | dict(state="ready"), url=""))


@pytest.mark.parametrize("field", ["lease_token", "url", "output", "command", "profile_version"])
def test_event_and_get_cannot_smuggle_private_runtime_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(ServerEvent).validate_python(
            ENVELOPE | dict(payload=STATUS | {field: "private"})
        )
    if field != "url":
        with pytest.raises(ValidationError):
            PreviewDetails.model_validate(dict(status=STATUS) | {field: "private"})


@pytest.mark.parametrize(
    "field", ["reason", "command", "url", "profile_version", "agent_id", "replacement_target"]
)
def test_token_request_has_no_caller_selected_controls(field: str) -> None:
    with pytest.raises(ValidationError):
        PreviewTokenRequest.model_validate(dict(lease_token=TOKEN) | {field: "chosen"})


def test_discriminated_unions_preserve_order_and_round_trip() -> None:
    view_adapter = TypeAdapter(LeadViewKind)
    view = PreviewView(kind="preview", agent_id="agent-1")
    assert view_adapter.validate_json(view_adapter.dump_json(view)) == view
    kinds = get_args(get_args(LeadViewKind.__value__)[0])
    assert [m.__name__ for m in kinds] == [
        "HighlightView",
        "FilesView",
        "BehaviorView",
        "MomentView",
        "PreviewView",
    ]
    adapter = TypeAdapter(ServerEvent)
    event = adapter.validate_python(ENVELOPE)
    assert isinstance(event, PreviewStatusEvent)
    assert adapter.validate_json(adapter.dump_json(event)) == event
    assert list(event.model_dump()) == ["seq", "ts", "type", "payload"]
    members = get_args(get_args(ServerEvent.__value__)[0])
    assert [get_args(m.model_fields["type"].annotation)[0] for m in members] == [
        "run.started",
        "graph.snapshot",
        "graph.delta",
        "agent.status",
        "agent.step",
        "agent.message",
        "agent.focus",
        "change.updated",
        "change.proposed",
        "change.resolved",
        "usage.tick",
        "model.attempt_failed",
        "error",
        "change.assessed",
        "merge_mode.changed",
        "plan.updated",
        "change.reviewed",
        "change.facts_recorded",
        "tests.executed",
        "revert.resolved",
        "queue.updated",
        "lead.status",
        "lead.view",
        "lead.reply",
        "grant.created",
        "grant.revoked",
        "preview.status",
    ]
    for field, value in (("seq", 0), ("ts", "invalid"), ("type", "preview.heartbeat")):
        with pytest.raises(ValidationError):
            adapter.validate_python(ENVELOPE | {field: value})


def test_generated_definitions_modes_and_literal_descriptions() -> None:
    definitions = json.loads((ROOT / "contracts/generated/oversight.schema.json").read_text())[
        "$defs"
    ]
    for model, _ in MODELS:
        generated = definitions[model.__name__]
        assert generated["additionalProperties"] is False
        assert generated["description"] == model.__doc__
        assert list(generated["properties"]) == list(model.model_fields)
        # Named references retain their own descriptions through generator normalization.
        for name, field in model.model_fields.items():
            prop = generated["properties"][name]
            if "description" in prop:
                assert prop["description"] == field.description
        assert generated["required"] == list(model.model_fields)
    assert definitions["PreviewId"]["pattern"] == r"^preview-[0-9a-f]{32}$"
    assert definitions["PreviewLeaseToken"]["pattern"] == r"^[A-Za-z0-9_-]{43}$"
    assert definitions["PreviewState"]["enum"] == list(STATES)
    assert definitions["PreviewReason"]["enum"] == list(REASONS)
    assert definitions["PreviewStatus"]["properties"]["reason"]["default"] is None
    assert definitions["PreviewDetails"]["properties"]["url"]["default"] is None
    assert definitions["LeadViewKind"]["oneOf"][-1] == {"$ref": "#/$defs/PreviewView"}
    event_union = definitions["ServerEvent"]
    assert event_union["oneOf"][-1] == {"$ref": "#/$defs/PreviewStatusEvent"}
    assert event_union["discriminator"]["mapping"]["preview.status"] == "#/$defs/PreviewStatusEvent"
    typescript = (ROOT / "contracts/generated/contracts.ts").read_text()
    # The payload is a named ref: the generator deliberately drops its sibling description.
    assert "Envelope for a public preview lifecycle transition." in typescript
    assert "Server-built loopback frame URL only while ready; otherwise null." in typescript
    # After validators are runtime Pydantic guarantees; TS and JSON Schema do not encode them.


def test_generator_registers_route_models_in_the_correct_schema_modes() -> None:
    assert generate.REQUESTS[-2:] == [StartPreview, PreviewTokenRequest]
    assert generate.RESPONSES[-3:] == [PreviewCreated, PreviewStatus, PreviewDetails]
    assert not {StartPreview, PreviewTokenRequest} & set(generate.RESPONSES)
    assert not {PreviewCreated, PreviewStatus, PreviewDetails} & set(generate.REQUESTS)
    for model in (StartPreview, PreviewTokenRequest):
        schema = model.model_json_schema(mode="validation")
        assert schema["required"] == list(model.model_fields)


@pytest.mark.parametrize("field", ["change_id", "profile_version", "command", "url"])
def test_start_cannot_select_a_derived_change_or_runtime_configuration(field: str) -> None:
    with pytest.raises(ValidationError):
        StartPreview.model_validate(START | {field: "chosen"})


def test_absent_profile_requires_empty_missing_without_a_new_setting() -> None:
    detail = ErrorDetail(
        code="not_configured", message="Preview profile is not configured.", missing=[]
    )
    response = ErrorResponse(error=detail)
    assert ErrorResponse.model_validate_json(response.model_dump_json()) == response
    assert response.model_dump()["error"]["missing"] == []
    assert ERROR_STATUS[detail.code] == 503
    for model, body in (
        (ErrorDetail, dict(code="not_configured", message="Preview profile is not configured.")),
        (ErrorResponse, dict(error=dict(code="not_configured", message="Not configured."))),
    ):
        with pytest.raises(ValidationError):
            model.model_validate(body)
