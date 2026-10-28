"""Internal, local-only timing alongside the evaluator's existing evidence."""

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from oversight.telemetry import CURRENT, Observer, annotate, mark, span


@contextmanager
def attempt_trace(api: Any, run_id: str, directory: Path) -> Generator[None]:
    if not getattr(api, "trace_enabled", False):
        yield
        return
    attempt_id = uuid4().hex
    observer = Observer(
        directory / f"timing-{attempt_id}.jsonl",
        {"run": run_id, "attempt": attempt_id},
        source="evaluator",
    )
    token = CURRENT.set(observer)
    api.trace_attempt = attempt_id
    try:
        mark("attempt_start")
        with span("evaluation_attempt"):
            yield
    finally:
        api.trace_attempt = None
        CURRENT.reset(token)


def observed(event: Any) -> None:
    # Only correlation metadata, never the SSE payload or its diagnostic text.
    agent = getattr(event.payload, "agent_id", None)
    if agent:
        annotate(agent=agent)
    if event.type == "change.proposed":
        mark("proposal_ready", event_seq=event.seq)
    if event.type == "agent.status" and event.payload.state in {
        "ready_for_review",
        "halted",
        "stopped",
        "done",
    }:
        mark("terminal_observation", event_seq=event.seq, state=event.payload.state)
