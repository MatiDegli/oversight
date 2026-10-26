"""Contracts v1: one valid example of every event, the rules the models enforce, and the schema."""

import copy
import json
from pathlib import Path
from typing import Any, get_args

import pytest
from pydantic import TypeAdapter, ValidationError

from oversight_contracts import CONTRACT_VERSION
from oversight_contracts.commands import ERROR_STATUS, ErrorCode, RequestChanges, StartAgent
from oversight_contracts.events import ServerEvent
from oversight_contracts.graph import GraphEdge

GENERATED = Path(__file__).resolve().parents[1] / "generated"
EVENTS = TypeAdapter(ServerEvent)
TS = "2026-10-27T14:03:12.345Z"

NODE = {
    "id": "shop.pricing:compute_price",
    "kind": "function",
    "name": "compute_price",
    "qualname": "compute_price",
    "path": "shop/pricing.py",
    "start_line": 8,
    "end_line": 21,
    "parent_id": "shop.pricing",
    "is_test": False,
}
EDGE = {
    "id": "calls:shop.orders:create_order->shop.pricing:compute_price",
    "kind": "calls",
    "src": "shop.orders:create_order",
    "dst": "shop.pricing:compute_price",
    "confidence": "exact",
    "reason": None,
}
CHANGE_GRAPH = {
    "nodes_added": [],
    "nodes_modified": [NODE["id"]],
    "nodes_removed": [],
    "edges_added": [],
    "edges_removed": [],
}
SUITE = {
    "outcome": "passed",
    "passed": 28,
    "failed": 0,
    "skipped": 0,
    "duration_ms": 900,
    "failed_tests": [],
}
CONE = {
    "seeds": [NODE["id"]],
    "entries": [{"node_id": "shop.orders:create_order", "distance": 1}],
    "edges_followed": "exact",
    "max_depth": 4,
    "max_nodes": 40,
    "omitted_by_depth": 0,
    "omitted_by_limit": 0,
    "tests_reaching": 6,
}
CHANGE_IDS = {"change_id": "change-1", "agent_id": "agent-1"}

PAYLOADS: dict[str, dict[str, Any]] = {
    "run.started": {
        "run_id": "run-20261027-140312",
        "contract_version": CONTRACT_VERSION,
        "prompt_version": "agent-v1-3f2a",
        "workspace": "demo-repo",
        "base_commit": "a1b2c3d",
        "models": [
            {
                "id": "fake",
                "label": "Scripted model",
                "provider": "fake",
                "model": "fake",
                "reasoning": None,
            }
        ],
        "slots": [{"slot": "A", "default_model": "fake"}],
        "limits": {
            "max_steps": 30,
            "max_output_tokens": 4096,
            "max_wall_clock_s": 600,
            "test_timeout_s": 60,
            "cone_max_depth": 4,
            "cone_max_nodes": 40,
        },
        "budget": {"budget_usd": 150.0, "spent_usd": 0.0, "warn_at": [0.5, 0.8], "block_at": 0.9},
    },
    "graph.snapshot": {
        "version": "v1",
        "commit": "a1b2c3d",
        "nodes": [NODE],
        "edges": [EDGE],
        "parse_errors": [],
        "positions": {NODE["id"]: {"x": 10.0, "y": -4.5}},
    },
    "graph.delta": {
        "from_version": "v1",
        "to_version": "v2",
        "commit": "d4e5f6a",
        "cause": "merge",
        "nodes_added": [],
        "nodes_changed": [NODE],
        "nodes_removed": [],
        "edges_added": [],
        "edges_changed": [],
        "edges_removed": [],
        "parse_errors": [{"path": "shop/cart.py", "line": 3, "message": "invalid syntax"}],
    },
    "agent.status": {
        "agent_id": "agent-1",
        "slot": "A",
        "change_id": "change-1",
        "task": "Cap the combined discount at 30%",
        "model": "fake",
        "state": "working",
        "step": 2,
        "max_steps": 30,
        "detail": None,
    },
    "agent.step": {
        "agent_id": "agent-1",
        "step": 3,
        "tool": "run_tests",
        "summary": "Ran tests: 28 of 28 pass",
        "result": "ok",
        "error": None,
        "duration_ms": 950,
        "tests": SUITE,
    },
    "agent.focus": {
        "agent_id": "agent-1",
        "mode": "read",
        "ranges": [{"path": "shop/pricing.py", "start_line": 8, "end_line": 21}],
        "node_ids": [NODE["id"]],
    },
    "change.updated": {
        **CHANGE_IDS,
        "author": "agent",
        "file": {
            "path": "shop/pricing.py",
            "status": "modified",
            "original": "a = 1\n",
            "modified": "a = 2\n",
            "lines_added": 1,
            "lines_removed": 1,
        },
        "node_ids": [NODE["id"]],
        "graph": CHANGE_GRAPH,
        "files_changed": 1,
        "lines_added": 1,
        "lines_removed": 1,
    },
    "change.proposed": {
        **CHANGE_IDS,
        "revision": 1,
        "task": "Cap the combined discount at 30%",
        "summary": "Capped the discount and added tests.",
        "base_commit": "a1b2c3d",
        "head_commit": "b2c3d4e",
        "files": [
            {"path": "shop/pricing.py", "status": "modified", "lines_added": 4, "lines_removed": 1}
        ],
        "lines_added": 4,
        "lines_removed": 1,
        "graph": CHANGE_GRAPH,
        "tests": SUITE,
        "impact": CONE,
    },
    "change.resolved": {
        **CHANGE_IDS,
        "revision": 1,
        "decision": "approved",
        "reason": None,
        "comment": None,
        "merge": "merged",
        "merge_commit": "d4e5f6a",
        "conflict": None,
    },
    "usage.tick": {
        "agent_id": "agent-1",
        "model": None,
        "input_tokens": None,
        "output_tokens": 210,
        "cached_tokens": None,
        "cost_usd": None,
        "spent_usd": 0.42,
        "budget_usd": 150.0,
        "brake": "ok",
    },
    "error": {
        "source": "model_api",
        "message": "429 from the API; retrying",
        "agent_id": "agent-1",
        "change_id": None,
        "fatal": False,
    },
}


def event(event_type: str, seq: int = 1, **payload_changes: Any) -> dict[str, Any]:
    """A fresh copy of the example, so a test that changes it cannot change another test's."""
    payload = copy.deepcopy(PAYLOADS[event_type])
    payload.update(payload_changes)
    return {"seq": seq, "ts": TS, "type": event_type, "payload": payload}


def test_the_eleven_event_types_are_agents_md_list() -> None:
    union = get_args(get_args(ServerEvent.__value__)[0])
    types = {get_args(model.model_fields["type"].annotation)[0] for model in union}
    assert types == set(PAYLOADS)
    assert len(PAYLOADS) == 11


@pytest.mark.parametrize("event_type", sorted(PAYLOADS))
def test_every_event_round_trips_with_every_key_present_in_order(event_type: str) -> None:
    # The examples list every field in docs/proposal.md's order, so comparing the serialized text
    # also checks the order of the keys.
    original = event(event_type)
    parsed = EVENTS.validate_python(original)
    dumped = EVENTS.dump_json(parsed).decode()
    assert dumped == json.dumps(original, separators=(",", ":"))
    assert EVENTS.validate_json(dumped) == parsed


def test_an_event_missing_a_nullable_key_is_rejected() -> None:
    incomplete = event("agent.status")
    del incomplete["payload"]["detail"]
    with pytest.raises(ValidationError, match="detail"):
        EVENTS.validate_python(incomplete)


@pytest.mark.parametrize("where", ["envelope", "payload", "nested"])
def test_extra_fields_are_rejected_at_every_level(where: str) -> None:
    bad = event("graph.snapshot")
    target = {
        "envelope": bad,
        "payload": bad["payload"],
        "nested": bad["payload"]["nodes"][0],
    }[where]
    target["surprise"] = 1
    with pytest.raises(ValidationError, match="surprise"):
        EVENTS.validate_python(bad)


@pytest.mark.parametrize(
    ("event_type", "changes"),
    [
        ("agent.status", {"agent_id": "agent-0"}),
        ("agent.status", {"change_id": "change-x"}),
        ("run.started", {"run_id": "run-2026-10-27"}),
        ("agent.status", {"slot": "C"}),
    ],
)
def test_malformed_ids_are_rejected(event_type: str, changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        EVENTS.validate_python(event(event_type, **changes))


@pytest.mark.parametrize("ts", ["2026-10-27T14:03:12Z", "2026-10-27T14:03:12.345+00:00", ""])
def test_timestamps_are_utc_with_milliseconds(ts: str) -> None:
    with pytest.raises(ValidationError):
        EVENTS.validate_python({**event("error"), "ts": ts})


def test_seq_starts_at_one() -> None:
    with pytest.raises(ValidationError):
        EVENTS.validate_python(event("error", seq=0))


@pytest.mark.parametrize(
    ("confidence", "reason", "valid"),
    [
        ("exact", None, True),
        ("possible", "unknown_receiver", True),
        ("possible", None, False),
        ("exact", "rebound_name", False),
    ],
)
def test_an_edge_has_a_reason_exactly_when_possible(
    confidence: str, reason: str | None, valid: bool
) -> None:
    edge = {**EDGE, "confidence": confidence, "reason": reason}
    if valid:
        GraphEdge.model_validate(edge)
    else:
        with pytest.raises(ValidationError, match="reason"):
            GraphEdge.model_validate(edge)


@pytest.mark.parametrize(
    ("changes", "rule"),
    [
        ({"decision": "rejected"}, "merge is set exactly"),  # a rejection carries no merge
        ({"merge": None}, "merge is set exactly"),  # an approval says how the merge went
        ({"merge_commit": None}, "merge_commit is set exactly"),  # a merge names main's HEAD
        ({"merge": "conflict", "merge_commit": None}, "conflict is set exactly"),  # and its details
    ],
)
def test_a_resolution_states_its_merge_consistently(changes: dict[str, Any], rule: str) -> None:
    with pytest.raises(ValidationError, match=rule):
        EVENTS.validate_python(event("change.resolved", **changes))


def test_a_conflict_resolution_is_valid_with_its_details() -> None:
    conflict = {"kind": "merge", "paths": ["shop/pricing.py"], "tests": None}
    EVENTS.validate_python(
        event("change.resolved", merge="conflict", merge_commit=None, conflict=conflict)
    )


def test_request_bodies_may_omit_optional_keys_but_not_required_text() -> None:
    assert StartAgent.model_validate({"task": "Fix reserve"}).slot is None
    with pytest.raises(ValidationError):
        StartAgent.model_validate({"task": ""})
    with pytest.raises(ValidationError):
        RequestChanges.model_validate({"comment": ""})


def test_every_error_code_has_the_status_of_section_4_7() -> None:
    assert ERROR_STATUS == {
        "not_found": 404,
        "wrong_state": 409,
        "slot_busy": 409,
        "spend_brake": 403,
        "invalid_path": 422,
        "stale_file": 409,
        "not_implemented": 501,
        "invalid_request": 422,
    }
    assert set(ERROR_STATUS) == set(get_args(ErrorCode.__value__))


def test_the_schema_forbids_extra_keys_and_requires_every_response_key() -> None:
    schema = json.loads((GENERATED / "oversight.schema.json").read_text())
    requests = {"StartAgent", "AgentMessage", "ApproveChange", "RequestChanges", "RejectChange"}
    requests.add("SaveFile")
    objects = {name: d for name, d in schema["$defs"].items() if d.get("type") == "object"}
    assert "ChangeResolvedEvent" in objects
    for name, definition in objects.items():
        if "properties" not in definition:  # a dict, such as positions, has no properties
            continue
        assert definition["additionalProperties"] is False, name
        if name not in requests:
            assert set(definition["required"]) == set(definition["properties"]), name
    assert "slot" not in schema["$defs"]["StartAgent"]["required"]


def test_the_typescript_types_carry_the_contract_version() -> None:
    typescript = (GENERATED / "contracts.ts").read_text()
    assert f'export const CONTRACT_VERSION = "{CONTRACT_VERSION}";' in typescript
    assert "export type ServerEvent =" in typescript
