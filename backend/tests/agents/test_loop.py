"""The tool loop through the scripted fake model: a whole task, the limits and the stops."""

from typing import cast

import pytest
from agent_helpers import FAKE_MODEL_DIR, Recorder, make_run, script

from oversight.agents import ModelAPIError, ModelReply, ScriptedModel, load_script, parse_script
from oversight.workspace import Tree, Workspace
from oversight_contracts.events import (
    AgentFocus,
    AgentStatus,
    AgentStep,
    ErrorReport,
    UsageTick,
)

pytestmark = pytest.mark.anyio

Opened = tuple[Workspace, Tree]


def steps(recorder: Recorder) -> list[AgentStep]:
    return cast(list[AgentStep], recorder.of("AgentStep"))


def states(recorder: Recorder) -> list[str]:
    return [cast(AgentStatus, s).state for s in recorder.of("AgentStatus")]


async def test_whole_task_emits_the_expected_events_in_order(demo_tree: Opened) -> None:
    loaded = load_script(FAKE_MODEL_DIR / "document-apply-coupon.json")
    model = ScriptedModel(loaded)
    recorder = Recorder()
    end = await make_run(demo_tree, model, recorder, task=loaded.task).run()

    assert end.state == "ready_for_review"
    assert end.summary == "Documented apply_coupon."
    assert recorder.kinds() == [
        "AgentStatus",  # starting
        "AgentStatus",  # working
        "UsageTick",
        "AgentStep",  # list_files: no focus
        "AgentFocus",  # search
        "AgentStep",
        "UsageTick",
        "AgentFocus",  # read_file
        "AgentStep",
        "UsageTick",
        "sink.file_edited",  # change.updated comes first
        "AgentFocus",
        "AgentStep",
        "UsageTick",
        "AgentStep",  # run_tests: no focus
        "UsageTick",
        "AgentStep",  # finish
        "sink.proposed",
        "AgentStatus",  # ready_for_review
    ]
    assert states(recorder) == ["starting", "working", "ready_for_review"]
    assert [(s.step, s.tool, s.result) for s in steps(recorder)] == [
        (1, "list_files", "ok"),
        (2, "search", "ok"),
        (3, "read_file", "ok"),
        (4, "edit_file", "ok"),
        (5, "run_tests", "ok"),
        (6, "finish", "ok"),
    ]
    summaries = [s.summary for s in steps(recorder)]
    assert summaries[2] == "Read apply_coupon in pricing.py, lines 38 to 40"
    assert summaries[3] == "Edited apply_coupon in pricing.py, lines 38 to 39"
    tests = steps(recorder)[4].tests
    assert tests is not None and tests.outcome == "passed" and tests.failed == 0

    search, read, edit = (cast(AgentFocus, f) for f in recorder.of("AgentFocus"))
    assert search.mode == "read" and "shop.pricing:apply_coupon" in search.node_ids
    assert len(search.ranges) <= 5 and len(search.node_ids) <= 5
    assert read.mode == "read" and read.node_ids == ["shop.pricing:apply_coupon"]
    assert edit.mode == "edit" and edit.node_ids == ["shop.pricing:apply_coupon"]
    assert recorder.of("sink.file_edited") == [("agent-1", "shop/pricing.py")]
    assert recorder.of("sink.proposed") == [("agent-1", "Documented apply_coupon.")]

    edited = (demo_tree[1].root / "shop" / "pricing.py").read_text()
    assert '    """Take the coupon\'s percent off the total' in edited
    ticks = cast(list[UsageTick], recorder.of("UsageTick"))
    assert ticks[0].input_tokens == 900
    # The last turn reports no usage: unknown, never zero.
    assert (ticks[-1].input_tokens, ticks[-1].output_tokens, ticks[-1].cost_usd) == (
        None,
        None,
        None,
    )
    # The model saw each tool result, with numbered lines and no metadata.
    third = model.requests[2]
    assert third[-1]["role"] == "tool"
    assert str(third[-1]["content"]).startswith("   38| def apply_coupon")


async def test_status_comes_from_tool_calls_not_text(small_tree: Opened) -> None:
    recorder = Recorder()
    model = ScriptedModel(
        parse_script(
            '{"task": "t", "turns": [{"text": "Done! Ready for review.", '
            '"tool_calls": [{"name": "list_files", "arguments": {}}]}]}'
        )
    )
    end = await make_run(small_tree, model, recorder).run()
    # The model claimed it was done but never called finish: the run halts, it is not ready.
    assert end.state == "halted"
    assert "ready_for_review" not in states(recorder)


async def test_same_call_same_result_four_times_halts(small_tree: Opened) -> None:
    read = ("read_file", {"path": "shop/mod.py"})
    recorder = Recorder()
    end = await make_run(small_tree, script([read], [read], [read], [read], [read]), recorder).run()
    assert end.state == "halted" and end.steps == 4
    assert "same result 4 times" in (end.detail or "")
    assert states(recorder)[-1] == "halted"
    assert recorder.of("sink.proposed") == [("agent-1", None)]


async def test_three_repeats_do_not_halt(small_tree: Opened) -> None:
    read = ("read_file", {"path": "shop/mod.py"})
    finish = ("finish", {"summary": "ok"})
    recorder = Recorder()
    end = await make_run(small_tree, script([read], [read], [read], [finish]), recorder).run()
    assert end.state == "ready_for_review" and end.steps == 4


async def test_six_failures_in_a_row_halt(small_tree: Opened) -> None:
    bad = [("read_file", {"path": f"missing-{n}.py"}) for n in range(7)]
    recorder = Recorder()
    end = await make_run(small_tree, script(*([call] for call in bad)), recorder).run()
    assert end.state == "halted" and end.steps == 6
    assert "6 failures in a row" in (end.detail or "")
    assert [s.result for s in steps(recorder)] == ["error"] * 6


async def test_a_success_resets_the_failure_count(small_tree: Opened) -> None:
    bad = [[("read_file", {"path": f"missing-{n}.py"})] for n in range(5)]
    good = [("list_files", {})]
    finish = [("finish", {"summary": "ok"})]
    recorder = Recorder()
    end = await make_run(small_tree, script(*bad, good, *bad, finish), recorder).run()
    assert end.state == "ready_for_review" and end.steps == 12


async def test_replies_without_tool_calls_count_as_failures(small_tree: Opened) -> None:
    recorder = Recorder()
    end = await make_run(small_tree, script(), recorder).run()  # the script is empty
    assert end.state == "halted" and end.steps == 0
    assert len(recorder.of("UsageTick")) == 6


async def test_step_limit_halts(small_tree: Opened) -> None:
    calls = [[("search", {"query": f"value{n}"})] for n in range(5)]
    recorder = Recorder()
    end = await make_run(small_tree, script(*calls), recorder, max_steps=3).run()
    assert end.state == "halted" and end.steps == 3
    assert end.detail == "Reached the step limit of 3."


async def test_parallel_calls_after_a_halt_get_a_result(small_tree: Opened) -> None:
    calls = [("search", {"query": f"value{n}"}) for n in range(3)]
    model = script(calls)
    recorder = Recorder()
    end = await make_run(small_tree, model, recorder, max_steps=2).run()
    assert end.steps == 2 and len(steps(recorder)) == 2


async def test_model_api_failure_reports_an_error_and_halts(small_tree: Opened) -> None:
    class Failing(ScriptedModel):
        async def complete(self, messages: object, tools: object) -> ModelReply:  # type: ignore[override]
            raise ModelAPIError("model API error after 5 attempts: HTTP 503")

    recorder = Recorder()
    end = await make_run(
        small_tree, Failing(parse_script('{"task": "t", "turns": []}')), recorder
    ).run()
    assert end.state == "halted" and end.detail == "The model API failed."
    (error,) = cast(list[ErrorReport], recorder.of("ErrorReport"))
    assert error.source == "model_api" and error.fatal and error.agent_id == "agent-1"
