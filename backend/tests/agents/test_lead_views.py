"""Dormant adapter admission, masking and fresh per-turn eligibility."""

import json
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from backend.tests.agents.lead_helpers import call, lead, scripted, setup_tools, turn

from oversight.agents._lead_tools import LEAD_TOOL_SCHEMAS
from oversight.agents._lead_views import ViewRefusal, parse_view

VALID = [
    {"kind": "highlight", "agent_ids": ["agent-1"], "paths": ["a.py"]},
    {"kind": "files", "files": [{"change_id": "change-1", "path": "a.py"}]},
    {"kind": "behavior", "change_id": "change-1"},
    {"kind": "moment", "event_seq": 1},
]
INVALID = [
    {},
    {"kind": "unknown"},
    {"kind": "highlight"},
    {"kind": "highlight", "agent_ids": []},
    {"kind": "highlight", "agent_ids": ["agent-1"] * 2},
    {"kind": "highlight", "agent_ids": [f"agent-{n}" for n in range(1, 10)]},
    {"kind": "highlight", "agent_ids": [f"agent-{n}" for n in range(1, 9)], "paths": ["a.py"]},
    {"kind": "highlight", "paths": ["a.py", "a.py"]},
    {"kind": "highlight", "paths": ["./a.py"]},
    {"kind": "highlight", "paths": ["a/../a.py"]},
    {"kind": "files", "files": [{"change_id": "change-1", "path": "a.py"}] * 2},
    {"kind": "files", "files": [{"change_id": "change-1", "path": "a.py", "label": "invented"}]},
    {"kind": "files", "files": []},
    {"kind": "behavior", "change_id": "change-1", "paths": ["a.py"]},
    {"kind": "moment", "event_seq": 1, "change_id": None},
    {"kind": "moment", "event_seq": True},
    {"kind": "moment", "event_seq": "1"},
    {"kind": "moment", "event_seq": 1.0},
    {"kind": "moment", "event_seq": 0},
    {"kind": "moment", "event_seq": 1, "caption": None},
    {"kind": "moment", "event_seq": 1, "caption": " "},
    {"kind": "moment", "event_seq": 1, "caption": "c" * 161},
    {"kind": "moment", "event_seq": 1, "facts": "invented"},
    *[
        {"kind": "highlight", "paths": [p]}
        for p in (
            ".git/config",
            "x/.GIT/HEAD",
            ".env",
            "x/.env.local/y",
            "id_rsa",
            "x/key.pem",
            "x.key",
            "a" * 513,
            "é.py",
            "/a.py",
            "a//b",
            "a\\b",
        )
    ],
]


@pytest.mark.parametrize("arguments", VALID)
def test_strict_adapter_valid_arguments_and_mask_before_validation(arguments: dict) -> None:
    parsed = parse_view(
        json.dumps(arguments | {"caption": "s" * 200}), "agent-2", 12, lambda _: "[redacted]"
    )
    assert parsed.caption == "[redacted]" and parsed.agent_id == "agent-2"
    assert parsed.through_seq == 12 and parsed.view.kind == arguments["kind"]
    with pytest.raises(ViewRefusal):
        parse_view(json.dumps(arguments | {"caption": "ok"}), "agent-2", 12, lambda _: " ")


@pytest.mark.parametrize("arguments", INVALID)
def test_strict_adapter_refuses_sanitized_arguments(arguments: dict) -> None:
    with pytest.raises(ViewRefusal) as error:
        parse_view(json.dumps(arguments), "agent-1", 1, str)
    assert "invented" not in str(error.value)


@pytest.mark.anyio
async def test_status_before_view_despite_wake_and_correct_after_refusal() -> None:
    publish = AsyncMock()
    tools, _ = setup_tools(
        status=lambda: (5, "status"),
        resolve_views=lambda _: lambda v: v.view.kind == "moment",
        publish_view=publish,
    )
    tools.through_seq = 5
    assert not (await tools.show_view(json.dumps(VALID[3]))).ok
    publish.assert_not_awaited()
    assert (await tools.call("status", "{}")).ok
    assert tools.status_seen and tools.view_S == 5
    assert not (await tools.show_view(json.dumps(VALID[2]))).ok
    publish.assert_not_awaited()
    assert (
        await tools.show_view(json.dumps(VALID[3]))
    ).content == "Shown moment view through event 5."
    assert publish.await_count == 1
    tools.reset_views()
    assert not (await tools.show_view(json.dumps(VALID[3]))).ok
    assert tools.through_seq == 5 and tools.view_S is None and not tools.status_seen
    assert publish.await_count == 1


@pytest.mark.anyio
async def test_failed_status_invalidates_previous_eligibility() -> None:
    def overflow():
        raise ValueError("private diagnostic")

    publish = AsyncMock()
    tools, _ = setup_tools(
        status=lambda: (1, "status"), resolve_views=lambda _: lambda _: True, publish_view=publish
    )
    await tools.call("status", "{}")
    tools.status = overflow
    outcome = await tools.call("status", "{}")
    assert not outcome.ok and "private" not in outcome.content
    assert not tools.status_seen and tools.view_S is None
    assert not (await tools.show_view(json.dumps(VALID[3]))).ok
    publish.assert_not_awaited()


@pytest.mark.anyio
async def test_tool_remains_unadvertised_undispatched_with_no_step_on_refusal() -> None:
    assert "show_view" not in [
        cast(dict[str, Any], schema["function"])["name"] for schema in LEAD_TOOL_SCHEMAS
    ]
    tools, _ = setup_tools()
    assert not (await tools.call("show_view", json.dumps(VALID[3]))).ok
    model = scripted([turn(call("show_view", **VALID[3])), {"text": "answer", "usage": {}}])
    run, _, step = lead(model, tools)
    tools.status_seen, tools.view_S = True, 123
    await run.run()
    assert not tools.status_seen and tools.view_S is None
    assert run.turn_steps == 1 and run.failures == 1
    step.assert_not_awaited()


@pytest.mark.anyio
async def test_successful_dormant_callback_keeps_budgets_without_step_event(monkeypatch) -> None:
    publish = AsyncMock()
    tools, _ = setup_tools(
        status=lambda: (5, "status"), resolve_views=lambda _: lambda _: True, publish_view=publish
    )
    original = tools.call

    async def invoke(name, arguments):
        return (
            await tools.show_view(arguments)
            if name == "show_view"
            else await original(name, arguments)
        )

    monkeypatch.setattr(tools, "call", invoke)
    model = scripted(
        [turn(call("status")), turn(call("show_view", **VALID[3])), {"text": "answer", "usage": {}}]
    )
    run, _, step = lead(model, tools)
    await run.run()
    assert run.turn_steps == 2 and run.failures == 0
    assert publish.await_count == 1 and step.await_count == 1
