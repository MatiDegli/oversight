"""Malformed call metadata stays in events; the model still receives the same tool text."""

from typing import cast

import pytest
from agent_helpers import make_run, script

from oversight.agents.tools import interrupted
from oversight.workspace import Tree, Workspace
from oversight_contracts.events import AgentStep

from .test_attempt_events import ValidatingRecorder
from .test_tools import EDIT, toolbox

pytestmark = pytest.mark.anyio
Opened = tuple[Workspace, Tree]


@pytest.mark.parametrize(
    "name,arguments,kind,text",
    [
        ("shell", "{}", "unknown_tool", "unknown tool 'shell'"),
        ("read_file", '{"path":', "invalid_json", "arguments are not valid JSON"),
        ("read_file", "[]", "invalid_arguments", "arguments must be a JSON object"),
        ("read_file", '{"path":3}', "invalid_arguments", "argument 'path' must be string"),
        ("read_file", "{}", "invalid_arguments", "invalid arguments"),
        ("list_files", '{"extra":1}', "invalid_arguments", "invalid arguments"),
        ("read_file", '{"path":"shop/mod.py","start_line":0}', "invalid_arguments", "from 1"),
        ("finish", '{"summary":" "}', "invalid_arguments", "summary must not be blank"),
        (
            "finish",
            '{"summary":"Done","files":[123]}',
            "invalid_arguments",
            "invalid finish declaration",
        ),
    ],
)
async def test_malformed_calls_set_outcome_and_step_kind(
    small_tree: Opened, name: str, arguments: str, kind: str, text: str
) -> None:
    tools = await toolbox(small_tree)
    outcome = await tools.call(name, arguments)
    assert not outcome.ok and outcome.error_kind == kind
    assert outcome.content.startswith("Error: ") and text in outcome.content
    model = script([(name, arguments)], [("finish", {"summary": "Done"})])
    recorder = ValidatingRecorder()
    end = await make_run(small_tree, model, recorder).run()
    assert end.state == "ready_for_review"
    steps = cast(list[AgentStep], recorder.of("AgentStep"))
    assert steps[0].error_kind == kind and steps[0].result == "error"
    assert steps[0].error == outcome.content.removeprefix("Error: ")[:200]
    assert steps[1].error_kind is None and steps[1].result == "ok"


async def test_ordinary_tool_errors_and_interruptions_keep_null(small_tree: Opened) -> None:
    tools = await toolbox(small_tree)
    # This malformed call distinguishes the new producer on main as well as checking defaults.
    assert (await tools.call("shell", "{}")).error_kind == "unknown_tool"
    ordinary = [
        await tools.call("read_file", '{"path":"missing.py"}'),
        await tools.call("read_file", '{"path":"../outside.py"}'),
        await tools.call("edit_file", '{"path":"shop/mod.py","old_text":"1","new_text":"2"}'),
        interrupted("read_file", '{"path":"shop/mod.py"}'),
    ]
    for outcome in ordinary:
        assert not outcome.ok and outcome.error_kind is None
    model = script(
        [("read_file", {"path": "missing.py"})],
        [("edit_file", EDIT)],
        [("finish", {"summary": "Done"})],
    )
    recorder = ValidatingRecorder()
    end = await make_run(small_tree, model, recorder).run()
    assert end.state == "ready_for_review"
    steps = cast(list[AgentStep], recorder.of("AgentStep"))
    assert [step.result for step in steps] == ["error", "error", "ok"]
    assert all(step.error_kind is None for step in steps)
