"""The loop against a change sink that cannot record: a refused proposal, a lost edit, and a
failure nobody planned for."""

from typing import cast

import pytest
from agent_helpers import Recorder, make_run, script

from oversight.agents import RunEnd
from oversight.workspace import Tree, Workspace
from oversight_contracts.events import AgentFocus, AgentStatus, AgentStep, ErrorReport

pytestmark = pytest.mark.anyio

Opened = tuple[Workspace, Tree]

FINISH = [("finish", {"summary": "Done."})]
READ = ("read_file", {"path": "shop/mod.py"})


def statuses(recorder: Recorder) -> list[AgentStatus]:
    return cast(list[AgentStatus], recorder.of("AgentStatus"))


def errors(recorder: Recorder) -> list[ErrorReport]:
    return cast(list[ErrorReport], recorder.of("ErrorReport"))


async def test_a_proposal_the_sink_refused_halts_the_run(small_tree: Opened) -> None:
    recorder = Recorder()
    recorder.propose_result = False
    end = await make_run(small_tree, script(FINISH), recorder).run()
    assert end == RunEnd("halted", "Its change could not be proposed.", 1, None)
    assert [s.state for s in statuses(recorder)] == ["starting", "working", "halted"]
    assert statuses(recorder)[-1].detail == end.detail
    assert errors(recorder) == []  # the sink that answers False has sent its own error


async def test_a_sink_that_raises_on_proposed_halts_the_run_and_the_error_is_sent(
    small_tree: Opened,
) -> None:
    _, worktree = small_tree
    recorder = Recorder()
    recorder.propose_result = RuntimeError(f"cannot read {worktree.root}/shop/mod.py\nnor more")
    end = await make_run(small_tree, script(FINISH), recorder).run()
    assert end == RunEnd("halted", "Its change could not be proposed.", 1, None)
    assert statuses(recorder)[-1].state == "halted"
    (error,) = errors(recorder)
    assert error.source == "server" and error.fatal and error.agent_id == "agent-1"
    assert error.message == (
        "The change could not be proposed: RuntimeError: cannot read shop/mod.py nor more"
    )


async def test_a_halt_keeps_its_own_reason_when_the_proposal_fails(small_tree: Opened) -> None:
    calls = [[("search", {"query": f"value{n}"})] for n in range(3)]
    recorder = Recorder()
    recorder.propose_result = False
    end = await make_run(small_tree, script(*calls), recorder, max_steps=2).run()
    assert end.state == "halted" and end.detail == "Reached the step limit of 2."
    assert recorder.of("sink.proposed") == [("agent-1", None)]
    assert statuses(recorder)[-1].detail == end.detail


def edit(old: int, new: int) -> tuple[str, object]:
    return (
        "edit_file",
        {"path": "shop/mod.py", "old_text": f"return {old}\n", "new_text": f"return {new}\n"},
    )


def focuses(recorder: Recorder) -> list[AgentFocus]:
    return cast(list[AgentFocus], recorder.of("AgentFocus"))


def steps(recorder: Recorder) -> list[AgentStep]:
    return cast(list[AgentStep], recorder.of("AgentStep"))


async def test_a_recorded_edit_names_its_nodes(small_tree: Opened) -> None:
    recorder = Recorder()
    model = script([READ], [edit(1, 2)], FINISH)
    end = await make_run(small_tree, model, recorder).run()
    assert end.state == "ready_for_review"
    _, focus = focuses(recorder)
    assert focus.mode == "edit" and focus.node_ids == ["shop.mod:value"]
    assert [(s.result, s.error) for s in steps(recorder)] == [("ok", None)] * 3


async def test_an_edit_the_sink_failed_to_record_still_emits_focus_without_nodes(
    small_tree: Opened,
) -> None:
    recorder = Recorder()
    recorder.edit_result = False
    model = script([READ], [edit(1, 2)], FINISH)
    end = await make_run(small_tree, model, recorder).run()
    assert end.state == "ready_for_review"  # the edit is on disk: the run goes on

    read, focus = focuses(recorder)
    assert read.mode == "read" and read.node_ids != []
    assert focus.mode == "edit" and focus.node_ids == []
    assert [(r.path, r.start_line, r.end_line) for r in focus.ranges] == [("shop/mod.py", 2, 2)]
    # The focus follows the sink's call and precedes the step.
    kinds = recorder.kinds()
    assert kinds[kinds.index("sink.file_edited") :][:3] == [
        "sink.file_edited",
        "AgentFocus",
        "AgentStep",
    ]

    edit_step = steps(recorder)[1]
    assert (edit_step.tool, edit_step.result) == ("edit_file", "error")
    assert edit_step.error == "The edit was saved but could not be recorded for review."
    assert edit_step.summary.startswith("Edited value in mod.py")  # the summary is unchanged
    # The model is told nothing of it: the edit worked, and the file shows it.
    tool_results = [m["content"] for m in model.requests[-1] if m["role"] == "tool"]
    assert tool_results[1] == "Edited shop/mod.py."
    assert "return 2" in (small_tree[1].root / "shop" / "mod.py").read_text()


async def test_edits_the_sink_failed_to_record_do_not_count_as_failures(small_tree: Opened) -> None:
    recorder = Recorder()
    recorder.edit_result = False
    # Seven in a row would halt the run at six if the lost record counted as a failed call.
    model = script([READ], *([edit(n, n + 1)] for n in range(1, 8)), FINISH)
    end = await make_run(small_tree, model, recorder).run()
    assert end.state == "ready_for_review" and end.steps == 9
    assert [s.result for s in steps(recorder)[1:8]] == ["error"] * 7


async def test_a_sink_that_raises_on_file_edited_loses_the_record_and_the_error_is_sent(
    small_tree: Opened,
) -> None:
    _, worktree = small_tree
    recorder = Recorder()
    recorder.edit_result = OSError(f"disk full at {worktree.root}/shop/mod.py")
    end = await make_run(small_tree, script([READ], [edit(1, 2)], FINISH), recorder).run()
    assert end.state == "ready_for_review"
    assert focuses(recorder)[-1].node_ids == []
    assert steps(recorder)[1].result == "error"
    (error,) = errors(recorder)
    assert error.source == "server" and not error.fatal and error.agent_id == "agent-1"
    assert error.message == (
        "The edit to shop/mod.py could not be recorded: OSError: disk full at shop/mod.py"
    )
