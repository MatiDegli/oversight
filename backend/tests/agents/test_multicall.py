"""A batched reply preserves tool order and the developer's task verbatim."""

from typing import cast

import pytest
from agent_helpers import Recorder, make_run, script

from oversight.agents.loop import PROMPT_VERSION, SYSTEM_PROMPT
from oversight.workspace import Tree, Workspace
from oversight_contracts.events import AgentStep

pytestmark = pytest.mark.anyio


async def test_multicall_reply_executes_in_order_with_one_step_per_call(
    small_tree: tuple[Workspace, Tree],
) -> None:
    task = "Improve the value documentation."
    model = script(
        [("search", {"query": "value"}), ("read_file", {"path": "shop/mod.py"})],
        [
            (
                "edit_file",
                {
                    "path": "shop/mod.py",
                    "old_text": "def value() -> int:\n",
                    "new_text": 'def value() -> int:\n    """Return one."""\n',
                },
            ),
            ("run_tests", {}),
        ],
        [("finish", {"summary": "Documented value."})],
        task=task,
    )
    recorder = Recorder()
    end = await make_run(small_tree, model, recorder, task=task).run()
    assert end.state == "ready_for_review" and end.steps == 5
    steps = cast(list[AgentStep], recorder.of("AgentStep"))
    assert [(step.step, step.tool, step.result) for step in steps] == [
        (1, "search", "ok"),
        (2, "read_file", "ok"),
        (3, "edit_file", "ok"),
        (4, "run_tests", "ok"),
        (5, "finish", "ok"),
    ]
    assert recorder.suites[0].result.outcome == "passed"
    assert len(model.requests) == 3
    assert model.requests[0] == [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]
    assert model.requests[2][: len(model.requests[1])] == model.requests[1]
    assert "independent reads and searches together" in SYSTEM_PROMPT
    assert PROMPT_VERSION.startswith("agent-v1-")
