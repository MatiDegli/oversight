"""Closed lead tools and bounded scripted coordination, without model transport."""

import json
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from backend.tests.agents.lead_helpers import call, lead, plan, scripted, setup_tools, turn

from oversight.agents import (
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
from oversight_contracts.plans import PlanZone

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


async def test_exhausted_tool_dispatch_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from oversight.agents import _lead_tools

    tools, _ = setup_tools()
    outcome = await tools.call("finish", '{"reply":"Ready."}')
    assert outcome.ok and outcome.finish and outcome.content == "Ready."
    monkeypatch.setattr(_lead_tools._Finish, "model_validate_json", lambda _: _lead_tools._Empty())
    with pytest.raises(RuntimeError, match=r"^Unknown lead tool outcome\.$"):
        await tools.call("finish", "{}")


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
        "set_limits",
    ]
    for name in names:
        outcome = await tools.call(name, "{}")
        assert not outcome.ok and outcome.content == (
            f"Unknown tool {name}. The lead's tools are graph, list_files, read_file, "
            "queue, request_plan, finish and status."
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
        "status",
        "queue",
        "request_plan",
        "finish",
    ]
    serialized = json.dumps(LEAD_TOOL_SCHEMAS)
    for field in ("zone", "graph_version", "agent", "slot", "model", "started_by", "decision"):
        assert f'"{field}"' not in serialized
    props = schemas[5]["function"]["parameters"]["properties"]
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
            "The plan task must be nonblank and at most 2,000 characters "
            "(OVERSIGHT_LEAD_MAX_TASK_CHARS).",
        ),
        (
            plan(task="x" * 2001),
            "The plan task must be nonblank and at most 2,000 characters "
            "(OVERSIGHT_LEAD_MAX_TASK_CHARS).",
        ),
        (plan(subtasks=[]), "A plan has 1 to 6 subtasks (OVERSIGHT_LEAD_MAX_SUBTASKS)."),
        (
            plan(subtasks=[dict(task="x")] * 7),
            "A plan has 1 to 6 subtasks (OVERSIGHT_LEAD_MAX_SUBTASKS).",
        ),
        (
            plan(subtasks=[dict(task=" ")]),
            "Subtask 0: the task must be nonblank and at most 2,000 characters "
            "(OVERSIGHT_LEAD_MAX_TASK_CHARS).",
        ),
        (
            plan(subtasks=[dict(task="x" * 2001)]),
            "Subtask 0: the task must be nonblank and at most 2,000 characters "
            "(OVERSIGHT_LEAD_MAX_TASK_CHARS).",
        ),
        (
            plan(subtasks=[dict(task="x", depends_on=[0])]),
            "Subtask dependencies: dependencies must be unique and exclude self.",
        ),
        (
            plan(subtasks=[dict(task="x")] * 3),
            "At most 2 subtasks may have no depends_on: this run offers 2 agent slots "
            "(OVERSIGHT_MAX_AGENTS).",
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
    ).content == "This lead already requested 3 plans (OVERSIGHT_LEAD_MAX_PLANS); call finish."
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
        ("request_plan", '{"task":"x","subtasks":[],"limits":{"subtasks":99}}'),
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
    if case == "same":
        turns = [turn(call("graph"))] * 5
    elif case == "failures":
        turns = [turn(*(call(f"unknown-{i}") for i in range(8)))]
    else:
        turns = [
            turn(
                *(call("request_plan", **plan()) for _ in range(7)), call("finish", reply="Ready.")
            )
        ]
    run, _, _ = lead(scripted(turns), tools)
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


def test_server_notice_paragraph_moves_prompt_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    from oversight.agents import LEAD_LIMITS

    versions = [_prompts.prompt_version(pack, lead_limits=LEAD_LIMITS) for pack in (False, True)]
    monkeypatch.setattr(_prompts, "SERVER_NOTICE_PROMPT", "Changed server notice instructions")
    assert all(
        _prompts.prompt_version(pack, lead_limits=LEAD_LIMITS) != versions[index]
        for index, pack in enumerate((False, True))
    )


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


async def test_scripted_thread_retains_exchanges_and_reads_fresh_status() -> None:
    tools, _ = setup_tools(status=lambda: (10, "Committed status data"))
    model = ScriptedModel(load_script(FIXTURES / "lead-thread.json"))
    run, _, _ = lead(model, tools)
    opening = await run.run()
    assert opening.reply and opening.reply.through_seq is None
    following = await run.run("Check again", 8)
    assert following.reply and following.reply.answers_seq == 8
    assert following.reply.through_seq == 10
    assert "I can explain" in str(model.requests[1]) and "Check again" in str(model.requests[1])
    assert not tools.plan_ids


@pytest.mark.parametrize("pack", [False, True])
def test_every_snapshot_value_moves_prompt_version(pack: bool) -> None:
    from oversight.agents import (
        LEAD_LIMITS,
        build_lead_prompt,
        build_starting_detail,
        prompt_version,
    )

    large_clock = LEAD_LIMITS.model_copy(update={"wall_clock_s": 10000000001.0})
    assert "10000000001 seconds" in build_lead_prompt(large_clock)
    assert "10000000001 s" in build_starting_detail(large_clock)
    baseline = prompt_version(pack, 8000, lead_limits=LEAD_LIMITS)
    assert baseline == prompt_version(pack, 8000, lead_limits=LEAD_LIMITS.model_copy())
    assert baseline == prompt_version(
        pack, 8000, lead_limits=LEAD_LIMITS.model_copy(update={"wall_clock_s": 300})
    )
    assert prompt_version(pack) == prompt_version(pack, lead_limits=LEAD_LIMITS)
    assert build_starting_detail(LEAD_LIMITS) == (
        "Limits: 12 tool calls, 18 model calls, 300 s, 3 plans of up to 6 subtasks, "
        "2,000 characters per task."
    )
    for field, value in LEAD_LIMITS.model_dump().items():
        changed = LEAD_LIMITS.model_copy(update={field: value + 1})
        assert prompt_version(pack, 8000, lead_limits=changed) != baseline
        assert build_lead_prompt(changed) != build_lead_prompt(LEAD_LIMITS)
        if field != "turns":
            assert build_starting_detail(changed) != build_starting_detail(LEAD_LIMITS)


@pytest.mark.parametrize("count", [1, 5])
async def test_configured_plan_count_is_thread_wide(count: int) -> None:
    from oversight.agents import LEAD_LIMITS

    limits = LEAD_LIMITS.model_copy(update={"plans": count})
    tools, propose = setup_tools(limits=limits, propose=AsyncMock(return_value="plan-n"))
    run, _, _ = lead(scripted([]), tools, limits=limits)
    model = scripted([])
    for index in range(count + 1):
        model = scripted([turn(call("request_plan", **plan())), dict(text="Ready", usage={})])
        run.make_client = lambda model=model: model
        assert (await run.run(None if index == 0 else "Another")).state == "idle"
    assert propose.await_count == count
    assert f"{count} plans (OVERSIGHT_LEAD_MAX_PLANS)" in str(model.requests)


@pytest.mark.parametrize("chars", [1, 3000])
@pytest.mark.parametrize("target", ["plan", "subtask"])
async def test_task_length_schema_prompt_and_refusal_agree(chars, target) -> None:
    from oversight.agents import LEAD_LIMITS, build_lead_prompt, build_lead_tool_schemas

    limits = LEAD_LIMITS.model_copy(update={"task_chars": chars})
    tools, propose = setup_tools(limits=limits)
    schemas: Any = build_lead_tool_schemas(limits)
    props = schemas[5]["function"]["parameters"]["properties"]
    assert props["task"]["maxLength"] == chars
    assert props["subtasks"]["items"]["properties"]["task"]["maxLength"] == chars
    assert f"at most {chars:,} characters" in build_lead_prompt(limits)
    for extra in (0, 1):
        text = "x" * (chars + extra)
        payload = plan(
            task=text if target == "plan" else "x",
            subtasks=[dict(task=text if target == "subtask" else "x")],
        )
        result = await tools.call("request_plan", json.dumps(payload))
        assert result.ok == (extra == 0)
        if extra:
            assert f"{chars:,} characters (OVERSIGHT_LEAD_MAX_TASK_CHARS)" in result.content
    assert propose.await_count == 1


@pytest.mark.parametrize("count", [1, 8])
async def test_subtask_bounds_and_graphs_validate_before_side_effects(count: int) -> None:
    from oversight.agents import LEAD_LIMITS, build_lead_tool_schemas

    limits = LEAD_LIMITS.model_copy(update={"subtasks": count})
    zone = Mock()
    tools, propose = setup_tools(limits=limits, zone=zone)
    zone.return_value = PlanZone(node_ids=[], paths=[])
    chain = [dict(task="x", depends_on=[i - 1] if i else []) for i in range(count)]
    schemas: Any = build_lead_tool_schemas(limits)
    props = schemas[5]["function"]["parameters"]["properties"]["subtasks"]
    assert props["maxItems"] == count
    assert props["items"]["properties"]["depends_on"]["items"]["maximum"] == count - 1
    assert (await tools.call("request_plan", json.dumps(plan(subtasks=chain)))).ok
    assert propose.await_count == 1
    propose.reset_mock()
    zone.reset_mock()
    invalid = [
        [*chain, dict(task="overflow")],
        [dict(task="x", depends_on=[count])],
        [dict(task="x", depends_on=[0])],
    ]
    if count == 8:
        invalid += [
            [dict(task="x", depends_on=[1]), dict(task="x", depends_on=[0])],
            [dict(task="x", depends_on=[1, 1]), dict(task="x")],
            [dict(task="x", depends_on=[2]), dict(task="x")],
            [dict(task="x", depends_on=[-1])],
            [dict(task="x")] * 3,
        ]
        defaults, _ = setup_tools()
        refusal = await defaults.call("request_plan", json.dumps(plan(subtasks=chain)))
        assert not refusal.ok and "6 subtasks (OVERSIGHT_LEAD_MAX_SUBTASKS)" in refusal.content
    for tasks in invalid:
        assert not (await tools.call("request_plan", json.dumps(plan(subtasks=tasks)))).ok
    propose.assert_not_awaited()
    zone.assert_not_called()
