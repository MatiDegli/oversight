"""Production view dispatch, strict admission and fresh per-turn eligibility."""

import json
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest
from backend.tests.agents.lead_helpers import call, lead, scripted, setup_tools, turn

from oversight.agents._lead_tools import LEAD_TOOL_SCHEMAS
from oversight.agents._lead_views import ViewRefusal, parse_view
from oversight.agents._prompts import LEAD_PROMPT
from oversight.events import StatusInventoryTooLarge, fold_lead_status, serialize_status
from oversight.events.view_resolution import capture_view_resolver

VALID = [
    {"kind": "highlight", "agent_ids": ["agent-1"], "paths": ["a.py"]},
    {"kind": "files", "files": [{"change_id": "change-1", "path": "a.py"}]},
    {"kind": "behavior", "change_id": "change-1"},
    {"kind": "moment", "event_seq": 1},
    {"kind": "preview", "agent_id": "agent-1"},
]
INVALID = [
    *[
        {"kind": "preview", **fields}
        for fields in [
            {},
            {"agent_id": None},
            {"agent_id": "agent-0"},
            {"agent_id": "agent-01"},
            {"agent_id": 1},
            {"agent_ids": ["agent-1"]},
            *[
                {"agent_id": "agent-1", key: value}
                for key, value in {
                    "agent_ids": ["agent-1"],
                    "change_id": "change-1",
                    "revision": 1,
                    "head_commit": "a" * 40,
                    "url": "http://secret.invalid/token",
                    "command": "secret-command",
                    "profile": "secret-profile",
                    "token": "secret-token",
                    "facts": "invented",
                    "paths": None,
                    "files": None,
                    "event_seq": None,
                    "caption": None,
                }.items()
            ],
        ]
    ],
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
    assert not (await tools.call("show_view", json.dumps(VALID[3]))).ok
    publish.assert_not_awaited()
    assert (await tools.call("status", "{}")).ok
    assert tools.status_seen and tools.view_S == 5
    assert not (await tools.call("show_view", json.dumps(VALID[2]))).ok
    publish.assert_not_awaited()
    assert (
        await tools.call("show_view", json.dumps(VALID[3]))
    ).content == "Shown moment view through event 5."
    assert publish.await_count == 1
    tools.reset_views()
    assert not (await tools.call("show_view", json.dumps(VALID[3]))).ok
    assert tools.through_seq == 5 and tools.view_S is None and not tools.status_seen
    assert publish.await_count == 1


@pytest.mark.anyio
async def test_failed_status_invalidates_previous_eligibility() -> None:
    def overflow():
        raise StatusInventoryTooLarge("private diagnostic")

    publish = AsyncMock()
    tools, _ = setup_tools(
        status=lambda: (1, "status"), resolve_views=lambda _: lambda _: True, publish_view=publish
    )
    await tools.call("status", "{}")
    tools.status = overflow
    outcome = await tools.call("status", "{}")
    assert not outcome.ok and "private" not in outcome.content
    assert not tools.status_seen and tools.view_S is None
    assert not (await tools.call("show_view", json.dumps(VALID[3]))).ok
    publish.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("fallback_fails", [False, True])
async def test_status_inventory_overflow_fallback(fallback_fails: bool) -> None:
    def overflow() -> tuple[int, str]:
        return 5, serialize_status(5, [], required=[("eligible path", {"path": "a" * 512})] * 100)

    fallback = Mock(return_value=(5, "Committed status without views.\n"))
    if fallback_fails:
        fallback.side_effect = ValueError("private diagnostic")
    publish = AsyncMock()
    tools, _ = setup_tools(status=overflow, status_without_views=fallback, publish_view=publish)
    tools.through_seq = 3
    outcome = await tools.call("status", "{}")
    fallback.assert_called_once_with()
    assert outcome.ok is not fallback_fails
    assert outcome.content == (
        "Status eligibility exceeds the inventory limit; no views are eligible."
        if fallback_fails
        else "Committed status without views.\n"
        "No views are eligible: the status inventory is over its limit."
    )
    assert tools.through_seq == (3 if fallback_fails else 5)
    assert not tools.status_seen and tools.view_S is None
    assert not (await tools.call("show_view", json.dumps(VALID[3]))).ok
    publish.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("source", ["prefix", "horizon"])
async def test_status_other_value_errors_propagate_without_fallback(source: str) -> None:
    fallback = Mock(return_value=(5, "status without views"))
    tools, _ = setup_tools(
        status=lambda: (5, "status"),
        status_without_views=fallback,
        resolve_views=lambda _: lambda _: True,
    )
    await tools.call("status", "{}")
    assert tools.status_seen and tools.view_S == 5
    if source == "prefix":
        tools.status = lambda: fold_lead_status([], 5)
        message = "status requires the complete committed prefix through S"
    else:
        tools.resolve_views = lambda seq: capture_view_resolver({}, seq)
        message = "view horizon is not committed"
    with pytest.raises(ValueError) as error:
        await tools.call("status", "{}")
    assert type(error.value) is ValueError and str(error.value) == message
    fallback.assert_not_called()
    assert not tools.status_seen and tools.view_S is None


@pytest.mark.anyio
async def test_production_dispatch_refuses_without_status_and_emits_no_step() -> None:
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
@pytest.mark.parametrize("arguments", VALID)
@pytest.mark.parametrize("correct", [False, True])
async def test_status_view_finish_keeps_budgets_without_view_step(
    arguments: dict, correct: bool
) -> None:
    publish = AsyncMock()
    tools, _ = setup_tools(
        status=lambda: (5, "status"), resolve_views=lambda _: lambda _: True, publish_view=publish
    )
    turns = [turn(call("status"))]
    if correct:
        turns.append(turn(call("show_view", **(arguments | {"facts": "invented"}))))
    turns += [turn(call("show_view", **arguments)), turn(call("finish", reply="answer"))]
    model = scripted(turns)
    run, _, step = lead(model, tools)
    end = await run.run()
    assert end.reply and end.reply.text == "answer" and end.reply.cut is None
    assert run.turn_steps == run.turns == 3 + correct and run.failures == 0
    publish.assert_awaited_once()
    assert publish.await_args is not None
    assert publish.await_args.args[0].view.kind == arguments["kind"]
    step.assert_awaited_once_with(None)  # Only status; neither view outcome produces a step.
    assert f"Shown {arguments['kind']} view through event 5." in str(model.requests)
    if correct:
        assert "View refused." in str(model.requests)
    # A later human turn cannot reuse the previous status eligibility.
    run.make_client = lambda: scripted(
        [turn(call("show_view", **arguments)), {"text": "No fresh status", "usage": {}}]
    )
    await run.run("Show it again", 9)
    assert not tools.status_seen and tools.view_S is None
    assert run.turn_steps == 1 and run.failures == 1
    assert publish.await_count == step.await_count == 1


def test_advertised_view_is_one_closed_reference_object() -> None:
    schema = next(
        cast(dict[str, Any], item["function"])
        for item in LEAD_TOOL_SCHEMAS
        if cast(dict[str, Any], item["function"])["name"] == "show_view"
    )
    params = schema["parameters"]
    assert params["type"] == "object" and params["additionalProperties"] is False
    assert params["required"] == ["kind"] and "oneOf" not in json.dumps(schema)
    assert set(params["properties"]) == {
        "kind",
        "agent_id",
        "agent_ids",
        "paths",
        "files",
        "change_id",
        "event_seq",
        "caption",
    }
    assert params["properties"]["kind"]["enum"] == [
        "highlight",
        "files",
        "behavior",
        "moment",
        "preview",
    ]
    assert LEAD_PROMPT.endswith(
        "Summarize completed plans for the person. "
        "Approval waiting is a notice, never permission to act."
    )
    assert (
        "Use show_view only after status, with references from its eligible inventory;"
        in LEAD_PROMPT
    )


@pytest.mark.anyio
@pytest.mark.parametrize("accepted", [False, True])
@pytest.mark.parametrize("budget", ["tool_calls", "model_calls"])
async def test_view_dispatch_preserves_per_turn_cut(accepted: bool, budget: str) -> None:
    from oversight.agents import LEAD_LIMITS

    publish = AsyncMock()
    tools, _ = setup_tools(
        status=lambda: (5, "status"),
        resolve_views=lambda _: lambda _: accepted,
        publish_view=publish,
    )
    model = scripted([turn(call("status")), turn(call("show_view", **VALID[3]))])
    run, _, step = lead(model, tools, limits=LEAD_LIMITS.model_copy(update={budget: 2}))
    end = await run.run()
    assert end.reply and end.reply.cut == budget
    assert run.steps == run.turn_steps == run.turns == 2
    assert run.failures == (0 if accepted else 1)
    assert publish.await_count == int(accepted) and step.await_count == 1


@pytest.mark.anyio
@pytest.mark.parametrize("accepted", [False, True])
async def test_view_dispatch_preserves_repetition_brake(accepted: bool) -> None:
    publish = AsyncMock()
    tools, _ = setup_tools(
        status=lambda: (5, "status"),
        resolve_views=lambda _: lambda _: accepted,
        publish_view=publish,
    )
    model = scripted([turn(call("status")), turn(*[call("show_view", **VALID[3])] * 4)])
    run, _, step = lead(model, tools)
    end = await run.run()
    assert end.state == "halted" and end.detail == (
        "Stopped: the same call gave the same result 4 times."
    )
    assert run.steps == 5 and publish.await_count == 4 * accepted and step.await_count == 1


@pytest.mark.anyio
async def test_view_dispatch_preserves_consecutive_failure_brake() -> None:
    publish = AsyncMock()
    tools, _ = setup_tools(
        status=lambda: (5, "status"),
        resolve_views=lambda _: lambda _: False,
        publish_view=publish,
    )
    model = scripted(
        [
            turn(call("status")),
            turn(*[call("show_view", **VALID[3], caption=f"Request {i}") for i in range(6)]),
        ]
    )
    run, _, step = lead(model, tools)
    end = await run.run()
    assert end.state == "halted" and end.detail == "Stopped after 6 failures in a row."
    assert run.steps == 7 and run.failures == 6
    publish.assert_not_awaited()
    step.assert_awaited_once_with(None)


def test_preview_identity_constraints_and_person_authority() -> None:
    schema = next(
        item["function"] for item in LEAD_TOOL_SCHEMAS if item["function"]["name"] == "show_view"
    )
    props = schema["parameters"]["properties"]
    assert props["agent_id"] == props["agent_ids"]["items"]
    parsed = parse_view(json.dumps(VALID[-1] | {"caption": "c" * 160}), "agent-9", 12, str)
    assert parsed.agent_id == "agent-9" and parsed.view.kind == "preview"
    assert parsed.view.agent_id == "agent-1" and len(parsed.caption or "") == 160
    for caption in ("", " ", "c" * 161):
        with pytest.raises(ViewRefusal):
            parse_view(json.dumps(VALID[-1] | {"caption": caption}), "agent-9", 12, str)
    for wording in (
        "eligible preview inventory",
        "asks to see or try the app",
        "never replaces review or Approve",
        "Only the person opens it",
        "cannot start, stop or open previews",
        "160-character limit",
    ):
        assert wording in LEAD_PROMPT
