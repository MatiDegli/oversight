"""Shared frontend identities, computed by the server oracle over the source recordings.

Regenerate explicitly from the repository root:
uv run --frozen python backend/tests/events/test_team_selector_parity.py --write-golden
"""

# This parity guard intentionally reads the private, authoritative team fold.
# pyright: reportPrivateUsage=false

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from oversight.events import read_jsonl
from oversight.events.usage_view import _teams
from oversight_contracts.events import ServerEvent

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures/events"
RECORDINGS = ("recorded-demo.jsonl", "handwritten-lead-plans.jsonl")
GOLDEN = FIXTURES / "team-selector.golden.json"


def _projection(events: Sequence[ServerEvent]) -> dict[str, object]:
    # Insertion order is each id's first occurrence in the three UI identity sources.
    observed: dict[str, None] = {}
    for event in events:
        if event.type == "agent.status" or event.type == "lead.status":
            observed.setdefault(event.payload.agent_id, None)
        elif event.type == "plan.updated" and event.payload.started_by is not None:
            observed.setdefault(event.payload.started_by, None)

    teams = _teams(events)
    by_agent: dict[str, dict[str, str]] = {}
    for agent_id in observed:
        team, kind = teams.team_of(agent_id)
        by_agent[agent_id] = {"team": team, "kind": kind}
    # Resolve with the completed fold before deduplicating teams in first-start order.
    order = list(dict.fromkeys(identity["team"] for identity in by_agent.values()))
    return {"byAgent": by_agent, "teamOrder": order}


@pytest.mark.parametrize("recording", RECORDINGS)
def test_plan_ticks_belong_to_planners(recording: str) -> None:
    events = read_jsonl(FIXTURES / recording)
    actors = {
        event.payload.agent_id
        for event in events
        if event.type == "agent.status" or event.type == "lead.status"
    }
    for event in events:
        if event.type == "usage.tick" and event.payload.plan_id is not None:
            assert event.payload.agent_id not in actors, (recording, event.seq)


@pytest.mark.parametrize("recording", RECORDINGS)
def test_team_selector_golden(recording: str) -> None:
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert _projection(read_jsonl(FIXTURES / recording)) == expected[recording]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-golden", action="store_true", required=True)
    parser.parse_args()
    golden = {name: _projection(read_jsonl(FIXTURES / name)) for name in RECORDINGS}
    GOLDEN.write_text(json.dumps(golden, indent=2, sort_keys=True) + "\n", encoding="utf-8")
