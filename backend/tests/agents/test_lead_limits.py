"""Per-turn budgets cut replies; failures and repetition remain terminal."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from backend.tests.agents.lead_helpers import call, lead, plan, scripted, setup_tools, turn

from oversight.agents import LEAD_LIMITS

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("cut", ["tool_calls", "model_calls", "time", "spend_brake"])
@pytest.mark.parametrize("text", [None, "x" * 5000, "Bearer secret-secret"])
async def test_each_cut_returns_one_nullable_bounded_reply(cut: str, text: str | None) -> None:
    tools, _ = setup_tools()
    model = scripted([dict(text=text, tool_calls=[call("graph")], usage={})])
    limits = replace(LEAD_LIMITS, tool_calls=1 if cut == "tool_calls" else 12, model_calls=1)
    run, _, _ = lead(model, tools, limits=limits, mask=lambda s: s.replace("secret", "MASKED"))
    if cut == "spend_brake":
        run.limits = LEAD_LIMITS
        run.admit = lambda: "brake" if run.turns else None
    if cut == "time":
        run.limits = replace(LEAD_LIMITS, wall_clock_s=0.01)
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


async def test_counters_reset_steps_accumulate_and_tools_do_not_cross_turns() -> None:
    tools, _ = setup_tools()
    model = scripted([turn(call("graph"))])
    run, _, _ = lead(model, tools, limits=replace(LEAD_LIMITS, tool_calls=1))
    for i in range(5):
        model = scripted([turn(call("graph"))])
        run.make_client = lambda model=model: model
        end = await run.run(None if i == 0 else "question", None if i == 0 else i)
        assert end.state == "idle" and end.reply and end.reply.cut == "tool_calls"
        assert run.steps == i + 1 and run.turn_steps == run.turns == 1
        assert len(run.seen) == 1 and run.failures == 0
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
    run, _, _ = lead(model, tools)
    end = await run.run()
    assert end.state == "halted" and end.reply is None
