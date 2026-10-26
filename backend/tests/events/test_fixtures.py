"""Every log in fixtures/events/ is valid against the contract and holds no absolute path.

Recorded logs go public with the repository (docs/proposal.md, 4.1 and 4.8), and the generated
TypeScript types validate nothing at run time, so this test is the check.
"""

import json
import re
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from oversight_contracts.events import AgentStatusEvent, ServerEvent

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "fixtures" / "events"
LOGS = sorted(FIXTURES.glob("*.jsonl"))
SERVER_EVENT: TypeAdapter[ServerEvent] = TypeAdapter(ServerEvent)

_ABSOLUTE = re.compile(
    r"^(/|~/)"  # a value that is itself an absolute path
    r"|(?<![\w.])(/home|/Users|/root|/tmp|/mnt|/var|/private|/opt|/srv|/workspace)/"
    r"|(?<![\w])[A-Za-z]:[\\/]"  # a Windows drive
    r"|\\Users\\"
)


def absolute_paths(value: object) -> list[str]:
    """Every string in a decoded event that is, or holds, an absolute path."""
    if isinstance(value, str):
        hit = _ABSOLUTE.search(value) or str(REPO) in value or str(Path.home()) in value
        return [value[:80]] if hit else []
    if isinstance(value, dict):
        return [found for key, item in value.items() for found in absolute_paths([key, item])]
    if isinstance(value, list):
        return [found for item in value for found in absolute_paths(item)]
    return []


def problems(log: Path) -> list[str]:
    found: list[str] = []
    lines = log.read_text(encoding="utf-8").splitlines()
    for number, line in enumerate(lines, start=1):
        try:
            event = SERVER_EVENT.validate_json(line)
        except ValidationError as error:
            found.append(f"line {number}: invalid: {error.errors()[0]['msg']}")
            continue
        if event.seq != number:
            found.append(f"line {number}: seq {event.seq}, expected {number}")
        found.extend(
            f"line {number}: absolute path {hit!r}" for hit in absolute_paths(json.loads(line))
        )
    return found


def test_there_are_logs_to_check() -> None:
    assert FIXTURES / "handwritten-level1.jsonl" in LOGS


@pytest.mark.parametrize("log", LOGS, ids=[log.name for log in LOGS])
def test_every_line_is_a_valid_event_in_seq_order_with_no_absolute_path(log: Path) -> None:
    assert problems(log) == []


@pytest.mark.parametrize(
    "planted",
    ["/home/someone/demo-repo/shop/pricing.py", "C:\\Users\\someone\\shop\\pricing.py"],
)
def test_a_planted_absolute_path_is_caught(tmp_path: Path, planted: str) -> None:
    original = (FIXTURES / "handwritten-level1.jsonl").read_text(encoding="utf-8")
    assert original.count('"path":"shop/pricing.py"') > 0
    log = tmp_path / "planted.jsonl"
    log.write_text(
        original.replace('"path":"shop/pricing.py"', f'"path":{json.dumps(planted)}', 1),
        encoding="utf-8",
    )
    assert any("absolute path" in problem for problem in problems(log))


def test_relative_paths_and_code_are_not_mistaken_for_absolute_ones() -> None:
    assert absolute_paths({"path": "shop/pricing.py", "code": "base * percent // 100"}) == []
    assert absolute_paths({"message": "failed in /tmp/x/test.py"}) != []


def test_an_invalid_line_is_caught(tmp_path: Path) -> None:
    log = tmp_path / "broken.jsonl"
    log.write_text('{"seq":1,"ts":"2026-10-27T14:03:12.000Z","type":"nope","payload":{}}\n')
    assert problems(log)[0].startswith("line 1: invalid")


def test_the_level1_log_follows_the_order_of_section_4_5() -> None:
    log = FIXTURES / "handwritten-level1.jsonl"
    events = [SERVER_EVENT.validate_json(line) for line in log.read_text().splitlines()]
    types = [event.type for event in events]
    assert types[:2] == ["run.started", "graph.snapshot"]
    states = [e.payload.state for e in events if isinstance(e, AgentStatusEvent)]
    assert states == ["starting", "working", "ready_for_review", "done"]
    # A read: focus, then step. An edit: change.updated, focus, step.
    edit = types.index("change.updated")
    assert types[edit : edit + 3] == ["change.updated", "agent.focus", "agent.step"]
    merged = types.index("change.resolved")
    assert types[merged:] == ["change.resolved", "graph.delta", "agent.status"]
    steps = sum(1 for t in types if t == "agent.step")
    assert sum(1 for t in types if t == "usage.tick") == steps
