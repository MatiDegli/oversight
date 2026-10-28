"""The loop against a change sink that cannot record: a refused proposal, a lost edit, and a
failure nobody planned for."""

import asyncio
from typing import Literal, cast

import pytest
from agent_helpers import Recorder, make_run, script

from oversight.agents import ModelReply, RunEnd, ScriptedModel, parse_script
from oversight.agents.tools import Toolbox, ToolOutcome
from oversight.workspace import Tree, Workspace
from oversight_contracts.events import AgentFocus, AgentStatus, AgentStep, ErrorReport
from oversight_contracts.review import FinishDeclaration

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

    # The step's error is what the model was told, and it was told nothing: ok, with a note.
    edit_step = steps(recorder)[1]
    assert (edit_step.tool, edit_step.result, edit_step.error) == ("edit_file", "ok", None)
    assert edit_step.summary.startswith("Edited value in mod.py")
    assert edit_step.summary.endswith(" (saved, but not recorded for review)")
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
    assert [s.result for s in steps(recorder)[1:8]] == ["ok"] * 7


async def test_a_sink_that_raises_on_file_edited_loses_the_record_and_the_error_is_sent(
    small_tree: Opened,
) -> None:
    _, worktree = small_tree
    recorder = Recorder()
    recorder.edit_result = OSError(f"disk full at {worktree.root}/shop/mod.py")
    end = await make_run(small_tree, script([READ], [edit(1, 2)], FINISH), recorder).run()
    assert end.state == "ready_for_review"
    assert focuses(recorder)[-1].node_ids == []
    lost = steps(recorder)[1]
    assert (lost.result, lost.error) == ("ok", None) and "not recorded" in lost.summary
    (error,) = errors(recorder)
    assert error.source == "server" and not error.fatal and error.agent_id == "agent-1"
    assert error.message == (
        "The edit to shop/mod.py could not be recorded: OSError: disk full at shop/mod.py"
    )


async def test_an_unexpected_error_in_the_model_call_halts_the_run(small_tree: Opened) -> None:
    class Broken(ScriptedModel):
        async def complete(self, messages: object, tools: object) -> ModelReply:  # type: ignore[override]
            raise KeyError("secret-slot")

    recorder = Recorder()
    model = Broken(parse_script('{"task": "t", "turns": []}'))
    end = await make_run(small_tree, model, recorder).run()
    detail = "Stopped by an unexpected error: KeyError: 'secret-slot'"
    assert end == RunEnd("halted", detail, 0, None)
    assert [s.state for s in statuses(recorder)] == ["starting", "working", "halted"]
    assert statuses(recorder)[-1].detail == detail
    (error,) = errors(recorder)
    assert error.source == "agent" and error.fatal and error.message == detail
    assert error.agent_id == "agent-1" and error.change_id == "change-1"
    # The change manager still hears of the end, as for any halt, and proposes what is on disk.
    assert recorder.of("sink.proposed") == [("agent-1", None)]
    assert recorder.kinds()[-3:] == ["ErrorReport", "sink.proposed", "AgentStatus"]


async def test_an_unexpected_error_in_a_tool_halts_the_run_after_the_steps_it_sent(
    small_tree: Opened, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, worktree = small_tree
    real = Toolbox.call

    async def search_breaks(self: Toolbox, name: str, raw_arguments: str) -> ToolOutcome:
        if name == "search":
            raise RuntimeError(f"cannot scan {worktree.root}/shop\nat depth 3")
        return await real(self, name, raw_arguments)

    monkeypatch.setattr(Toolbox, "call", search_breaks)
    recorder = Recorder()
    model = script([READ], [("search", {"query": "value"})], FINISH)
    end = await make_run(small_tree, model, recorder).run()
    assert end.state == "halted" and end.steps == 1 and end.summary is None
    assert end.detail == "Stopped by an unexpected error: RuntimeError: cannot scan shop at depth 3"
    assert [s.tool for s in steps(recorder)] == ["read_file"]
    assert "ready_for_review" not in [s.state for s in statuses(recorder)]
    (error,) = errors(recorder)
    assert error.fatal and str(worktree.root) not in error.message
    assert recorder.of("sink.proposed") == [("agent-1", None)]


async def test_a_long_error_message_is_cut_to_one_short_line(small_tree: Opened) -> None:
    class Broken(ScriptedModel):
        async def complete(self, messages: object, tools: object) -> ModelReply:  # type: ignore[override]
            raise ValueError("line one\n" + "x" * 1000)

    model = Broken(parse_script('{"task": "t", "turns": []}'))
    end = await make_run(small_tree, model, Recorder()).run()
    assert end.detail is not None and "\n" not in end.detail
    assert end.detail.startswith("Stopped by an unexpected error: ValueError: line one xxx")
    assert len(end.detail) < 260


class Held(Recorder):
    """A sink whose call never returns, so only a cancellation can end the run."""

    def __init__(self, hold: Literal["file_edited", "proposed"]) -> None:
        super().__init__()
        self.hold = hold
        self.entered = asyncio.Event()

    async def file_edited(self, agent_id: str, path: str) -> bool:
        if self.hold == "file_edited":
            self.entered.set()
            await asyncio.Event().wait()
        return await super().file_edited(agent_id, path)

    async def proposed(
        self, agent_id: str, summary: str | None, *, declaration: FinishDeclaration | None = None
    ) -> bool:
        if self.hold == "proposed":
            self.entered.set()
            await asyncio.Event().wait()
        return await super().proposed(agent_id, summary)


@pytest.mark.parametrize("hold", ["file_edited", "proposed"])
async def test_stop_during_a_sink_call_still_propagates(
    small_tree: Opened, hold: Literal["file_edited", "proposed"]
) -> None:
    recorder = Held(hold)
    model = script([READ], [edit(1, 2)], FINISH)
    run = asyncio.create_task(make_run(small_tree, model, recorder).run())
    async with asyncio.timeout(60):
        await recorder.entered.wait()
    run.cancel()  # Stop
    with pytest.raises(asyncio.CancelledError):
        await run
    assert errors(recorder) == []  # a cancellation is not an error
    assert "halted" not in [s.state for s in statuses(recorder)]


async def test_stop_during_file_edited_still_reports_the_edit(small_tree: Opened) -> None:
    recorder = Held("file_edited")
    model = script([READ], [edit(1, 2)], FINISH)
    run = asyncio.create_task(make_run(small_tree, model, recorder).run())
    async with asyncio.timeout(60):
        await recorder.entered.wait()
    run.cancel()  # Stop, while the sink is still recording the edit
    with pytest.raises(asyncio.CancelledError):
        await run
    # The edit is on disk: the UI gets its focus, with no nodes since the record is unknown, and
    # its step, before the cancellation goes on.
    assert [f.mode for f in focuses(recorder)] == ["read", "edit"]
    assert focuses(recorder)[-1].node_ids == []
    assert [(s.step, s.tool, s.result, s.error) for s in steps(recorder)] == [
        (1, "read_file", "ok", None),
        (2, "edit_file", "ok", None),
    ]
    assert steps(recorder)[-1].summary.endswith(" (saved, but not recorded for review)")
    assert recorder.kinds()[-2:] == ["AgentFocus", "AgentStep"]
    assert errors(recorder) == [] and "halted" not in [s.state for s in statuses(recorder)]
    assert "return 2" in (small_tree[1].root / "shop" / "mod.py").read_text()
