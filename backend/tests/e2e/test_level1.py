"""Level 1 end to end on the live app: two approved runs and a rejected one, with the fake model."""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from decision_helpers import observed_body
from live_helpers import DEADLINE_S, Scripts, is_type, live_server, live_settings, script_json

from oversight_contracts.commands import AgentCreated, GraphResponse

pytestmark = pytest.mark.anyio
PRICING = "shop/pricing.py"


def adding(name: str, after: str, body: str) -> str:
    """A run that reads shop/pricing.py, adds a function after the line `after`, runs the tests and
    finishes."""
    new = f"{after}\n\ndef {name}(total: int) -> int:\n    {body}\n"
    return script_json(
        f"Add {name} to {PRICING}.",
        [
            {"tool_calls": [{"name": "read_file", "arguments": {"path": PRICING}}]},
            {
                "tool_calls": [
                    {
                        "name": "edit_file",
                        "arguments": {"path": PRICING, "old_text": f"{after}\n", "new_text": new},
                    }
                ]
            },
            {"tool_calls": [{"name": "run_tests", "arguments": {}}]},
            {"tool_calls": [{"name": "finish", "arguments": {"summary": f"Added {name}."}}]},
        ],
    )


SHIPPING = "    return 0 if total >= FREE_SHIPPING_FROM else FLAT_SHIPPING"
FIRST = "    return shipping_cost(total) + 1"
SECOND = "    return first_helper(total) * 2"
SCRIPTS = [
    adding("first_helper", SHIPPING, FIRST.strip()),
    adding("second_helper", FIRST, SECOND.strip()),
    adding("third_helper", SECOND, "return second_helper(total) - 1"),
]


def first_index(events: list[Any], kind: str, after: int = -1, **payload: Any) -> int:
    for index, event in enumerate(events):
        if index > after and is_type(kind, **payload)(event):
            return index
    raise AssertionError(f"no {kind} {payload} after {after}")


async def test_two_approved_runs_and_a_rejected_one(tmp_path: Path) -> None:
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), Scripts(SCRIPTS)) as s,
    ):
        deltas: list[Any] = []
        for number, decision in ((1, "approve"), (2, "approve"), (3, "reject")):
            response = await s.client.post("/agents", json={"task": f"run {number}"})
            assert response.status_code == 201, response.text
            created = AgentCreated.model_validate(response.json())
            assert created.change_id == f"change-{number}"
            await s.until(
                is_type("agent.status", agent_id=created.agent_id, state="ready_for_review")
            )
            proposed = [e for e in s.events() if e.type == "change.proposed"][-1]
            assert proposed.payload.tests.outcome == "passed"
            decided = await s.client.post(
                f"/changes/{created.change_id}/{decision}", json=observed_body(s, created.change_id)
            )
            assert decided.status_code == 202
            await s.until(is_type("agent.status", agent_id=created.agent_id, state="done"))
            events = s.events()
            change, agent = created.change_id, created.agent_id
            updated = first_index(events, "change.updated", change_id=change)
            proposed_at = first_index(events, "change.proposed", updated, change_id=change)
            resolved_at = first_index(events, "change.resolved", proposed_at, change_id=change)
            done_at = first_index(events, "agent.status", resolved_at, agent_id=agent, state="done")
            deltas_after = [
                i for i, e in enumerate(events) if e.type == "graph.delta" and i > proposed_at
            ]
            if decision == "approve":
                assert len(deltas_after) == 1 and resolved_at < deltas_after[0] < done_at
            else:
                assert deltas_after == []
            resolved = [e for e in s.events() if e.type == "change.resolved"][-1]
            if decision == "approve":
                assert resolved.payload.merge == "merged"
                deltas.append([e for e in s.events() if e.type == "graph.delta"][-1])
            else:
                assert resolved.payload.decision == "rejected"
        assert len([e for e in s.events() if e.type == "graph.delta"]) == 2
        first, second = deltas
        assert second.payload.from_version == first.payload.to_version
        graph = GraphResponse.model_validate((await s.client.get("/graph")).json())
        ids = {node.id for node in graph.graph.nodes}
        assert {"shop.pricing:first_helper", "shop.pricing:second_helper"} <= ids
        assert "shop.pricing:third_helper" not in ids
        assert graph.seq == s.store.last_seq
