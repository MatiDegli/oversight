"""The existing L2 manual review trace stays unchanged with cross-review disabled."""

import asyncio
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from live_helpers import DEADLINE_S, BySlot, is_type, live_server, start, two_fakes
from revise_helpers import ready, request, scripted_a

from oversight.settings import REPO_ROOT

pytestmark = pytest.mark.anyio


async def test_switch_off_preserves_level2_manual_review_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forbidden = Mock(side_effect=AssertionError("Disabled review must add no work"))
    monkeypatch.setattr("oversight.review_config.configured_reviewer", forbidden)
    monkeypatch.setattr("oversight.agents.reviewer.review_candidate", forbidden)
    # The old L2 recording fixes the proposal -> person request trace. Other agents' events
    # interleave in that log, so compare the full ordered trace belonging to this agent.
    recorded = [
        json.loads(line)
        for line in (REPO_ROOT / "fixtures/events/scripted-level2.jsonl").read_text().splitlines()
    ]
    end = next(
        e["seq"]
        for e in recorded
        if e["type"] == "change.resolved" and e["payload"]["agent_id"] == "agent-1"
    )
    begin = next(
        e["seq"]
        for e in recorded
        if e["type"] == "change.proposed" and e["payload"]["agent_id"] == "agent-1"
    )
    expected = [
        e["type"]
        for e in recorded
        if begin <= e["seq"] <= end and e["payload"].get("agent_id") == "agent-1"
    ]
    model = scripted_a()
    clients = BySlot(lambda _profile, _slot: model)
    settings = two_fakes(tmp_path, cross_review=False)
    async with asyncio.timeout(DEADLINE_S), live_server(settings, clients) as server:
        assert (await start(server, slot="A")).status_code == 201
        await ready(server)
        initial_calls = len(model.requests)
        assert initial_calls == 5  # Existing author script, no reviewer model call.
        assert (await request(server)).status_code == 202
        resolved = (await server.until(is_type("change.resolved", change_id="change-1")))[-1]
        events = server.events()
        proposal = next(e for e in events if e.type == "change.proposed")
        actual = [
            e.type
            for e in events
            if proposal.seq <= e.seq <= resolved.seq
            and getattr(e.payload, "agent_id", None) == "agent-1"
        ]
        assert actual == expected
        await ready(server, after=resolved.seq)
        assert len(model.requests) == initial_calls + 2  # Only the manual revision's turns.
        assert not any(e.type in {"change.reviewed", "error"} for e in server.events())
        assert all(e.payload.role == "agent" for e in server.events() if e.type == "usage.tick")
    assert not forbidden.called
