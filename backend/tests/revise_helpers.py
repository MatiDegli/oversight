"""Scripts and doubles for the tests of Request changes and Stop on the live app: the fixture
script of slot A with revision turns after its `finish`, a store whose `changes_requested` publish
waits at a gate per agent, before the store has taken it, and what the two test files share.

A gate must be open before the server shuts down, which waits for a decision held at it and
cannot be cancelled out of it: `HeldRequests` and `opened` are context managers that open theirs on
the way out, so a failed assertion fails the test instead of hanging its teardown."""

import asyncio
import contextlib
import json
from collections import defaultdict
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from live_helpers import FAKE_SCRIPT, FAKE_SCRIPT_B, LiveServer

from oversight.agents import ScriptedModel, parse_script
from oversight.events import EventPayload, EventStore, StoredEvent
from oversight_contracts.events import AgentStatus, ChangeResolved

DOC = '"""Take the coupon\'s percent off the total; unknown codes change nothing."""'
SHORTER = '"""Take the coupon\'s percent off the total."""'
REVISION_A = [
    {
        "tool_calls": [
            {
                "name": "edit_file",
                "arguments": {"path": "shop/pricing.py", "old_text": DOC, "new_text": SHORTER},
            }
        ]
    },
    {"tool_calls": [{"name": "finish", "arguments": {"summary": "Shortened the docstring."}}]},
]
"""What agent A does after Request changes: it edits its own docstring again and finishes."""
STEPS_A = 6
"""Tool calls of A's first run: list_files and search in one reply, then read, edit, run_tests and
finish."""
MODEL_TURNS_A = 5
"""Model calls of A's first run."""


CHANGE = "/changes/change-1"


async def request(
    s: LiveServer, comment: str = "Shorter, please.", change: str = "change-1"
) -> Any:
    return await s.client.post(f"/changes/{change}/request-changes", json={"comment": comment})


def states(events: list[Any], agent: str = "agent-1") -> list[str]:
    return [
        e.payload.state for e in events if e.type == "agent.status" and e.payload.agent_id == agent
    ]


def resolutions(events: list[Any]) -> list[ChangeResolved]:
    return [e.payload for e in events if e.type == "change.resolved"]


def last_status(events: list[Any]) -> AgentStatus:
    return [e.payload for e in events if e.type == "agent.status"][-1]


@contextlib.asynccontextmanager
async def opened(*gates: asyncio.Event) -> AsyncIterator[None]:
    """Sets each gate on the way out, whether the body ended or failed."""
    try:
        yield
    finally:
        for gate in gates:
            gate.set()


def revisable(path: Path = FAKE_SCRIPT, turns: list[dict[str, Any]] | None = None) -> str:
    """A fixture script with `turns` (A's by default) after its last one, for the revision."""
    data = json.loads(path.read_text(encoding="utf-8"))
    data["turns"] += REVISION_A if turns is None else turns
    return json.dumps(data)


def metered(script: str) -> str:
    """The script with usage on every turn, so a metered agent leaves no call of unknown cost in
    the ledger, which would close the brake."""
    data = json.loads(script)
    for turn in data["turns"]:
        turn.setdefault("usage", {"input_tokens": 1000, "output_tokens": 100, "cached_tokens": 0})
    return json.dumps(data)


def revisable_b() -> str:
    """Slot B's script, with a revision that only finishes: its edit stays as the first run's."""
    finish = {"tool_calls": [{"name": "finish", "arguments": {"summary": "Kept as it was."}}]}
    return revisable(FAKE_SCRIPT_B, [finish])


def scripted_a() -> ScriptedModel:
    return ScriptedModel(parse_script(revisable()))


async def ready(s: LiveServer, agent: str = "agent-1", after: int = 0) -> list[Any]:
    """The events up to the agent's next `ready_for_review`."""
    return await s.until(
        lambda e: (
            e.type == "agent.status"
            and e.payload.agent_id == agent
            and e.payload.state == "ready_for_review"
        ),
        after,
    )


class HeldRequests:
    """Each agent's `changes_requested` waits before the store until its gate opens: `at[agent]` is
    set once it is waiting, and the store has not taken it. Others go through. Leaving the context
    opens every gate, and any later request goes through at once."""

    def __init__(self, store: EventStore, monkeypatch: pytest.MonkeyPatch) -> None:
        self.at: defaultdict[str, asyncio.Event] = defaultdict(asyncio.Event)
        self.gates: defaultdict[str, asyncio.Event] = defaultdict(asyncio.Event)
        self._all_open = False
        real = store.publish

        async def publish(payload: EventPayload) -> StoredEvent:
            if isinstance(payload, ChangeResolved) and payload.decision == "changes_requested":
                self.at[payload.agent_id].set()
                if not self._all_open:
                    await self.gates[payload.agent_id].wait()
            return await real(payload)

        monkeypatch.setattr(store, "publish", publish)

    def open(self, agent: str) -> None:
        self.gates[agent].set()

    async def __aenter__(self) -> "HeldRequests":
        return self

    async def __aexit__(self, *_: object) -> None:
        self._all_open = True
        for gate in self.gates.values():
            gate.set()
