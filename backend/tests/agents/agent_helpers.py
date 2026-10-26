"""Helpers for the agent runtime tests: an event recorder and fake ChangeSink that log into one
ordered list, a workspace with one agent worktree, and fake-model scripts built in code."""

import asyncio
import json
import sys
from collections.abc import Sequence
from functools import partial
from pathlib import Path

from oversight.agents import (
    AgentRun,
    AgentTask,
    ModelClient,
    ScriptedModel,
    UsageLedger,
    parse_script,
)
from oversight.events import EventPayload
from oversight.workspace import Tree, Workspace
from oversight_contracts.events import Limits

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_REPO = REPO_ROOT / "demo-repo"
FAKE_MODEL_DIR = REPO_ROOT / "fixtures" / "fake-model"

SMALL_TEMPLATE = {
    "pyproject.toml": "[tool.pytest.ini_options]\ntestpaths = ['tests']\npythonpath = ['.']\n",
    "shop/__init__.py": "",
    "shop/mod.py": (
        "def value() -> int:\n    return 1\n\n\ndef twice() -> int:\n    return value() + value()\n"
    ),
    "shop/notes.txt": "value\nvalue\n",
    "tests/test_mod.py": "from shop.mod import value\n\n\ndef test_value() -> None:\n"
    "    assert value() == 1\n",
}


class Recorder:
    """Events and sink calls, in the order they happened."""

    def __init__(self) -> None:
        self.log: list[tuple[str, object]] = []

    async def publish(self, payload: EventPayload) -> None:
        self.log.append((type(payload).__name__, payload))

    async def file_edited(self, agent_id: str, path: str) -> None:
        self.log.append(("sink.file_edited", (agent_id, path)))

    async def proposed(self, agent_id: str, summary: str | None) -> None:
        self.log.append(("sink.proposed", (agent_id, summary)))

    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.log]

    def of(self, kind: str) -> list[object]:
        return [payload for k, payload in self.log if k == kind]


def write_tree(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return root


async def open_tree(made: list[Workspace], root: Path, template: Path) -> tuple[Workspace, Tree]:
    """A started workspace from `template`, with the worktree of agent-1. The test suite runs in
    the backend's interpreter, which carries the pinned pytest; the product uses the demo venv."""
    runtime = root / ".runtime"
    await asyncio.to_thread(partial(root.mkdir, parents=True, exist_ok=True))
    workspace = Workspace(runtime, template, pytest_pin="9.1.1", demo_python=Path(sys.executable))
    made.append(workspace)
    await workspace.acquire_runtime()
    await workspace.reset()
    return workspace, await workspace.create_worktree("agent-1")


def limits(max_steps: int = 20, max_wall_clock_s: int = 120) -> Limits:
    return Limits(
        max_steps=max_steps,
        max_output_tokens=4096,
        max_wall_clock_s=max_wall_clock_s,
        test_timeout_s=60,
        cone_max_depth=4,
        cone_max_nodes=40,
    )


def script(*turns: Sequence[tuple[str, object]], task: str = "Do the task.") -> ScriptedModel:
    """A fake model from turns of (tool, arguments) pairs; arguments may be a raw string."""
    data = {
        "task": task,
        "turns": [
            {"tool_calls": [{"name": name, "arguments": args} for name, args in turn]}
            for turn in turns
        ],
    }
    return ScriptedModel(parse_script(json.dumps(data)))


def make_run(
    tree: tuple[Workspace, Tree],
    model: ModelClient,
    recorder: Recorder,
    *,
    task: str = "Do the task.",
    max_steps: int = 20,
    max_wall_clock_s: int = 120,
    ledger: UsageLedger | None = None,
) -> AgentRun:
    workspace, worktree = tree
    return AgentRun(
        AgentTask("agent-1", "change-1", "A", task, limits(max_steps, max_wall_clock_s)),
        model=model,
        workspace=workspace,
        tree=worktree,
        sink=recorder,
        publish=recorder.publish,
        ledger=ledger or UsageLedger(budget_usd=10.0),
        monotonic=lambda: 0.0,
    )
