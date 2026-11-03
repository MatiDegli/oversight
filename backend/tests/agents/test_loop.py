"""The tool loop through the scripted fake model: a whole task, the limits and the stops."""

import asyncio
from pathlib import Path
from typing import Literal, cast

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
    assert len(recorder.suites) == 1
    assert recorder.suites[0].cases and not recorder.suites[0].problems
    assert recorder.suites[0].tested_tree is not None

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


async def test_a_failed_suite_spawn_still_reaches_the_sink_without_changing_model_text(
    small_tree: Opened,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _ = small_tree
    monkeypatch.setattr(workspace, "_demo_python", Path("/missing-demo-python"))
    recorder = Recorder()
    end = await make_run(
        small_tree, script([("run_tests", {})], [("finish", {"summary": "Done."})]), recorder
    ).run()
    assert end.state == "ready_for_review"
    assert len(recorder.suites) == 1 and recorder.suites[0].result.outcome == "error"
    assert recorder.suites[0].tested_tree is not None
    [step, _] = steps(recorder)
    assert step.result == "error" and step.tests is None
    assert step.error == "cannot access the file: No such file or directory"


async def test_a_cancelled_suite_reaches_the_sink_with_partial_evidence(
    small_tree: Opened,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from oversight.workspace import core as workspace_core

    entered = asyncio.Event()

    async def held_spawn(*args: object, **kwargs: object):
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("not reached")

    monkeypatch.setattr(workspace_core, "run_process", held_spawn)
    recorder = Recorder()
    running = asyncio.create_task(make_run(small_tree, script([("run_tests", {})]), recorder).run())
    async with asyncio.timeout(60):
        await entered.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert len(recorder.suites) == 1
    assert recorder.suites[0].result.outcome == "timeout"
    assert recorder.suites[0].tested_tree is not None and recorder.suites[0].problems
    assert steps(recorder)[0].error == "interrupted before it finished"


def queue_suite(outcome: Literal["passed", "failed", "error", "timeout"] = "passed"):
    from oversight.workspace import SuiteRun
    from oversight_contracts.events import CaseResult, SuiteResult

    return SuiteRun(
        SuiteResult(
            outcome=outcome,
            passed=1,
            failed=int(outcome == "failed"),
            skipped=0,
            duration_ms=10,
            failed_tests=["tests/test_x.py::test_bad"] if outcome == "failed" else [],
        ),
        "same suite output",
        cases=(CaseResult(test_id="tests/test_x.py::test_ok", outcome="passed"),),
        problems=(),
    )


@pytest.mark.parametrize(
    "mode", ["passed", "failed", "timeout", "unavailable", "cancelled", "partial"]
)
async def test_run_tests_summary_queue_and_budget(small_tree, monkeypatch, mode):
    import re

    from oversight.agents.client import CALL_SCOPE
    from oversight.workspace import SuiteInterrupted, SuiteUnavailable

    expected_budget = []
    suite = queue_suite(mode if mode in {"passed", "failed", "timeout"} else "passed")
    calls = 0

    async def run_tests(self, tree, *, timeout_s, on_queue_wait):
        nonlocal calls
        calls += 1
        on_queue_wait(0.0)
        on_queue_wait(2.5 if calls == 1 else 0.0)
        if calls == 1:
            await asyncio.sleep(0.12)  # the remaining budget must use the active clock
        scope = CALL_SCOPE.get()
        assert scope is not None and scope.deadline is not None
        expected_budget.append(scope.deadline - asyncio.get_running_loop().time())
        if calls == 1:
            if mode == "unavailable":
                raise SuiteUnavailable(suite, OSError("suite unavailable"))
            if mode == "cancelled":
                task = asyncio.current_task()
                assert task is not None
                task.cancel()
                await asyncio.sleep(0)
            if mode == "partial":
                raise SuiteInterrupted(suite)
        return suite

    monkeypatch.setattr(Workspace, "run_tests", run_tests)
    model = script(
        [("run_tests", {})],
        [("run_tests", {})],
        [("list_files", {})],
        [("finish", {"summary": "ok"})],
    )
    recorder = Recorder()
    run = make_run(small_tree, model, recorder)
    if mode in {"cancelled", "partial"}:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.create_task(run.run())
    else:
        assert (await run.run()).state == "ready_for_review"
    first = steps(recorder)[0]
    match = re.search(r"\. Queued 2.5 s; round budget remaining ([\d.]+) s\.$", first.summary)
    assert match and float(match[1]) == pytest.approx(expected_budget[0], abs=0.051)
    assert "Queued" not in (first.error or "")
    assert recorder.suites == (
        [] if mode == "cancelled" else [suite] if mode == "partial" else [suite, suite]
    )
    if mode in {"cancelled", "partial"}:
        assert first.result == "error" and first.error == "interrupted before it finished"
        assert "sink.proposed" not in recorder.kinds()
    else:
        assert "Queued 0.0 s;" in steps(recorder)[1].summary
        assert all("Queued" not in step.summary for step in steps(recorder)[2:])
        assert all(
            "Queued" not in str(message.get("content", ""))
            for request in model.requests
            for message in request
        )


@pytest.mark.parametrize(
    "summary, expected",
    [
        ("Ran tests: 3 of 4 pass", "Ran tests: 3 of 4 pass."),
        ("Tests reached the limit.", "Tests reached the limit."),
        ("Tests passed!", "Tests passed!"),
        ("Tests available?", "Tests available?"),
    ],
)
async def test_queue_summary_joins_sentences(small_tree, monkeypatch, summary, expected):
    run = make_run(small_tree, script(), Recorder())
    run._tools._queue_wait(2.5)
    outcome = ToolOutcome(ok=True, summary=summary, content="Unchanged model observation")
    # Freeze only this synchronous helper call; no event-loop callbacks use the fake clock.
    with monkeypatch.context() as clock:
        clock.setattr(asyncio.get_running_loop(), "time", lambda: 0.0)
        updated = run._queue_summary(outcome, asyncio.Timeout(41.0))
    assert updated.summary == expected + " Queued 2.5 s; round budget remaining 41.0 s."
    assert updated.content == outcome.content
    assert outcome.summary == summary


async def test_queue_timing_does_not_reset_no_progress(small_tree, monkeypatch):
    suite = queue_suite()
    calls = 0

    async def run_tests(self, tree, *, timeout_s, on_queue_wait):
        nonlocal calls
        calls += 1
        on_queue_wait(float(calls))
        return suite

    monkeypatch.setattr(Workspace, "run_tests", run_tests)
    recorder = Recorder()
    model = script(*([("run_tests", {})] for _ in range(5)))
    end = await make_run(small_tree, model, recorder).run()
    assert end.state == "halted" and "same result 4 times" in (end.detail or "")
    assert calls == 4
    assert [f"Queued {n}.0 s;" in step.summary for n, step in enumerate(steps(recorder), 1)] == [
        True
    ] * 4
    observations = [
        message["content"]
        for request in model.requests
        for message in request
        if message["role"] == "tool"
    ]
    assert len(set(observations)) == 1 and "Queued" not in str(observations[0])


@pytest.mark.parametrize("boundary", ["main", "worktree", "Git lease"])
@pytest.mark.parametrize("mode", ["deadline", "cancel"])
async def test_round_deadline_while_waiting_reports_queue_time(
    small_tree, monkeypatch, boundary, mode
):
    from oversight.workspace.locks import QueueWait

    tasks_before = asyncio.all_tasks()
    workspace, tree = small_tree
    scanned, admit, held, release, waiting = [asyncio.Event() for _ in range(5)]
    scan = Toolbox.refresh_graph
    begin = QueueWait.begin
    now = 10.0
    monkeypatch.setattr("oversight.workspace.locks.monotonic", lambda: now)

    async def scan_then_gate(self):
        await scan(self)
        scanned.set()
        await admit.wait()

    def observe(self, category):
        begin(self, category)
        waiting.set()

    monkeypatch.setattr(Toolbox, "refresh_graph", scan_then_gate)
    monkeypatch.setattr(QueueWait, "begin", observe)

    async def holder():
        async with workspace.locks(
            main=boundary == "main",
            agent_id="agent-1"
            if boundary == "worktree"
            else "holder"
            if boundary == "Git lease"
            else None,
        ):
            if boundary == "Git lease":
                async with workspace.git_lease():
                    held.set()
                    await release.wait()
            else:
                held.set()
                await release.wait()

    recorder = Recorder()
    run = asyncio.create_task(
        make_run(
            small_tree,
            script([("run_tests", {})]),
            recorder,
            max_wall_clock_s=1 if mode == "deadline" else 120,
        ).run()
    )
    await scanned.wait()
    holder_task = asyncio.create_task(holder())
    await held.wait()
    admit.set()
    await waiting.wait()
    now = 18.0
    if mode == "cancel":
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
        assert "sink.proposed" not in recorder.kinds()
    else:
        end = await run
        assert end.state == "halted" and end.detail == "Ran past the time limit of 1 s."
    (step,) = steps(recorder)
    assert "Queued 8.0 s; round budget remaining " in step.summary
    if mode == "deadline":
        assert step.summary.endswith("round budget remaining 0.0 s.")
    assert step.error == "interrupted before it finished" and step.tests is None
    assert not recorder.suites and not holder_task.done()
    release.set()
    await holder_task
    async with workspace.locks(main=True, agent_id=tree.agent_id), workspace.git_lease():
        pass
    assert workspace.operations.idle
    assert asyncio.all_tasks() <= tasks_before


async def test_queue_timing_is_cleared_before_a_call_that_cannot_report(small_tree, monkeypatch):
    calls = 0

    async def run_tests(self, tree, *, timeout_s, on_queue_wait):
        nonlocal calls
        calls += 1
        if calls == 1:
            on_queue_wait(4.0)
            return queue_suite()
        raise OSError("failed before observation")

    monkeypatch.setattr(Workspace, "run_tests", run_tests)
    recorder = Recorder()
    model = script([("run_tests", {})], [("run_tests", {})], [("finish", {"summary": "ok"})])
    await make_run(small_tree, model, recorder).run()
    assert "Queued 4.0 s;" in steps(recorder)[0].summary
    assert "Queued" not in steps(recorder)[1].summary
