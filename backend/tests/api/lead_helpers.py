"""Independent lead-thread scripts and committed-event waits for HTTP/race tests."""

# Tests inspect owned runtime tasks to establish cleanup barriers.
# pyright: reportPrivateUsage=false

import json
from typing import Any

from live_helpers import is_type

from oversight.agents import ScriptedModel, parse_script


class ThreadModels:
    def __init__(self, scripts: list[list[dict[str, Any]]] | None = None) -> None:
        self.scripts = scripts or [[{"text": "A factual answer.", "usage": {}}]]
        self.built: list[ScriptedModel] = []

    def __call__(self, profile: Any, _slot: Any) -> ScriptedModel:
        turns = self.scripts[min(len(self.built), len(self.scripts) - 1)]
        model = ScriptedModel(
            parse_script(json.dumps({"task": "task", "turns": turns}), profile), profile
        )
        self.built.append(model)
        return model


async def idle(s: Any, after: int = 0) -> None:
    await s.until(is_type("lead.status", agent_id="agent-1", state="idle"), after=after)
    # Observe the complete turn, including the owned client's cleanup.
    task = s.live.leads.leads["agent-1"].task
    if task is not None:
        await task


async def start_idle(s: Any) -> None:
    response = await s.client.post("/leads", json={"task": "Retained opening task"})
    assert response.status_code == 201, response.text
    await idle(s)


async def send(s: Any, text: str = "Next question") -> int:
    after = s.store.last_seq
    response = await s.client.post("/leads/agent-1/message", json={"text": text})
    assert response.status_code == 202 and response.content == b"", response.text
    await idle(s, after)
    return after


def replies(s: Any) -> list[Any]:
    return [e for e in s.events() if e.type == "lead.reply"]
