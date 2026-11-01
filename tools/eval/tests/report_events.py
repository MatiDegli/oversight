"""Independent event builders and hand-computed local check receipts."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import TypeAdapter

from oversight_contracts.events import ServerEvent

ADAPTER = TypeAdapter(ServerEvent)
MODEL = "muse-spark-1.3-contributor"


def event(kind: str, t: int = 0, change: int = 1, **fields) -> ServerEvent:
    payload: dict = dict(change_id=f"change-{change}", agent_id=f"agent-{change}")
    if kind == "agent.status":
        payload.update(slot="A", task="Implement", model=MODEL, step=0, max_steps=10, detail=None)
    elif kind == "usage.tick":
        payload.update(
            role="agent",
            model=MODEL,
            input_tokens=1000000,
            cached_tokens=0,
            output_tokens=0,
            cost_usd=999.0,
            spent_usd=999.0,
            budget_usd=25,
            brake="blocked",
        )
    elif kind == "change.proposed":
        payload.update(
            revision=1,
            task="Implement",
            summary=None,
            base_commit="base",
            head_commit="h1a",
            files=[],
            lines_added=0,
            lines_removed=0,
            graph=dict(
                nodes_added=[],
                nodes_modified=[],
                nodes_removed=[],
                edges_added=[],
                edges_removed=[],
            ),
            tests=dict(
                outcome="passed", passed=6, failed=0, skipped=0, duration_ms=1, failed_tests=[]
            ),
            impact=dict(
                seeds=[],
                entries=[],
                edges_followed="exact",
                max_depth=3,
                max_nodes=100,
                omitted_by_depth=0,
                omitted_by_limit=0,
                tests_reaching=0,
            ),
        )
    elif kind == "change.resolved":
        payload.update(
            revision=1,
            decision="approved",
            reason=None,
            comment=None,
            merge="merged",
            merge_commit="merged",
            conflict=None,
            decided_by="person",
        )
    elif kind == "change.reviewed":
        payload.pop("agent_id")
        payload.update(
            revision=1,
            base_commit="base",
            head_commit="h1a",
            main_commit="main",
            candidate_tree="tree",
            author_model=MODEL,
            reviewer_model="reviewer",
            reviewer_provider="fake",
            reasoning=None,
            verdict="clean",
            findings=[],
            complete=True,
            problems=[],
        )
    payload.update(fields)
    return ADAPTER.validate_python(dict(seq=1, ts=timestamp(t), type=kind, payload=payload))


def timestamp(t: int) -> str:
    return (
        (datetime(2026, 10, 31, 12, tzinfo=UTC) + timedelta(seconds=t))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def scenario() -> list[ServerEvent]:
    return renumber(
        [
            event("agent.status", state="working"),
            event("usage.tick", 5),
            event("agent.status", 10, 2, state="working"),
            event("agent.status", 20, 3, state="working"),
            event("usage.tick", 22, 3),
            event("agent.status", 30, 3, state="halted"),
            event(
                "change.resolved",
                35,
                3,
                revision=0,
                decision="rejected",
                reason="stopped",
                merge=None,
                merge_commit=None,
            ),
            event("usage.tick", 40, 2, input_tokens=0, output_tokens=500000),
            event("change.proposed", 50, 2, head_commit="h2"),
            event("agent.status", 50, 2, state="ready_for_review"),
            event("usage.tick", 55, 2, role="review", input_tokens=500000),
            event("change.reviewed", 60, 2, head_commit="h2"),
            event("change.proposed", 60),
            event("agent.status", 60, state="ready_for_review"),
            event("usage.tick", 65, role="review", input_tokens=500000),
            event(
                "change.reviewed",
                70,
                verdict="changes_requested",
                findings=[dict(severity="BLOCKING", text="Fix", path="boltons/x.py", line=10)],
            ),
            event(
                "change.resolved",
                71,
                decision="changes_requested",
                merge=None,
                merge_commit=None,
                decided_by="cross_review",
                review_seq=16,
                head_commit="h1a",
                comment="fix",
            ),
            event("agent.status", 72, state="revising"),
            event("change.resolved", 90, 2),
            event("usage.tick", 100, input_tokens=0, output_tokens=1000000),
            event("change.proposed", 120, revision=2, head_commit="h1b"),
            event("agent.status", 120, state="ready_for_review"),
            event("usage.tick", 125, role="review", input_tokens=500000),
            event("change.reviewed", 130, revision=2, head_commit="h1b"),
            event("change.resolved", 140, revision=2),
            event("agent.status", 141, state="done"),
        ]
    )


def renumber(events: list[ServerEvent]) -> list[ServerEvent]:
    return [e.model_copy(update={"seq": i}) for i, e in enumerate(events, 1)]


def receipts() -> list[dict]:
    def suite(code, passed, ids):
        return dict(exit_code=code, passed=passed, failed=len(ids), errors=0, failing_test_ids=ids)

    return [
        dict(kind="base", task="p04", result=suite(1, 5, ["t_new"])),
        dict(
            kind="revision",
            task="p04",
            change_id="change-1",
            revision=1,
            head_commit="h1a",
            result=suite(1, 4, ["t_new", "t_old"]),
        ),
        dict(
            kind="revision",
            task="p04",
            change_id="change-1",
            revision=2,
            head_commit="h1b",
            result=suite(0, 6, []),
        ),
        dict(
            kind="revision",
            task="p10",
            change_id="change-2",
            revision=1,
            head_commit="h2",
            result=dict(
                exit_code=0,
                production_unchanged=True,
                new_tests_present=True,
                new_tests_pass=True,
                mutation_threshold=True,
            ),
        ),
    ]


def files(tmp_path: Path, events=None, results=None) -> tuple[Path, Path]:
    log, checks = tmp_path / "run.jsonl", tmp_path / "results.jsonl"
    log.write_text(
        "".join(e.model_dump_json() + "\n" for e in (scenario() if events is None else events))
    )
    checks.write_text(
        "".join(json.dumps(r) + "\n" for r in (receipts() if results is None else results))
    )
    return log, checks
