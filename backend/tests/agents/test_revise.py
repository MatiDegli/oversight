"""Revision on a kept run: the history, the budget and the stop rules start over in part."""

import asyncio
import hashlib
import json
from typing import cast

import pytest
from agent_helpers import Recorder, make_run, script

from oversight.agents import PROMPT_VERSION, SYSTEM_PROMPT, ScriptedModel, parse_script
from oversight.agents.loop import REVISION_PROMPT
from oversight.agents.tools import TOOL_SCHEMAS, Toolbox, ToolOutcome
from oversight.workspace import Tree, Workspace
from oversight_contracts.events import AgentStatus, AgentStep

pytestmark = pytest.mark.anyio

Opened = tuple[Workspace, Tree]

READ = ("read_file", {"path": "shop/mod.py"})
FINISH = ("finish", {"summary": "First version."})


def statuses(recorder: Recorder) -> list[AgentStatus]:
    return cast(list[AgentStatus], recorder.of("AgentStatus"))


def steps(recorder: Recorder) -> list[AgentStep]:
    return cast(list[AgentStep], recorder.of("AgentStep"))


async def test_revise_keeps_the_history(small_tree: Opened) -> None:
    recorder = Recorder()
    model = script([READ], [FINISH], [READ], [("finish", {"summary": "Second version."})])
    run = make_run(small_tree, model, recorder, task="Change value.")
    first = await run.run()
    assert first.state == "ready_for_review" and first.steps == 2
    assert run.max_steps == 20

    end = await run.revise("Make twice use the new value.", "Base moved: shop/mod.py has markers.")

    assert end.state == "ready_for_review" and end.summary == "Second version."
    assert end.steps == 4 and run.steps == 4
    assert run.max_steps == 40  # the budget grows with each revision
    # The revision's first model call still sees the whole first run, then one user message.
    history = model.requests[2]
    assert [m["role"] for m in history] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "user",
    ]
    assert history[1]["content"] == "Change value."
    assert str(history[3]["content"]).startswith("    1| def value()")
    revision = str(history[-1]["content"])
    assert "Make twice use the new value." in revision
    assert "Base moved: shop/mod.py has markers." in revision
    assert "Read a file again before editing it" in revision
    # The status stays `revising`, at the kept step count and the grown budget.
    assert [s.state for s in statuses(recorder)] == [
        "starting",
        "working",
        "ready_for_review",
        "revising",
        "ready_for_review",
    ]
    revising = statuses(recorder)[3]
    assert (revising.step, revising.max_steps) == (2, 40)
    assert recorder.of("sink.proposed") == [
        ("agent-1", "First version."),
        ("agent-1", "Second version."),
    ]


async def test_revise_answers_unanswered_calls(small_tree: Opened) -> None:
    recorder = Recorder()
    # `finish` ends the reply: the read after it never runs and gets no result.
    model = script([(FINISH[0], FINISH[1]), READ], [("finish", {"summary": "Second version."})])
    run = make_run(small_tree, model, recorder)
    await run.run()

    end = await run.revise("Try again.", None)

    assert end.state == "ready_for_review"
    history = model.requests[1]
    answered = {m["tool_call_id"]: m["content"] for m in history if m["role"] == "tool"}
    assert answered == {"call-1": "Finished.", "call-2": "Not run."}
    # The answers come before the revision message, which is the last word to the model.
    assert history[-1]["role"] == "user" and "Try again." in str(history[-1]["content"])


async def test_revise_answers_a_call_cut_short(
    small_tree: Opened, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = asyncio.Event()

    async def held_call(self: Toolbox, name: str, raw_arguments: str) -> ToolOutcome:
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("not reached")

    monkeypatch.setattr(Toolbox, "call", held_call)
    recorder = Recorder()
    model = script([("run_tests", {})], [("finish", {"summary": "Second version."})])
    run = make_run(small_tree, model, recorder)
    task = asyncio.create_task(run.run())
    async with asyncio.timeout(60):
        await entered.wait()
    task.cancel()  # Stop, mid-call: the step is sent, the tool result never is.
    with pytest.raises(asyncio.CancelledError):
        await task
    monkeypatch.undo()

    end = await run.revise("Try again.", None)

    assert end.state == "ready_for_review"
    history = model.requests[1]
    answered = {m["tool_call_id"]: m["content"] for m in history if m["role"] == "tool"}
    assert answered == {"call-1": "Not run."}


async def test_revise_resets_stop_rules(small_tree: Opened) -> None:
    recorder = Recorder()
    # Three identical reads in the first run: one more would halt it, were the count kept.
    model = script(
        [READ],
        [READ],
        [READ],
        [FINISH],
        [READ],
        [READ],
        [READ],
        [("finish", {"summary": "Second version."})],
    )
    run = make_run(small_tree, model, recorder)
    await run.run()

    end = await run.revise("Again.", None)

    assert end.state == "ready_for_review" and end.steps == 8


async def test_revise_resets_the_failure_count(small_tree: Opened) -> None:
    recorder = Recorder()
    # Five distinct failures, then finish: the count stands at five when the run ends.
    bad = [("read_file", {"path": f"missing-{n}.py"}) for n in range(6)]
    model = script(
        [bad[0]],
        [bad[1]],
        [bad[2]],
        [bad[3]],
        [bad[4]],
        [FINISH],
        [bad[5]],
        [("finish", {"summary": "Second version."})],
    )
    run = make_run(small_tree, model, recorder)
    await run.run()

    end = await run.revise("Again.", None)

    # One more failure halts a run that kept the count; a reset one goes on to finish.
    assert end.state == "ready_for_review" and end.steps == 8


async def test_revise_grows_the_step_limit(small_tree: Opened) -> None:
    recorder = Recorder()
    model = script(
        [("search", {"query": "value0"})],
        [("search", {"query": "value1"})],
        [("search", {"query": "value2"})],
        [("finish", {"summary": "Second version."})],
    )
    run = make_run(small_tree, model, recorder, max_steps=2)
    first = await run.run()
    assert first.state == "halted" and first.detail == "Reached the step limit of 2."

    end = await run.revise("Again.", None)

    assert end.state == "ready_for_review" and end.steps == 4
    revising = statuses(recorder)[-2]
    assert revising.state == "revising" and revising.max_steps == 4


async def test_revise_during_a_run_raises(
    small_tree: Opened, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = asyncio.Event()

    async def held_call(self: Toolbox, name: str, raw_arguments: str) -> ToolOutcome:
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("not reached")

    monkeypatch.setattr(Toolbox, "call", held_call)
    recorder = Recorder()
    run = make_run(small_tree, script([("run_tests", {})]), recorder)
    task = asyncio.create_task(run.run())
    async with asyncio.timeout(60):
        await entered.wait()

    with pytest.raises(RuntimeError):
        await run.revise("Too soon.", None)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "revising" not in [s.state for s in statuses(recorder)]
    assert len(steps(recorder)) == 1  # only the interrupted step


async def test_edit_after_a_save_needs_a_read(small_tree: Opened) -> None:
    _, worktree = small_tree
    recorder = Recorder()
    edit = (
        "edit_file",
        {"path": "shop/mod.py", "old_text": "return 1\n", "new_text": "return 2\n"},
    )
    stale = (
        "edit_file",
        {"path": "shop/mod.py", "old_text": "return 2\n", "new_text": "return 3\n"},
    )
    saved = (
        "edit_file",
        {"path": "shop/mod.py", "old_text": "return 99\n", "new_text": "return 3\n"},
    )
    model = script(
        [READ],
        [edit],
        [FINISH],
        [stale],
        [READ],
        [saved],
        [("finish", {"summary": "Second version."})],
    )
    run = make_run(small_tree, model, recorder)
    await run.run()
    # The person's save in review: the file changes behind the agent's back.
    path = worktree.root / "shop" / "mod.py"
    path.write_text(path.read_text().replace("return 2\n", "return 99\n"))

    end = await run.revise("Use 3 instead.", None)

    assert end.state == "ready_for_review"
    revision_steps = steps(recorder)[3:]
    assert [(s.tool, s.result) for s in revision_steps] == [
        ("edit_file", "error"),
        ("read_file", "ok"),
        ("edit_file", "ok"),
        ("finish", "ok"),
    ]
    assert revision_steps[0].error == "shop/mod.py changed since you last read it; read it again"
    assert "return 3" in path.read_text()


def test_prompt_version_hashes_the_revision_prompt() -> None:
    from oversight.agents._prompts import PLAN_PROMPT

    expected = (
        "agent-v1-"
        + hashlib.sha256(
            (
                SYSTEM_PROMPT
                + REVISION_PROMPT
                + PLAN_PROMPT
                + json.dumps(TOOL_SCHEMAS, sort_keys=True)
            ).encode()
        ).hexdigest()[:12]
    )
    assert expected == PROMPT_VERSION == "agent-v1-c7ca1d7c6ffe"


async def test_revise_before_a_run_raises(small_tree: Opened) -> None:
    model = ScriptedModel(parse_script('{"task": "t", "turns": []}'))
    run = make_run(small_tree, model, Recorder())
    with pytest.raises(RuntimeError):
        await run.revise("Too soon.", None)


async def test_a_second_revision_grows_the_budget_again(small_tree: Opened) -> None:
    recorder = Recorder()
    model = script(
        [FINISH],
        [("finish", {"summary": "Second version."})],
        [("finish", {"summary": "Third version."})],
    )
    run = make_run(small_tree, model, recorder, max_steps=5)
    await run.run()
    await run.revise("Once more.", None)
    end = await run.revise("And again.", "Note for the third.")

    assert end.state == "ready_for_review" and end.steps == 3
    assert [s.max_steps for s in statuses(recorder) if s.state == "revising"] == [10, 15]
    users = [str(m["content"]) for m in model.requests[-1] if m["role"] == "user"]
    assert "Note for the third." in users[-1]
    # `steps` is read-only.
    with pytest.raises(AttributeError):
        run.steps = 0  # type: ignore[misc]
