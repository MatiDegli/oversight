"""Exercise the external bench against the same real fake-model server as backend e2e."""

import asyncio
import json

import pytest
from live_helpers import GatedModel, Scripts, live_server, live_settings  # pyright: ignore[reportMissingImports]
from tools.eval.grade import Tariff
from tools.eval.run import execute
from tools.eval.tasks import BenchError, load_tasks, read_template

TASK = load_tasks()[0]
FREE = Tariff(input=0, output=0)


def script(task=TASK, *, fix=True, tamper=False, halt=False, usage=True):
    turns = []
    paths = task.allowed_changed_paths + (["tests/test_pricing.py"] if tamper else [])
    before, after = task.files(), task.files(reference=True)
    for path in paths:
        turns.append({"tool_calls": [{"name": "read_file", "arguments": {"path": path}}]})
        if fix or tamper:
            turns.append(
                {
                    "tool_calls": [
                        {
                            "name": "edit_file",
                            "arguments": {
                                "path": path,
                                "old_text": before[path],
                                "new_text": "def test_always_passes():\n    pass\n"
                                if path.startswith("tests/")
                                else after[path],
                            },
                        }
                    ]
                }
            )
    if not halt:
        turns.append({"tool_calls": [{"name": "finish", "arguments": {"summary": "Repaired."}}]})
    else:
        # Four identical successful calls trigger the runtime's no-progress cutoff.
        turns += [{"tool_calls": [{"name": "list_files", "arguments": {}}]} for _ in range(4)]
    if usage:
        for turn in turns:
            turn["usage"] = {"input_tokens": 100, "output_tokens": 20}
    return json.dumps({"task": task.prompt, "turns": turns})


def result(out):
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(rows) == 1
    return rows[0]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mode,outcome",
    [("solved", "solved"), ("incorrect", "failed"), ("halted", "failed"), ("tamper", "failed")],
)
async def test_end_to_end(mode, outcome, tmp_path):
    factory = Scripts(
        [script(fix=mode != "incorrect", tamper=mode == "tamper", halt=mode == "halted")]
    )
    settings = live_settings(tmp_path)
    out = tmp_path / "results" / "results.jsonl"
    async with live_server(settings, factory) as server:
        await execute(str(server.client.base_url), "fake", 1, out, [TASK], FREE)
        row = result(out)
        assert row["outcome"] == outcome
        assert row["tokens"]["input"] > 0 and row["cost_usd"] == 0
        assert row["steps"] > 0 and row["duration_s"] > 0
        if mode == "tamper":
            assert row["test_result"]["policy"] == "failed"
        assert factory.calls == 1
        events = server.events()
        assert any(e.type == "change.resolved" and e.payload.decision == "rejected" for e in events)
        assert not any(
            e.type == "change.resolved" and e.payload.decision == "approved" for e in events
        )
        for path, content in read_template().items():
            assert (settings.runtime_dir / "workspace" / path).read_text() == content
        assert not list((settings.runtime_dir / "worktrees").iterdir())
        # Same ledger, same server: completed tasks are skipped before any request.
        await execute(str(server.client.base_url), "fake", 1, out, [TASK], FREE)
        assert factory.calls == 1
        assert result(out) == row


@pytest.mark.anyio
@pytest.mark.parametrize("missing_counts", [True, False])
async def test_unknown_usage_stops_and_persists_across_server_restart(tmp_path, missing_counts):
    out = tmp_path / "results" / "results.jsonl"
    factory = Scripts([script(usage=not missing_counts)])
    tariff = FREE if missing_counts else None
    async with live_server(live_settings(tmp_path), factory) as server:
        with pytest.raises(BenchError, match="unknown cost"):
            await execute(str(server.client.base_url), "fake", 1, out, [TASK], tariff)
        row = result(out)
        assert row["outcome"] == "stopped"
        assert row["cost_usd"] == "unknown"
        # Stop may cancel another call before it can report usage; totals stay unknown.
        assert row["tokens"] == {"input": "unknown", "output": "unknown"}
    async with live_server(live_settings(tmp_path), factory) as server:
        with pytest.raises(BenchError, match="unknown cost"):
            await execute(str(server.client.base_url), "fake", 1, out, [TASK], tariff)
        assert factory.calls == 1
        assert result(out) == row


@pytest.mark.anyio
async def test_two_tasks_use_one_agent_at_a_time(tmp_path):
    tasks = load_tasks()[:2]
    factory = Scripts([script(t) for t in tasks])
    out = tmp_path / "results" / "results.jsonl"
    async with live_server(live_settings(tmp_path), factory) as server:
        await execute(str(server.client.base_url), "fake", 1, out, tasks, FREE)
        rows = [json.loads(line) for line in out.read_text().splitlines()]
        assert [r["outcome"] for r in rows] == ["solved", "solved"]
        active = set()
        for event in server.events():
            if event.type == "agent.status" and event.payload.state == "starting":
                assert not active
                active.add(event.payload.agent_id)
            if event.type == "change.resolved":
                active.discard(event.payload.agent_id)
        assert not active and factory.calls == 2


@pytest.mark.anyio
async def test_explicit_cancellation_stops_and_restores(tmp_path):
    model = GatedModel()
    out = tmp_path / "results" / "results.jsonl"
    settings = live_settings(tmp_path)
    async with live_server(settings, lambda p, s: model) as server:
        task = asyncio.create_task(
            execute(str(server.client.base_url), "fake", 1, out, [TASK], FREE)
        )
        await asyncio.wait_for(model.entered.wait(), 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert result(out)["outcome"] == "stopped"
        assert model.cancelled.is_set()
        assert not list((settings.runtime_dir / "worktrees").iterdir())
