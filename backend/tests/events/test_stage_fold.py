"""Per-change clocks preserve recorded intervals, unknowns and the committed horizon."""

import ast
import re
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter

from oversight.events import fold_stages, read_jsonl
from oversight_contracts.events import ServerEvent
from oversight_contracts.stages import StageTime

ROOT = Path(__file__).resolve().parents[3]
RECORDINGS = ROOT / "fixtures/events"
ADAPTER = TypeAdapter(ServerEvent)


@lru_cache
def templates() -> dict[str, ServerEvent]:
    events = read_jsonl(RECORDINGS / "scripted-level2.jsonl")
    events += [
        e
        for e in read_jsonl(RECORDINGS / "handwritten-inbox-kinds.jsonl")
        if e.type == "change.assessed"
    ]
    return {event.type: event for event in events}


def resolution(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep edited decision envelopes valid under the existing provenance contract."""
    actor = payload["decided_by"]
    if actor == "rules":
        source = next(
            e
            for e in read_jsonl(RECORDINGS / "handwritten-inbox-kinds.jsonl")
            if e.type == "change.resolved" and e.payload.decided_by == "rules"
        )
        original = source.payload.model_dump()
        original.update({key: payload[key] for key in ["change_id", "agent_id", "revision"]})
        original["assessment"].update(
            {key: original[key] for key in ["change_id", "agent_id", "revision"]}
        )
        return original
    payload.update(reason=None, review_seq=None, requested_by=None)
    if actor in {"cross_review", "lead_agent"}:
        payload.update(decision="changes_requested", comment="Revise", head_commit="head")
        if actor == "cross_review":
            payload["review_seq"] = 1
        else:
            payload["requested_by"] = "agent-2"
    approved = payload["decision"] == "approved"
    payload["merge"] = ("conflict" if payload["conflict"] else "merged") if approved else None
    payload["merge_commit"] = "merged" if payload["merge"] == "merged" else None
    return payload


def log(*rows: tuple[str, int, dict[str, Any]]) -> list[ServerEvent]:
    """Restamp validated envelopes; payload overrides default to this change and revision."""
    events = []
    for seq, (kind, ms, overrides) in enumerate(rows, 1):
        payload: dict[str, Any]
        if kind == "change.reviewed":
            payload = dict(
                change_id="change-1",
                revision=1,
                base_commit="base",
                head_commit="head",
                main_commit="main",
                candidate_tree="tree",
                author_model="author",
                reviewer_model="reviewer",
                reviewer_provider="fake",
                reasoning=None,
                verdict="clean",
                findings=[],
                complete=True,
                problems=[],
            )
        else:
            payload = templates()[kind].payload.model_dump()
            if "change_id" in payload:
                payload["change_id"] = "change-1"
            if "agent_id" in payload:
                payload["agent_id"] = "agent-1"
            if "revision" in payload:
                payload["revision"] = 1
            if kind == "change.resolved":
                payload.update(decided_by="person", decision="rejected")
        overrides = dict(overrides)
        if kind == "tests.executed":
            payload["suite"] = dict(payload["suite"], duration_ms=overrides.pop("duration", 10))
        payload.update(overrides)
        if kind == "change.resolved":
            payload = resolution(payload)
        ts = datetime(2026, 10, 31, tzinfo=UTC) + timedelta(milliseconds=ms)
        events.append(
            ADAPTER.validate_python(
                dict(
                    seq=seq,
                    ts=ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z",
                    type=kind,
                    payload=payload,
                )
            )
        )
    return events


def row(stage: StageTime) -> tuple[int | None, int, int, bool]:
    return stage.ms, stage.known_ms, stage.intervals, stage.open


@pytest.mark.parametrize("seqs", [[], [2], [1, 3], [1, 1], [2, 1], [1, "2"], [True]])
def test_incomplete_or_noninteger_inventory(seqs):
    event = log(("agent.status", 0, {"state": "working"}))[0]
    assert fold_stages([event.model_copy(update={"seq": seq}) for seq in seqs], "change-1") is None


def test_statuses_use_change_id_and_close_on_paused_and_halted():
    events = log(
        ("agent.status", 0, {"state": "starting"}),
        ("agent.status", 2, {"state": "working"}),
        ("agent.status", 5, {"state": "done", "change_id": "change-2"}),
        ("agent.status", 10, {"state": "paused"}),
        ("agent.status", 20, {"state": "revising"}),
        ("agent.status", 25, {"state": "halted"}),
        ("agent.status", 30, {"state": "working", "change_id": "change-2", "agent_id": "agent-2"}),
    )
    stages = fold_stages(events, "change-1")
    assert stages is not None
    assert row(stages.agent_work) == (15, 15, 2, False)
    other = fold_stages(events, "change-2")
    assert other is not None and row(other.agent_work) == (0, 0, 1, True)


def test_open_work_uses_last_unrelated_event_and_is_pure():
    events = log(
        ("agent.status", 10, {"state": "working"}),
        ("agent.status", 50, {"state": "done", "change_id": "change-2"}),
    )
    before = [e.model_dump() for e in events]
    first = fold_stages(events, "change-1")
    assert first is not None and row(first.agent_work) == (40, 40, 1, True)
    assert first == fold_stages(events, "change-1")
    assert [e.model_dump() for e in events] == before


@pytest.mark.parametrize("receipt", ["change.facts_recorded", "change.reviewed", "change.assessed"])
def test_checks_use_last_receipt_inside_same_revision_window(receipt):
    events = log(
        ("change.proposed", 0, {}),
        ("change.facts_recorded", 5, {}),
        (receipt, 10, {}),
        (receipt, 20, {"revision": 2}),
        (receipt, 25, {"change_id": "change-2"}),
        ("change.resolved", 30, {}),
        (receipt, 40, {}),
    )
    stages = fold_stages(events, "change-1")
    assert stages is not None and row(stages.checks) == (10, 10, 1, False)
    assert row(stages.waiting_for_person) == (20, 20, 1, False)


@pytest.mark.parametrize("closed", [True, False])
def test_checks_without_receipt_are_absent_when_closed_and_open_otherwise(closed):
    end = "change.resolved" if closed else "agent.step"
    stages = fold_stages(log(("change.proposed", 0, {}), (end, 20, {})), "change-1")
    assert stages is not None
    assert row(stages.checks) == ((0, 0, 0, False) if closed else (20, 20, 1, True))
    assert row(stages.waiting_for_person) == ((20, 20, 1, False) if closed else (0, 0, 0, False))


def test_tests_count_only_execution_receipts_in_fixed_phase_order():
    suite = templates()["change.proposed"].payload.model_dump()["tests"]
    events = log(
        ("change.proposed", 0, {"tests": dict(suite, duration_ms=900)}),
        ("agent.step", 1, {"tests": dict(suite, duration_ms=800)}),
        ("tests.executed", 2, {"phase": "head_probe", "duration": 5}),
        ("tests.executed", 3, {"phase": "base_probe", "duration": 4}),
        ("tests.executed", 4, {"phase": "combined", "duration": 3}),
        ("tests.executed", 5, {"phase": "proposal", "duration": 2}),
        ("tests.executed", 6, {"phase": "agent", "duration": 1}),
        ("tests.executed", 7, {"phase": "agent", "duration": 6}),
        ("tests.executed", 8, {"phase": "agent", "duration": 100, "change_id": "change-2"}),
        (
            "change.resolved",
            9,
            {"decision": "approved", "conflict": {"kind": "tests", "paths": [], "tests": suite}},
        ),
    )
    stages = fold_stages(events, "change-1")
    assert stages is not None and row(stages.test_runs) == (21, 21, 6, False)
    assert [(p.phase, p.ms, p.runs) for p in stages.test_phases] == [
        ("agent", 7, 2),
        ("proposal", 2, 1),
        ("combined", 3, 1),
        ("base_probe", 4, 1),
        ("head_probe", 5, 1),
    ]


def test_negative_suite_is_unknown_but_still_counts_a_run():
    stages = fold_stages(
        log(
            ("tests.executed", 0, {"phase": "proposal", "duration": -1}),
            ("tests.executed", 1, {"phase": "proposal", "duration": 5}),
        ),
        "change-1",
    )
    assert stages is not None and row(stages.test_runs) == (None, 5, 2, False)
    assert [(p.phase, p.ms, p.runs) for p in stages.test_phases] == [("proposal", 5, 2)]
    assert len(stages.problems) == 1 and "seq 1" in stages.problems[0]


@pytest.mark.parametrize(
    "stage,rows",
    [
        (
            "agent_work",
            [("agent.status", 10, {"state": "working"}), ("agent.status", 0, {"state": "done"})],
        ),
        ("checks", [("change.proposed", 10, {}), ("change.facts_recorded", 0, {})]),
        ("waiting_for_person", [("change.proposed", 10, {}), ("change.resolved", 0, {})]),
    ],
)
def test_backwards_interval_is_unknown(stage, rows):
    stages = fold_stages(log(*rows), "change-1")
    assert stages is not None and row(getattr(stages, stage)) == (None, 0, 1, False)
    assert any("seq 2" in p and "Timestamp" in p for p in stages.problems)


def test_invalid_timestamp_does_not_break_a_read():
    events = log(("agent.status", 10, {"state": "working"}))
    events[0] = events[0].model_copy(update={"ts": "invalid"})
    stages = fold_stages(events, "change-1")
    assert stages is not None and row(stages.agent_work) == (None, 0, 1, True)


def test_problem_cap_and_seq_order_for_more_than_twenty_unknown_intervals():
    rows = []
    for n in range(21):
        rows.extend([("change.proposed", n, {}), ("change.resolved", n, {"decided_by": None})])
    stages = fold_stages(log(*rows), "change-1")
    assert stages is not None and stages.waiting_for_person.intervals == 21
    assert len(stages.problems) == 20
    assert [int(re.findall(r"seq (\d+)", p)[0]) for p in stages.problems] == list(range(2, 41, 2))
    assert all(
        not any(word in p for word in ["Error", "Exception", "/", "review time"])
        for p in stages.problems
    )
    # Finalization discovers the early backwards checks interval after the suite failure.
    stages = fold_stages(
        log(
            ("change.proposed", 10, {}),
            ("change.facts_recorded", 0, {}),
            ("tests.executed", 20, {"duration": -1}),
            ("change.resolved", 30, {"decided_by": None}),
        ),
        "change-1",
    )
    assert stages is not None
    assert [int(re.findall(r"seq (\d+)", p)[0]) for p in stages.problems] == [2, 3, 4]


@pytest.mark.parametrize(
    "name,change,work,checks,waiting,tests,phases,problem_seq",
    [
        (
            "recorded-demo",
            "change-1",
            (21457, 21457, 1, False),
            (0, 0, 0, False),
            (None, 0, 1, False),
            (0, 0, 0, False),
            [],
            64,
        ),
        (
            "recorded-demo",
            "change-2",
            (11525, 11525, 1, False),
            (0, 0, 0, False),
            (None, 0, 1, False),
            (0, 0, 0, False),
            [],
            67,
        ),
        (
            "scripted-level2",
            "change-1",
            (8293, 8293, 2, False),
            (20, 20, 1, False),
            (9179, 9179, 1, False),
            (582, 582, 2, False),
            [("agent", 299, 1), ("proposal", 283, 1)],
            None,
        ),
        (
            "scripted-level2",
            "change-2",
            (13925, 13925, 2, False),
            (2045, 2045, 3, False),
            (None, 2577, 3, False),
            (3044, 3044, 11, False),
            [
                ("agent", 633, 2),
                ("proposal", 827, 3),
                ("base_probe", 784, 3),
                ("head_probe", 800, 3),
            ],
            80,
        ),
    ],
)
def test_recordings_expected_rows(name, change, work, checks, waiting, tests, phases, problem_seq):
    stages = fold_stages(read_jsonl(RECORDINGS / f"{name}.jsonl"), change)
    assert stages is not None
    assert [
        row(getattr(stages, f)) for f in ["agent_work", "checks", "waiting_for_person", "test_runs"]
    ] == [work, checks, waiting, tests]
    assert [(p.phase, p.ms, p.runs) for p in stages.test_phases] == phases
    assert (
        stages.problems == []
        if problem_seq is None
        else len(stages.problems) == 1 and f"seq {problem_seq}" in stages.problems[0]
    )


@pytest.mark.parametrize(
    "n,change,field,expected",
    [
        (6, "change-1", "agent_work", (28, 28, 1, True)),
        (52, "change-2", "checks", (607, 607, 1, True)),
        (52, "change-2", "waiting_for_person", (0, 0, 0, False)),
        (54, "change-2", "agent_work", (7015, 7015, 1, False)),
        (54, "change-2", "checks", (634, 634, 1, False)),
        (54, "change-2", "waiting_for_person", (22, 22, 1, True)),
    ],
)
def test_recorded_prefix_rows(n, change, field, expected):
    stages = fold_stages(read_jsonl(RECORDINGS / "scripted-level2.jsonl")[:n], change)
    assert stages is not None and row(getattr(stages, field)) == expected


def test_every_prefix_invariants_and_nonvacuous_open_stages():
    opened = set()
    for name in ["recorded-demo", "scripted-level2"]:
        events = read_jsonl(RECORDINGS / f"{name}.jsonl")
        changes = {
            identifier
            for e in events
            if isinstance(identifier := getattr(e.payload, "change_id", None), str)
        }
        for n in range(1, len(events) + 1):
            for change in changes:
                stages = fold_stages(events[:n], change)
                assert stages is not None
                for field in ["agent_work", "checks", "waiting_for_person", "test_runs"]:
                    stage = getattr(stages, field)
                    assert stage.ms is None or stage.ms == stage.known_ms
                    assert stage.ms is None or stage.known_ms <= stage.ms
                    if stage.intervals == 0:
                        assert row(stage) == (0, 0, 0, False)
                    if stage.open:
                        opened.add(field)
    assert {"agent_work", "checks", "waiting_for_person"} <= opened


def test_fold_has_no_io_store_or_wall_clock_calls():
    tree = ast.parse((ROOT / "backend/src/oversight/events/stage_fold.py").read_text())
    inspected = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom | ast.Call):
            inspected += 1
            source = ast.unparse(node)
            assert not re.search(r"\b(time|asyncio|EventStore)\b|datetime\.now|\bopen\(", source)
    assert inspected >= 5
