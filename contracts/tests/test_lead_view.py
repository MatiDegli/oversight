"""Reference-only views: closed shapes, documented bounds, immutability and round trips."""

import copy
import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from oversight_contracts.events import ServerEvent
from oversight_contracts.views import LeadView, PreviewView, ViewPath

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = json.loads((ROOT / "fixtures/contracts/views-1.18.json").read_text())


@pytest.mark.parametrize("row", EXAMPLES)
def test_shapes_docs_and_round_trips(row: dict) -> None:
    adapter = TypeAdapter(ServerEvent)
    event = adapter.validate_python(row)
    assert event.type == "lead.view"
    assert adapter.validate_json(adapter.dump_json(event)) == event
    assert event.payload.caption is None
    assert LeadView.model_fields["caption"].description == (
        "Masked optional speech; null when omitted or withheld."
    )
    assert all(f.description for f in type(event.payload.view).model_fields.values())
    with pytest.raises(ValidationError):
        event.payload.through_seq = 0
    # Envelope validation also runs at seq=1 before publication: no horizon comparison here.
    prevalidated = adapter.validate_python(row | {"seq": 1})
    assert prevalidated.type == "lead.view" and prevalidated.payload.through_seq == 15


@pytest.mark.parametrize("caption", ["", " ", "x" * 161])
def test_caption_bounds(caption: str) -> None:
    with pytest.raises(ValidationError):
        LeadView.model_validate(EXAMPLES[0]["payload"] | {"caption": caption})


@pytest.mark.parametrize("caption", [None, "x", "x" * 160])
def test_caption_optional_and_maximum(caption: str | None) -> None:
    assert LeadView.model_validate(EXAMPLES[0]["payload"] | {"caption": caption}).caption == caption


@pytest.mark.parametrize("path", ["a", ".hidden/a.py", "a" * 512])
def test_path_boundaries(path: str) -> None:
    assert TypeAdapter(ViewPath).validate_python(path) == path


@pytest.mark.parametrize("path", ["", "a" * 513, "./a", "../a", "a//b", "/a", "a\\b", "é"])
def test_noncanonical_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(ViewPath).validate_python(path)


@pytest.mark.parametrize("kind", [0, 1])
@pytest.mark.parametrize("count", [0, 9])
def test_reference_list_bounds(kind: int, count: int) -> None:
    payload = copy.deepcopy(EXAMPLES[kind]["payload"])
    payload["view"]["references"] *= count
    with pytest.raises(ValidationError):
        LeadView.model_validate(payload)


@pytest.mark.parametrize("kind", [0, 1])
def test_duplicate_references_are_rejected_in_contract(kind: int) -> None:
    payload = copy.deepcopy(EXAMPLES[kind]["payload"])
    payload["view"]["references"] *= 2
    with pytest.raises(ValidationError, match="distinct"):
        LeadView.model_validate(payload)


@pytest.mark.parametrize("layer", ["payload", "view", "reference"])
def test_extras_are_forbidden(layer: str) -> None:
    payload = copy.deepcopy(EXAMPLES[0]["payload"])
    target = payload if layer == "payload" else payload["view"]
    if layer == "reference":
        target = target["references"][0]
    target["facts"] = "invented"
    with pytest.raises(ValidationError):
        LeadView.model_validate(payload)


@pytest.mark.parametrize(
    "view",
    [
        {"kind": "other"},
        {"kind": "moment", "event_seq": 0},
        {"kind": "highlight", "references": [{"kind": "other"}]},
    ],
)
def test_closed_discriminators_and_positive_moment(view: dict) -> None:
    with pytest.raises(ValidationError):
        LeadView.model_validate(EXAMPLES[0]["payload"] | {"view": view})


def test_generated_schema_retains_literal_descriptions() -> None:
    schema = json.loads((ROOT / "contracts/generated/oversight.schema.json").read_text())
    definitions = schema["$defs"]
    assert (
        definitions["MomentView"]["description"]
        == "One committed event; navigation is replay-only."
    )
    assert definitions["LeadViewEvent"]["description"] == "Envelope for one committed lead view."


def test_preview_names_target_separately_from_requesting_lead() -> None:
    view = LeadView(
        agent_id="agent-9",
        through_seq=15,
        view=PreviewView(kind="preview", agent_id="agent-1"),
    )
    assert view.agent_id == "agent-9"
    assert isinstance(view.view, PreviewView)
    assert view.view.agent_id == "agent-1"
    assert LeadView.model_validate_json(view.model_dump_json()) == view
    assert list(view.view.model_dump()) == ["kind", "agent_id"]
    for extra in ("change_id", "profile_version"):
        with pytest.raises(ValidationError):
            PreviewView.model_validate(view.view.model_dump() | {extra: "chosen"})
    with pytest.raises(ValidationError):
        PreviewView(kind="preview", agent_id="agent-0")
