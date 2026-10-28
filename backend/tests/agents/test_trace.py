"""Sanitized trace correlation and real lock/ledger intervals on fake work."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from agent_helpers import Recorder, make_run, script
from test_client import COMPLETION, TOOLS, USER, FakeApi, client, ok, status

from oversight.agents import LedgerFile, LedgerState, UnknownUsage
from oversight.telemetry import CURRENT, Observer, bind, mark, span
from oversight.workspace import Tree, Workspace

pytestmark = pytest.mark.anyio


def rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


async def test_fake_trace_keeps_ids_and_never_contains_task_or_observations(
    small_tree: tuple[Workspace, Tree], tmp_path: Path
) -> None:
    path = tmp_path / "timing.jsonl"
    task = "PRIVATE_TASK_SENTINEL"
    model = script(
        [("read_file", {"path": "shop/mod.py"}), ("search", {"query": "value"})],
        [("finish", {"summary": "PRIVATE_NOTE_SENTINEL"})],
        task=task,
    )
    token = CURRENT.set(Observer(path, {"run": "run-test", "attempt": "attempt-test"}))
    try:
        end = await make_run(small_tree, model, Recorder(), task=task).run()
        mark("safe", prompt="PRIVATE_PROMPT", authorization="PRIVATE_CREDENTIAL")
    finally:
        CURRENT.reset(token)
    trace = rows(path)
    assert end.state == "ready_for_review"
    tools = [r for r in trace if r["name"] == "tool_execution"]
    assert [r["tool_call"] for r in tools] == ["call-1", "call-2", "call-3"]
    assert [r["turn"] for r in tools] == [1, 1, 2]
    assert all(r["agent"] == "agent-1" for r in tools)
    assert all(r["run"] == "run-test" and r["attempt"] == "attempt-test" for r in trace)
    turns = [r for r in trace if r["name"] == "model_turn"]
    assert all(r["reasoning_tokens"] is None and r["input_tokens"] is None for r in turns)
    assert [r["tool_count"] for r in turns] == [2, 1]
    assert any(r["name"] == "lock_wait" for r in trace)
    assert any(r["name"] == "proposal_ready" for r in trace)
    assert all(r["end"] >= r["start"] for r in trace)
    text = path.read_text()
    for private in (task, "PRIVATE_NOTE", "PRIVATE_PROMPT", "PRIVATE_CREDENTIAL", "return 1"):
        assert private not in text


async def test_http_attempts_retries_sizes_headers_and_reasoning_are_correlated(
    tmp_path: Path,
) -> None:
    path = tmp_path / "http.jsonl"
    usage = {
        "prompt_tokens": 700,
        "completion_tokens": 100,
        "completion_tokens_details": {"reasoning_tokens": 40},
    }
    success = ok(COMPLETION | {"usage": usage})
    success.headers.update({"x-request-id": "request-123", "authorization": "PRIVATE_CREDENTIAL"})
    api = FakeApi(status(429, {"x-ratelimit-remaining-tokens": "10"}), success)
    model = client(api, [])
    token = CURRENT.set(Observer(path, {"run": "run-test", "attempt": "test"}))
    try:
        with bind(agent="agent-1", turn=1), span("model_turn"):
            reply = await model.complete(USER, TOOLS)
    finally:
        CURRENT.reset(token)
        await model.aclose()
    attempts = [r for r in rows(path) if r["name"] == "http_attempt"]
    assert [r["http_attempt"] for r in attempts] == [1, 2]
    assert [r["http_status"] for r in attempts] == [429, 200]
    assert attempts[0]["rate_limits"] == {"x-ratelimit-remaining-tokens": "10"}
    assert attempts[1]["request_id"] == "request-123"
    assert all(r["request_bytes"] > 0 for r in attempts)
    assert any(r["name"] == "retry_sleep" for r in rows(path))
    assert any(r["name"] == "decoding" for r in rows(path))
    assert reply.usage.reasoning_tokens == 40 and reply.usage.cached_tokens is None
    assert "PRIVATE_CREDENTIAL" not in path.read_text() and '"messages"' not in path.read_text()


async def test_ledger_trace_separates_lock_wait_from_durable_write(tmp_path: Path) -> None:
    path = tmp_path / "ledger-trace.jsonl"
    ledger = LedgerFile(tmp_path / "ledger.json")
    ledger.state = LedgerState(0.0, ())
    token = CURRENT.set(Observer(path, {"run": "test", "attempt": "test"}))
    try:
        await ledger._lock.acquire()
        pending = asyncio.create_task(
            ledger.record(None, UnknownUsage("test", "agent-1", None, "2026-10-28T00:00:00Z"))
        )
        await asyncio.sleep(0.02)
        ledger._lock.release()
        await pending
    finally:
        CURRENT.reset(token)
    waits = [r for r in rows(path) if r["name"] == "ledger_lock_wait"]
    writes = [r for r in rows(path) if r["name"] == "ledger_write"]
    assert len(waits) == len(writes) == 1
    assert waits[0]["end"] - waits[0]["start"] >= 0.01
    assert writes[0]["start"] >= waits[0]["end"]
    assert ledger.state is not None and len(ledger.state.unknown) == 1
