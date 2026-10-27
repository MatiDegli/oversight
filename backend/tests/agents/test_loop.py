"""The tool loop through the scripted fake model: a whole task, the limits and the stops."""

import asyncio
from typing import cast

import httpx2 as httpx
import openai
import pytest
from agent_helpers import FAKE_MODEL_DIR, Recorder, make_run, script

from oversight.agents import (
    MUSE_SPARK,
    MUSE_SPARK_OPEN,
    ModelAPIError,
    ModelReply,
    OpenAIClient,
    ScriptedModel,
    UnverifiedProfile,
    load_script,
    parse_script,
)
from oversight.agents.tools import Toolbox, ToolOutcome
from oversight.workspace import Tree, Workspace
from oversight_contracts.events import (
    AgentFocus,
    AgentStatus,
    AgentStep,
    ErrorReport,
    ModelProfile,
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
    assert end.state == "halted"
    assert end.detail == "The model API failed: model API error after 5 attempts: HTTP 503."
    (error,) = cast(list[ErrorReport], recorder.of("ErrorReport"))
    assert error.source == "model_api" and error.fatal and error.agent_id == "agent-1"


async def test_retry_after_past_deadline_reaches_the_halted_status(small_tree: Opened) -> None:
    requests: list[httpx.Request] = []
    sleeps: list[float] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(429, headers={"retry-after": "75"}, json={"error": "busy"})

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    recorder = Recorder()
    async with openai.DefaultAsyncHttpxClient(transport=httpx.MockTransport(respond)) as http:
        model = OpenAIClient(
            MUSE_SPARK,
            base_url="https://api.test/v1",
            api_key="test-key",
            max_output_tokens=2048,
            http_client=http,
            sleep=sleep,
        )
        end = await make_run(small_tree, model, recorder, max_wall_clock_s=10).run()

    reason = "model API retry wait of 75 s exceeds the task deadline"
    assert end.state == "halted" and end.steps == 0
    assert end.detail == f"The model API failed: {reason}."
    halted = cast(list[AgentStatus], recorder.of("AgentStatus"))[-1]
    assert halted.state == "halted" and halted.detail == end.detail
    (error,) = cast(list[ErrorReport], recorder.of("ErrorReport"))
    assert error.message == reason and error.source == "model_api" and error.fatal
    assert len(requests) == 1 and sleeps == []
    assert "ready_for_review" not in states(recorder)


async def test_model_calls_are_bounded_on_their_own(small_tree: Opened) -> None:
    # No stop rule fires: each search succeeds and resets the failure count, and none repeats.
    turns: list[list[tuple[str, object]]] = []
    for n in range(20):
        turns += [[], [("search", {"query": f"value{n}"})]]
    model = script(*turns)
    end = await make_run(small_tree, model, Recorder(), max_steps=10).run()
    # max_steps plus the failure limit: 10 + 6 model calls, though only 8 steps were taken.
    assert end.state == "halted" and end.detail == "Reached the limit of 16 model calls."
    assert len(model.requests) == 16 and end.steps == 8


async def test_the_time_limit_covers_the_first_scan(small_tree: Opened) -> None:
    model = script([("finish", {"summary": "ok"})])
    recorder = Recorder()
    # A budget of 0 s is spent before anything runs: the scan is inside it, so no model call.
    end = await make_run(small_tree, model, recorder, max_wall_clock_s=0).run()
    assert end.state == "halted" and end.detail == "Ran past the time limit of 0 s."
    assert model.requests == []
    assert states(recorder) == ["starting", "halted"]


async def test_a_root_lost_before_the_first_scan_halts_the_run(small_tree: Opened) -> None:
    _, worktree = small_tree
    root = worktree.root
    moved = root.with_name("moved-away")
    root.rename(moved)  # the root vanishes after the worktree was made, before the run starts
    root.symlink_to(moved, target_is_directory=True)  # and a link to the old files takes its place
    model = script([("finish", {"summary": "ok"})])
    recorder = Recorder()
    end = await make_run(small_tree, model, recorder).run()
    assert end.state == "halted" and end.steps == 0 and end.summary is None
    assert end.detail is not None and end.detail.startswith("The worktree cannot be used: ")
    assert "\n" not in end.detail
    assert model.requests == []
    assert states(recorder) == ["starting", "halted"]
    (halted,) = [s for s in recorder.of("AgentStatus") if cast(AgentStatus, s).state == "halted"]
    assert cast(AgentStatus, halted).detail == end.detail
    # The sink would read the invalid root, so it is never asked.
    assert "sink.proposed" not in recorder.kinds()


async def test_a_tool_cut_short_by_the_time_limit_still_gets_its_step(
    small_tree: Opened, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_scan(self: Toolbox) -> None:
        return None  # nothing to wait on, so the 0 s limit first fires inside the read

    monkeypatch.setattr(Toolbox, "refresh_graph", no_scan)
    recorder = Recorder()
    model = script([("read_file", {"path": "shop/mod.py"})])
    end = await make_run(small_tree, model, recorder, max_wall_clock_s=0).run()
    assert end.state == "halted" and end.detail == "Ran past the time limit of 0 s."
    (step,) = steps(recorder)
    assert (step.step, step.tool, step.result) == (1, "read_file", "error")
    assert step.error == "interrupted before it finished"
    assert step.summary == "Could not read mod.py"
    assert recorder.kinds()[-3:] == ["AgentStep", "sink.proposed", "AgentStatus"]


async def test_a_tool_cut_short_by_stop_still_gets_its_step(
    small_tree: Opened, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = asyncio.Event()

    async def held_call(self: Toolbox, name: str, raw_arguments: str) -> ToolOutcome:
        entered.set()
        await asyncio.Event().wait()  # never set: only the cancellation ends the call
        raise AssertionError("not reached")

    monkeypatch.setattr(Toolbox, "call", held_call)
    recorder = Recorder()
    run = asyncio.create_task(make_run(small_tree, script([("run_tests", {})]), recorder).run())
    async with asyncio.timeout(60):
        await entered.wait()
    run.cancel()  # Stop
    with pytest.raises(asyncio.CancelledError):
        await run
    (step,) = steps(recorder)
    assert (step.step, step.tool, step.result) == (1, "run_tests", "error")
    assert step.error == "interrupted before it finished"
    # Stop is not a proposal: the change manager sends the stop events.
    assert "sink.proposed" not in recorder.kinds()


UNVERIFIED_TEST_PROFILE = ModelProfile(
    id="test-unverified",
    label="Unverified test profile",
    provider="fake",
    model="test-model",
    reasoning=None,
)


async def test_a_run_on_an_unverified_profile_is_refused(
    small_tree: Opened, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No shipped profile is unverified now: the test puts a synthetic one in the set.
    monkeypatch.setattr(
        "oversight.agents.client.UNVERIFIED_PROFILES", frozenset({UNVERIFIED_TEST_PROFILE.id})
    )
    empty = parse_script('{"task": "t", "turns": []}')
    model = ScriptedModel(empty, profile=UNVERIFIED_TEST_PROFILE)
    with pytest.raises(UnverifiedProfile, match="reasoning setting is unverified") as refused:
        make_run(small_tree, model, Recorder())
    assert "Unverified test profile (test-model)" in str(refused.value)
    assert "tools/probe_api.py" in str(refused.value)
    make_run(small_tree, ScriptedModel(empty, profile=MUSE_SPARK), Recorder())


async def test_both_muse_profiles_may_run_agents(small_tree: Opened) -> None:
    empty = parse_script('{"task": "t", "turns": []}')
    for profile in (MUSE_SPARK, MUSE_SPARK_OPEN):
        make_run(small_tree, ScriptedModel(empty, profile=profile), Recorder())
