"""Per-turn budgets cut replies; failures and repetition remain terminal."""

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from backend.tests.agents.lead_helpers import call, lead, plan, scripted, setup_tools, turn

from oversight.agents import LEAD_LIMITS, _prompts

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("cut", ["tool_calls", "model_calls", "time", "spend_brake"])
@pytest.mark.parametrize("text", [None, "x" * 5000, "Bearer secret-secret"])
async def test_each_cut_returns_one_nullable_bounded_reply(cut: str, text: str | None) -> None:
    tools, _ = setup_tools()
    model = scripted([dict(text=text, tool_calls=[call("graph")], usage={})])
    limits = LEAD_LIMITS.model_copy(
        update={"tool_calls": 1 if cut == "tool_calls" else 12, "model_calls": 1}
    )
    run, _, _ = lead(model, tools, limits=limits, mask=lambda s: s.replace("secret", "MASKED"))
    if cut == "spend_brake":
        run.limits = LEAD_LIMITS
        run.admit = lambda: "brake" if run.turns else None
    if cut == "time":
        run.limits = LEAD_LIMITS.model_copy(update={"wall_clock_s": 0.01})
        gate = asyncio.Event()
        original = model.complete

        async def complete(*args):
            if run.turns == 1:
                return await original(*args)
            await gate.wait()
            raise AssertionError("unreleased model gate")

        model.complete = AsyncMock(side_effect=complete)
    end = await run.run()
    assert end.state == "idle" and end.reply is not None and end.reply.cut == cut
    assert end.reply.answers_seq is None and end.reply.through_seq is None
    if text is None:
        assert end.reply.text is None
    else:
        assert end.reply.text and len(end.reply.text) <= 4096
        assert "secret" not in end.reply.text


@pytest.mark.parametrize("name", ["graph", "unknown"])
async def test_counters_reset_steps_accumulate_and_tools_do_not_cross_turns(name: str) -> None:
    tools, _ = setup_tools()
    model = scripted([turn(call("graph"))])
    run, _, _ = lead(model, tools, limits=LEAD_LIMITS.model_copy(update={"tool_calls": 1}))
    for i in range(7):
        model = scripted([turn(call(name))])
        run.make_client = lambda model=model: model
        end = await run.run(None if i == 0 else "question", None if i == 0 else i)
        assert end.state == "idle" and end.reply and end.reply.cut == "tool_calls"
        assert run.steps == i + 1 and run.turn_steps == run.turns == 1
        assert len(run.seen) == 1 and run.failures == (name == "unknown")
        assert all(m["role"] != "tool" for m in model.requests[0])


async def test_three_plans_survive_turns_fourth_errors_then_replies() -> None:
    tools, propose = setup_tools()
    run, _, _ = lead(scripted([]), tools)
    model = scripted([])
    for i in range(4):
        model = scripted([turn(call("request_plan", **plan())), dict(text="Ready", usage={})])
        run.make_client = lambda model=model: model
        end = await run.run(None if i == 0 else "another plan", None if i == 0 else i)
        assert end.state == "idle" and end.reply and end.reply.text == "Ready"
    assert propose.await_count == 3 and len(tools.plan_ids) == 3
    assert "already requested 3 plans" in str(model.requests)


@pytest.mark.parametrize("kind", ["failures", "same", "api"])
async def test_terminal_rules_never_reply_even_with_text(kind: str) -> None:
    tools, _ = setup_tools()
    calls = [call("graph")] * 4 if kind == "same" else [call(f"unknown-{i}") for i in range(6)]
    model = scripted([dict(text="Do not publish", tool_calls=calls, usage={})])
    if kind == "api":
        from oversight.agents import ModelAPIError

        model.complete = AsyncMock(side_effect=ModelAPIError("private"))
    run, _, _ = lead(
        model,
        tools,
        limits=LEAD_LIMITS.model_copy(
            update={"model_calls": 1, "tool_calls": 4 if kind == "same" else 6}
        ),
    )
    end = await run.run()
    assert end.state == "halted" and end.reply is None


async def test_reply_horizon_is_maximum_status_read_in_this_turn_only() -> None:
    readings = iter([(4, "first"), (9, "new"), (7, "older")])
    tools, _ = setup_tools(status=lambda: next(readings))
    model = scripted(
        [turn(call("status"), call("status"), call("status")), dict(text="Fact", usage={})]
    )
    run, _, _ = lead(model, tools)
    end = await run.run()
    assert end.reply and end.reply.through_seq == 9
    run.make_client = lambda: scripted([dict(text="No status", usage={})])
    end = await run.run("another question", 10)
    assert end.reply and end.reply.answers_seq == 10 and end.reply.through_seq is None


@pytest.mark.parametrize("budget", ["models", "tools"])
async def test_default_18_model_and_12_tool_limits_are_exact(budget: str) -> None:
    tools, _ = setup_tools()
    if budget == "tools":
        turns = [
            turn(
                *(
                    call("read_file", path="shop/pricing.py", start_line=i, end_line=i)
                    for i in range(1, 14)
                )
            )
        ]
    else:
        turns = [
            t
            for i in range(1, 7)
            for t in [
                turn(call("read_file", path="shop/pricing.py", start_line=i, end_line=i)),
                {"text": None, "usage": {}},
                {"text": " ", "usage": {}},
            ]
        ]
    model = scripted(turns)
    run, _, _ = lead(model, tools)
    end = await run.run()
    assert end.state == "idle" and end.reply and end.reply.text is None
    if budget == "tools":
        assert run.steps == 12 and end.reply.cut == "tool_calls"
    else:
        assert len(model.requests) == 18 and run.steps == 6 and end.reply.cut == "model_calls"


async def test_lead_tool_enforces_default_dependency_index_server_side() -> None:
    tools, propose = setup_tools()
    outcome = await tools.call(
        "request_plan", json.dumps(plan(subtasks=[dict(task="Work", depends_on=[6])]))
    )
    assert not outcome.ok
    assert outcome.content == (
        "A subtask dependency index must be at most 5 (OVERSIGHT_LEAD_MAX_SUBTASKS)."
    )
    propose.assert_not_awaited()


@pytest.mark.parametrize(
    "budget,value", [("tool_calls", 2), ("tool_calls", 14), ("model_calls", 2), ("model_calls", 21)]
)
async def test_injected_cuts_are_exact_and_reset_each_turn(budget: str, value: int) -> None:
    limits = LEAD_LIMITS.model_copy(
        update={budget: value, "tool_calls" if budget == "model_calls" else "model_calls": 100}
    )
    tools, _ = setup_tools(limits=limits)
    run, _, _ = lead(scripted([]), tools, limits=limits)
    for index in range(2):
        model = scripted(
            [
                turn(call("read_file", path="shop/pricing.py", start_line=i, end_line=i))
                for i in range(1, value + 2)
            ]
        )
        run.make_client = lambda model=model: model
        end = await run.run(None if index == 0 else "Again", None if index == 0 else 2)
        assert end.reply and end.reply.cut == budget
        assert run.turn_steps == run.turns == len(model.requests) == value
        assert run.steps == value * (index + 1)


@pytest.mark.parametrize("seconds", [1, 600])
async def test_injected_time_budget_cuts_and_restarts(seconds, monkeypatch) -> None:
    timeout = asyncio.timeout
    deadlines = []

    def accelerated(value):
        deadlines.append(value)
        return timeout(0.001)

    monkeypatch.setattr(asyncio, "timeout", accelerated)
    limits = LEAD_LIMITS.model_copy(update={"wall_clock_s": seconds})
    tools, _ = setup_tools(limits=limits)
    model = scripted([])
    model.complete = AsyncMock(side_effect=lambda *args: None)

    async def wait(*args):
        await asyncio.Event().wait()

    model.complete.side_effect = wait
    run, _, _ = lead(model, tools, limits=limits)
    for index in range(2):
        end = await run.run(None if index == 0 else "Again")
        assert end.reply and end.reply.cut == "time"
        assert run.turns == 1
    assert deadlines == [seconds, seconds]


async def test_thread_builds_derived_policy_once_and_passes_its_snapshot(monkeypatch) -> None:
    from unittest.mock import Mock

    from oversight.agents import _lead, build_lead_prompt, build_lead_tool_schemas

    limits = LEAD_LIMITS.model_copy(update={"tool_calls": 2, "subtasks": 8})
    tools, _ = setup_tools(limits=limits)
    prompt = Mock(wraps=build_lead_prompt)
    schemas = Mock(wraps=build_lead_tool_schemas)
    monkeypatch.setattr(_prompts, "build_lead_prompt", prompt)
    monkeypatch.setattr(_lead, "build_lead_tool_schemas", schemas)
    model = scripted([dict(text="Answer", usage={})] * 2)
    model.complete = AsyncMock(wraps=model.complete)
    run, _, _ = lead(model, tools, limits=limits, history_chars=48000)
    for _ in range(2):
        assert (await run.run()).state == "idle"
    prompt.assert_called_once_with(limits)
    schemas.assert_called_once_with(limits)
    assert run.limits is tools.limits is limits
    assert run.history.history_chars == 48000
    from oversight.agents._prompts import SERVER_NOTICE_PROMPT

    assert (
        model.requests[0][0]["content"] == build_lead_prompt(limits) + "\n\n" + SERVER_NOTICE_PROMPT
    )
    assert all(c.args[1] is run.schemas for c in model.complete.await_args_list)
    other = build_lead_tool_schemas(LEAD_LIMITS)
    instance_schemas: Any = run.schemas
    instance_schemas[5]["function"]["parameters"]["properties"]["subtasks"]["maxItems"] = 99
    assert other == build_lead_tool_schemas(LEAD_LIMITS)
    assert other != run.schemas


@pytest.mark.parametrize(
    "pack,version",
    [(False, "agent-v1-53ab6d25b16f"), (True, "agent-v1-83e8717606d6")],
)
def test_the_lead_prompt_builder_keeps_the_sent_and_hashed_text(pack: bool, version: str) -> None:
    from oversight.agents import build_lead_prompt

    limits = LEAD_LIMITS.model_copy(update={"turns": 7})
    assert _prompts.prompt_version(pack, lead_limits=limits) == version
    assert (
        _prompts.lead_system_prompt(limits)
        == build_lead_prompt(limits) + "\n\n" + _prompts.SERVER_NOTICE_PROMPT
    )
    assert _prompts.lead_system_prompt(LEAD_LIMITS) == _prompts.LEAD_PROMPT


def test_mismatched_loop_and_tool_snapshots_are_rejected() -> None:
    from oversight.agents import LeadRun

    tools, _ = setup_tools()
    with pytest.raises(ValueError, match="same lead limits"):
        LeadRun(
            agent_id="agent-1",
            task="Task",
            make_client=lambda: scripted([]),
            tools=tools,
            record=AsyncMock(),
            publish=AsyncMock(),
            admit=lambda: None,
            on_working=AsyncMock(),
            on_step=AsyncMock(),
            limits=LEAD_LIMITS.model_copy(update={"subtasks": 8}),
        )
