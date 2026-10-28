"""Unusable completions and invalid tools recover without executing partial work."""

import json
from typing import cast

import pytest
from agent_helpers import Recorder, make_run

from oversight.agents import ScriptedModel, parse_script
from oversight.workspace import Tree, Workspace
from oversight_contracts.events import AgentStep, UsageTick

pytestmark = pytest.mark.anyio
Opened = tuple[Workspace, Tree]


def model(*turns: dict[str, object]) -> ScriptedModel:
    return ScriptedModel(parse_script(json.dumps({"task": "t", "turns": turns})))


FINISH: dict[str, object] = {
    "tool_calls": [{"name": "finish", "arguments": {"summary": "Done"}}],
    "usage": {"input_tokens": 20, "output_tokens": 2},
}
TRUNCATED: dict[str, object] = {
    "finish_reason": "length",
    "tool_calls": [{"name": "finish", "arguments": {"summary": "Must not execute"}}],
    "usage": {"input_tokens": 10, "output_tokens": 4},
}
EMPTY: dict[str, object] = {"finish_reason": "stop", "text": "  "}


@pytest.mark.parametrize("arguments", [{"summary": "Must not execute"}, '{"summary":'])
async def test_length_discards_all_calls_and_accounts_once(
    small_tree: Opened, arguments: object
) -> None:
    truncated = TRUNCATED | {"tool_calls": [{"name": "finish", "arguments": arguments}]}
    fake = model(truncated, FINISH)
    recorder = Recorder()
    end = await make_run(small_tree, fake, recorder).run()
    assert end.state == "ready_for_review" and end.steps == 1 and end.summary == "Done"
    ticks = cast(list[UsageTick], recorder.of("UsageTick"))
    assert [(t.input_tokens, t.output_tokens) for t in ticks] == [(10, 4), (20, 2)]
    assert "Regenerate a shorter reply" in str(fake.requests[1][-1]["content"])
    assert not any(m["role"] == "assistant" for m in fake.requests[1])


async def test_empty_stop_continues_once_and_keeps_unknown_usage(small_tree: Opened) -> None:
    fake = model(EMPTY, FINISH)
    recorder = Recorder()
    end = await make_run(small_tree, fake, recorder).run()
    assert end.state == "ready_for_review" and end.steps == 1
    ticks = cast(list[UsageTick], recorder.of("UsageTick"))
    assert len(ticks) == 2 and ticks[0].input_tokens is None and ticks[0].cost_usd is None
    assert "Your reply was empty" in str(fake.requests[1][-1]["content"])


@pytest.mark.parametrize(
    "unusable, detail", [(TRUNCATED, "truncated again"), (EMPTY, "another empty stop")]
)
async def test_recovery_is_bounded(
    small_tree: Opened, unusable: dict[str, object], detail: str
) -> None:
    fake = model(unusable, unusable, FINISH)
    recorder = Recorder()
    end = await make_run(small_tree, fake, recorder).run()
    assert end.state == "halted" and end.steps == 0 and detail in (end.detail or "")
    assert len(fake.requests) == 2 and len(recorder.of("UsageTick")) == 2


async def test_text_with_tools_is_preserved(small_tree: Opened) -> None:
    fake = model(FINISH | {"text": "Ready now.", "finish_reason": "stop"})
    end = await make_run(small_tree, fake, Recorder()).run()
    assert end.state == "ready_for_review" and end.steps == 1


@pytest.mark.parametrize(
    "name, args",
    [
        ("list_files", {"path": "shop"}),
        ("run_tests", {"timeout": 999}),
        ("read_file", {"path": "shop/mod.py", "extra": True}),
        ("search", {"query": "value", "extra": True}),
        (
            "edit_file",
            {"path": "shop/mod.py", "old_text": "return 1", "new_text": "return 2", "extra": True},
        ),
        ("finish", {"summary": "ok", "extra": True}),
        ("finish", {"summary": " \n "}),
    ],
)
async def test_invalid_arguments_return_to_model_then_recover(
    small_tree: Opened, name: str, args: dict[str, object]
) -> None:
    before = (small_tree[1].root / "shop/mod.py").read_text()
    fake = model({"tool_calls": [{"name": name, "arguments": args}]}, FINISH)
    recorder = Recorder()
    end = await make_run(small_tree, fake, recorder).run()
    assert end.state == "ready_for_review" and end.steps == 2
    steps = cast(list[AgentStep], recorder.of("AgentStep"))
    assert steps[0].result == "error" and steps[1].result == "ok"
    assert "expected " + name + "(" in str(fake.requests[1][-1]["content"])
    assert (small_tree[1].root / "shop/mod.py").read_text() == before
    assert not recorder.of("AgentFocus") and not recorder.of("sink.file_edited")
