"""Every log in fixtures/events/ is valid against the contract and holds no absolute path.

Recorded logs go public with the repository (docs/proposal.md, 4.1 and 4.8), and the generated
TypeScript types validate nothing at run time, so this test is the check.
"""

import json
import re
from pathlib import Path
from typing import cast

import pytest
from pydantic import TypeAdapter, ValidationError

from oversight_contracts.events import AgentStatusEvent, ServerEvent

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "fixtures" / "events"
LOGS = sorted(FIXTURES.glob("*.jsonl"))
SERVER_EVENT: TypeAdapter[ServerEvent] = TypeAdapter(ServerEvent)

# What counts as an absolute path, found anywhere inside a string:
#  - a "/" that starts a word: not glued to a word, ".", "/", ")", "]", "}", "<" or "*" before it
#    (so "a/b", "./a", "../a", "//", "</div>", "*/" and "(x)/y" are not), and followed by a name
#    character (so "a / b" is not). A URL's "//" is followed by "/", and its path follows a host.
#  - "~/", for the home directory, and "file:///", for a path written as a URL.
#  - a Windows drive: a letter, ":" and a separator, not glued to a word ("https://" is not).
_ABSOLUTE = re.compile(
    r"(?<![\w.~/\\)\]}<*])/(?=[\w.~@$%+-])"
    r"|(?<![\w.~/\\])~/"
    r"|\bfile:///"
    r"|(?<!\w)[A-Za-z]:[\\/]"
)


def absolute_paths(value: object) -> list[str]:
    """Every string in a decoded event, keys included, that is or holds an absolute path."""
    if isinstance(value, str):
        return [value[:80]] if _ABSOLUTE.search(value) else []
    if isinstance(value, dict):
        return [
            found
            for key, item in cast(dict[object, object], value).items()
            for found in absolute_paths(key) + absolute_paths(item)
        ]
    if isinstance(value, list):
        return [found for item in cast(list[object], value) for found in absolute_paths(item)]
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


def plant(tmp_path: Path, field: str, original: str, planted: str) -> Path:
    """A copy of the level 1 log with one string of it replaced by `planted`."""
    text = (FIXTURES / "handwritten-level1.jsonl").read_text(encoding="utf-8")
    assert text.count(f'"{field}":"{original}"') > 0
    log = tmp_path / "planted.jsonl"
    log.write_text(
        text.replace(f'"{field}":"{original}"', f'"{field}":{json.dumps(planted)}', 1),
        encoding="utf-8",
    )
    return log


@pytest.mark.parametrize(
    "planted",
    ["/home/someone/demo-repo/shop/pricing.py", "C:\\Users\\someone\\shop\\pricing.py"],
)
def test_a_planted_absolute_path_is_caught(tmp_path: Path, planted: str) -> None:
    log = plant(tmp_path, "path", "shop/pricing.py", planted)
    assert any("absolute path" in problem for problem in problems(log))


def test_an_absolute_path_inside_a_message_is_caught_whatever_its_directory(
    tmp_path: Path,
) -> None:
    summary = "Searched for compute_price(: 9 matches in 4 files"
    log = plant(tmp_path, "summary", summary, "failed in /etc/private/demo.py")
    assert any("absolute path" in problem for problem in problems(log))


@pytest.mark.parametrize(
    "text",
    [
        "failed in /etc/private/demo.py",
        "see /opt/demo/x.py:12",
        "PATH=/usr/local/bin:/srv/app",
        'open("/etc/passwd")',
        "(/data/run/out.log)",
        "config in ~/.config/tool",
        "file:///etc/hosts",
        "failed in D:\\work\\demo.py",
        "failed in D:/work/demo.py",
        "C:\\x",
    ],
)
def test_absolute_paths_are_found_in_any_string(text: str) -> None:
    assert absolute_paths({"message": text}) != [], text
    assert absolute_paths([{"nested": [text]}]) != [], text
    assert absolute_paths({text: 1}) != [], text


@pytest.mark.parametrize(
    "text",
    [
        "shop/pricing.py",
        "./shop/pricing.py",
        "../shop/pricing.py",
        "tests/test_pricing.py::test_total",
        "base * percent // 100",
        "total / count",
        "total/count",
        "a /* note */ b",
        "// a comment",
        "and/or",
        "10/3",
        "<div>x</div>",
        "https://example.com/docs/page",
        "http://localhost:8710/events",
        "Fetched https://example.com:8443/a/b?x=1 in 30 ms",
        "at 14:03:12",
        "see key:value and a:b",
    ],
)
def test_relative_paths_urls_and_code_are_not_mistaken_for_absolute_paths(text: str) -> None:
    assert absolute_paths({"message": text}) == [], text


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
