"""Closed lead tools and bounded scripted coordination, without model transport."""

import json
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from backend.tests.agents.lead_helpers import call, lead, plan, scripted, setup_tools, turn

from oversight.agents import (
    LEAD_LIMITS,
    LEAD_TOOL_SCHEMAS,
    ModelAPIError,
    ScriptedModel,
    Usage,
    _prompts,
    load_script,
)
from oversight.agents._lead_tools import LeadProposalError
from oversight.agents.client import CALL_SCOPE
from oversight.settings import REPO_ROOT
from oversight.workspace import FileTooLarge, InvalidPath

pytestmark = pytest.mark.anyio
FIXTURES = REPO_ROOT / "fixtures/fake-model"


async def test_a_lead_reads_requests_two_plans_and_finishes() -> None:
    tools, propose = setup_tools()
    model = ScriptedModel(load_script(FIXTURES / "lead-two-plans.json"))
    run, record, step = lead(model, tools)
    end = await run.run()
    assert (end.state, end.detail) == ("idle", "Requested plan-1, plan-2.")
    assert run.steps == 7 and record.await_count == 3 and propose.await_count == 2
    assert [c.args[0] for c in step.await_args_list] == [None] * 4 + ["plan-1", "plan-2"]
    assert CALL_SCOPE.get() is None
    assert "    1| line 1" in str(model.requests)


async def test_decision_tools_are_unknown_and_change_nothing() -> None:
    tools, propose = setup_tools()
    names = [
        "approve",
        "reject",
        "request_changes",
        "enqueue",
        "queue_task",
        "start_agent",
        "start_plan",
        "merge",
    ]
    for name in names:
        outcome = await tools.call(name, "{}")
        assert not outcome.ok and outcome.content == (
            f"Unknown tool {name}. The lead's tools are graph, list_files, read_file, "
            "queue, request_plan and finish."
        )
    run, _, _ = lead(ScriptedModel(load_script(FIXTURES / "lead-forbidden-tools.json")), tools)
    end = await run.run()
    assert (end.state, end.detail) == ("halted", "Stopped after 6 failures in a row.")
    assert run.steps == 6 and not tools.plan_ids
    propose.assert_not_awaited()


async def test_a_bad_plan_returns_as_a_tool_error_and_the_fixed_plan_is_accepted() -> None:
    tools, propose = setup_tools()
    model = ScriptedModel(load_script(FIXTURES / "lead-bad-plan.json"))
    run, _, _ = lead(model, tools)
    assert (await run.run()).detail == "Requested plan-1."
    assert propose.await_count == 1
    assert "Subtask dependencies: subtask dependencies must be acyclic." in str(model.requests)


def test_lead_tool_schemas_offer_no_zone_or_decision_field() -> None:
    schemas: Any = LEAD_TOOL_SCHEMAS
    assert [s["function"]["name"] for s in schemas] == [
        "graph",
        "list_files",
        "read_file",
        "queue",
        "request_plan",
        "finish",
    ]
    serialized = json.dumps(LEAD_TOOL_SCHEMAS)
    for field in ("zone", "graph_version", "agent", "slot", "model", "started_by", "decision"):
        assert f'"{field}"' not in serialized
    props = schemas[4]["function"]["parameters"]["properties"]
    assert props["subtasks"]["maxItems"] == 6


async def test_read_file_numbers_main_lines_and_refuses_bad_ranges() -> None:
    tools, _ = setup_tools()
    outcome = await tools.call(
        "read_file", json.dumps(dict(path="shop/pricing.py", start_line=2, end_line=3))
    )
    assert (
        outcome.content == "shop/pricing.py: lines 2-3 of 450\n    2| line 2\n    3| line 3\n"
        "Read more with start_line=4."
    )
    assert isinstance(tools.read, AsyncMock)
    tools.read.assert_awaited_once_with("shop/pricing.py")
    for first, last in ((3, 2), (451, 451), (1, 451)):
        outcome = await tools.call(
            "read_file", json.dumps(dict(path="shop/pricing.py", start_line=first, end_line=last))
        )
        assert (
            not outcome.ok
            and outcome.content == "start_line and end_line must be within 1-450, in order."
        )
    outcome = await tools.call("read_file", '{"path":"shop/pricing.py"}')
    assert "lines 1-400 of 450" in outcome.content and outcome.content.endswith("start_line=401.")


@pytest.mark.parametrize(
    "error,message",
    [
        (FileNotFoundError(), "There is no file 'shop/pricing.py' on main."),
        (InvalidPath("private"), "Use a path relative to the repository root."),
        (FileTooLarge("private"), "The file is too large to read."),
    ],
)
async def test_read_errors_are_templates(error: Exception, message: str) -> None:
    tools, _ = setup_tools(read=AsyncMock(side_effect=error))
    assert (await tools.call("read_file", '{"path":"shop/pricing.py"}')).content == message


@pytest.mark.parametrize(
    "payload,message",
    [
        (
            plan(task=" ", subtasks=[]),
            "The plan task must be nonblank and at most 2,000 characters.",
        ),
        (plan(task="x" * 2001), "The plan task must be nonblank and at most 2,000 characters."),
        (plan(subtasks=[]), "A plan has 1 to 6 subtasks."),
        (plan(subtasks=[dict(task="x")] * 7), "A plan has 1 to 6 subtasks."),
        (
            plan(subtasks=[dict(task=" ")]),
            "Subtask 0: the task must be nonblank and at most 2,000 characters.",
        ),
        (
            plan(subtasks=[dict(task="x" * 2001)]),
            "Subtask 0: the task must be nonblank and at most 2,000 characters.",
        ),
        (
            plan(subtasks=[dict(task="x", depends_on=[0])]),
            "Subtask dependencies: dependencies must be unique and exclude self.",
        ),
        (
            plan(subtasks=[dict(task="x")] * 3),
            "At most 2 subtasks may have no depends_on: this run offers 2 agent slots.",
        ),
    ],
)
async def test_request_plan_checks_in_order_before_proposing(payload: Any, message: str) -> None:
    zone = AsyncMock()
    tools, propose = setup_tools(zone=zone)
    outcome = await tools.call("request_plan", json.dumps(payload))
    assert not outcome.ok and outcome.content == message
    propose.assert_not_awaited()
    zone.assert_not_called()
    tools.plan_ids[:] = ["plan-1", "plan-2", "plan-3"]
    assert (
        await tools.call("request_plan", json.dumps(payload))
    ).content == "This lead already requested 3 plans; call finish."
    propose.assert_not_awaited()


async def test_request_plan_fills_the_whole_graph_zone() -> None:
    tools, propose = setup_tools()
    result = await tools.call("request_plan", json.dumps(plan()))
    assert result.ok and result.plan_id == "plan-1"
    assert propose.await_args is not None
    assert propose.await_args.args[1][0].zone == tools.zone()
    assert propose.await_args.args[0] == "Document pricing"


@pytest.mark.parametrize(
    "name,args",
    [
        ("graph", "bad"),
        ("graph", '{"agent":"A"}'),
        ("read_file", '{"path":"x", "start_line":true}'),
        ("request_plan", '{"task":"x","subtasks":[{"task":"y","zone":{}}]}'),
    ],
)
async def test_arguments_are_strict(name: str, args: str) -> None:
    tools, propose = setup_tools()
    outcome = await tools.call(name, args)
    assert not outcome.ok and outcome.content.startswith(f"Invalid arguments for {name}: ")
    propose.assert_not_awaited()


async def test_only_server_proposal_refusals_become_tool_errors() -> None:
    tools, _ = setup_tools(propose=AsyncMock(side_effect=LeadProposalError("the run is closing")))
    outcome = await tools.call("request_plan", json.dumps(plan()))
    assert not outcome.ok and outcome.content == "the run is closing" and not tools.plan_ids
    tools.propose = AsyncMock(side_effect=RuntimeError("unexpected"))
    with pytest.raises(RuntimeError, match="unexpected"):
        await tools.call("request_plan", json.dumps(plan()))


@pytest.mark.parametrize(
    "case,expected",
    [
        ("same", "Stopped: the same call gave the same result 4 times."),
        ("failures", "Stopped after 6 failures in a row."),
        (
            "fourth",
            "Stopped: the same call gave the same result 4 times. "
            "Requested plan-1, plan-2, plan-3.",
        ),
    ],
)
async def test_each_stop_rule_halts_the_lead(case: str, expected: str) -> None:
    tools, _ = setup_tools()
    limits = LEAD_LIMITS
    delay = 0.0
    if case == "same":
        turns = [turn(call("graph"))] * 5
    elif case == "failures":
        turns = [turn(*(call(f"unknown-{i}") for i in range(8)))]
    elif case == "tools":
        turns = [
            turn(
                *(
                    call("read_file", path="shop/pricing.py", start_line=i, end_line=i)
                    for i in range(1, 14)
                )
            )
        ]
    elif case == "models":
        turns = [
            t
            for _ in range(3)
            for t in [turn(call("graph")), *[dict(text="Continue", usage={})] * 5]
        ]
    elif case == "clock":
        turns, limits, delay = (
            [turn(call("finish", reply="Ready."))],
            replace(LEAD_LIMITS, wall_clock_s=0.001),
            0.05,
        )
    else:
        turns = [
            turn(
                *(call("request_plan", **plan()) for _ in range(7)), call("finish", reply="Ready.")
            )
        ]
    run, _, _ = lead(scripted(turns, delay), tools, limits=limits)
    end = await run.run()
    assert (end.state, end.detail) == ("halted", expected)
    assert CALL_SCOPE.get() is None


async def test_the_brake_halts_before_the_next_model_call() -> None:
    tools, _ = setup_tools()
    model = scripted([turn(call("request_plan", **plan())), turn(call("finish", reply="Ready."))])
    run, record, _ = lead(model, tools, admit=lambda: "blocked" if tools.plan_ids else None)
    end = await run.run()
    assert end.state == "idle" and end.reply is not None and end.reply.cut == "spend_brake"
    assert len(model.requests) == record.await_count == 1


async def test_unknown_usage_reaches_record_unchanged() -> None:
    tools, _ = setup_tools()
    model = scripted([dict(text=None, tool_calls=[call("finish", reply="Ready.")])])
    run, record, _ = lead(model, tools)
    assert (await run.run()).state == "idle"
    assert record.await_args is not None
    assert record.await_args.args[0].usage == Usage()


async def test_no_detail_carries_model_text() -> None:
    tools, _ = setup_tools()
    sentinel = "PRIVATE_MODEL_SENTINEL"
    model = scripted(
        [
            dict(text=sentinel, tool_calls=[call("unknown", task=sentinel)], usage={}),
            turn(call("request_plan", **plan(task=sentinel))),
            turn(call("finish", reply="Ready.")),
        ]
    )
    run, _, step = lead(model, tools)
    end = await run.run()
    assert sentinel not in end.detail
    assert [c.args[0] for c in step.await_args_list] == [None, "plan-1"]


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("api", "The model API failed after its retries."),
        ("length", "The model reply was truncated again after regeneration."),
        ("empty", "The model returned another empty stop after continuation."),
    ],
)
async def test_reply_failures_use_templates(kind: str, expected: str) -> None:
    tools, _ = setup_tools()
    model = scripted(
        [turn(finish_reason="length")] * 2 if kind == "length" else [turn(finish_reason="stop")] * 2
    )
    if kind == "api":
        model.complete = AsyncMock(side_effect=ModelAPIError("private provider text"))
    run, _, _ = lead(model, tools)
    assert (await run.run()).detail == expected


def test_the_lead_prompt_and_schemas_move_both_prompt_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = [_prompts.prompt_version(pack) for pack in (False, True)]
    with monkeypatch.context() as patch:
        patch.setattr(_prompts, "LEAD_PROMPT", _prompts.LEAD_PROMPT + " Changed.")
        assert all(
            _prompts.prompt_version(pack) != before[i] for i, pack in enumerate((False, True))
        )
    monkeypatch.setattr(_prompts, "LEAD_TOOL_SCHEMAS", [])
    assert all(_prompts.prompt_version(pack) != before[i] for i, pack in enumerate((False, True)))
