"""The loop against a change sink that cannot record: a refused proposal, a lost edit, and a
failure nobody planned for."""

from typing import cast

import pytest
from agent_helpers import Recorder, make_run, script

from oversight.agents import RunEnd
from oversight.workspace import Tree, Workspace
from oversight_contracts.events import AgentStatus, ErrorReport

pytestmark = pytest.mark.anyio

Opened = tuple[Workspace, Tree]

FINISH = [("finish", {"summary": "Done."})]


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
