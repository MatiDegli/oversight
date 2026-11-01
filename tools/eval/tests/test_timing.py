"""Opt-in timing reconciles one real fake evaluation and preserves its outcomes."""

import json

import pytest
from live_helpers import Scripts, live_server, live_settings  # pyright: ignore[reportMissingImports]
from tools.eval.run import execute
from tools.eval.tests.test_runner import FREE, TASK, result, script


@pytest.mark.anyio
async def test_observer_on_and_off_preserve_outcomes_and_reconcile_wall_time(tmp_path, monkeypatch):
    results = []
    for enabled in (False, True):
        root = tmp_path / str(enabled)
        out = root / "evidence" / "results.jsonl"
        monkeypatch.setenv("OVERSIGHT_EVAL_TRACE_DIR", str(out.parent))
        factory = Scripts([script()])
        async with live_server(live_settings(root), factory) as server:
            await execute(str(server.client.base_url), "fake", 1, out, [TASK], FREE, trace=enabled)
            results.append(result(out))
        paths = list(out.parent.glob("timing-*.jsonl"))
        if not enabled:
            assert not paths
            continue
        assert len(paths) == 1
        trace = [json.loads(line) for line in paths[0].read_text().splitlines()]
        roots = [r for r in trace if r["name"] == "evaluation_attempt"]
        assert len(roots) == 1
        root_span = roots[0]
        elapsed = root_span["end"] - root_span["start"]
        # Load adds scheduler delays; measured duration deltas peaked at 0.91% of elapsed.
        # Allow 1%, retaining the 50 ms floor for short, quiet attempts.
        assert elapsed == pytest.approx(results[-1]["duration_s"], rel=0.01, abs=0.05)
        children = sorted(
            (r for r in trace if r["parent_id"] == root_span["span_id"]),
            key=lambda r: r["start"],
        )
        assert [r["name"] for r in children] == ["start_request", "monitor"]
        cursor, gaps = root_span["start"], 0.0
        for child in children:
            assert child["start"] >= cursor  # no overlapping exclusive top-level phases
            gaps += child["start"] - cursor
            cursor = child["end"]
        gaps += root_span["end"] - cursor
        # Load adds scheduler gaps; measured maxima: 1.02% of elapsed and 0.030 s.
        # A 50 ms floor covers short attempts; 1% stays below the 2.29% minimum start phase.
        assert gaps < max(0.05, 0.01 * elapsed)
        assert sum(r["end"] - r["start"] for r in children) + gaps == pytest.approx(elapsed)
        assert {r["source"] for r in trace} == {"server", "evaluator"}
        assert len({r["attempt"] for r in trace}) == 1
        assert len({r["run"] for r in trace}) == 1
        assert any(r["name"] == "terminal_observation" for r in trace)
        assert any(r["name"] == "grading_done" for r in trace)
        turns = [r for r in trace if r["name"] == "model_turn"]
        assert turns and all(r["prompt_version"] for r in turns)
        for row in turns:
            assert row["requested_model"] == row["reported_model"] == "fake-scripted"
            assert row["reasoning_effort"] is None and row["output_allowance"] > 0
        assert TASK.prompt not in paths[0].read_text()
    for key in ("outcome", "steps", "tokens", "cost_usd", "test_result"):
        assert results[0][key] == results[1][key]


@pytest.mark.anyio
async def test_header_without_server_opt_in_refuses_before_start(tmp_path, monkeypatch):
    from tools.eval.tasks import BenchError
    from tools.eval.transport import API

    monkeypatch.delenv("OVERSIGHT_EVAL_TRACE_DIR", raising=False)
    factory = Scripts([script()])
    async with live_server(live_settings(tmp_path), factory) as server:
        api = API(str(server.client.base_url))
        api.trace_attempt = "a" * 32
        try:
            with pytest.raises(BenchError, match="HTTP 422"):
                await api.request(
                    "POST", "/agents", json={"task": TASK.prompt, "slot": "A", "model": "fake"}
                )
        finally:
            await api.client.aclose()
        assert factory.calls == 0


@pytest.mark.anyio
@pytest.mark.parametrize("attempt_id", ["", "abc", "A" * 32, "g" * 32])
@pytest.mark.parametrize("configured", [False, True])
async def test_malformed_attempt_has_its_own_422_reason(
    tmp_path, monkeypatch, attempt_id, configured
):
    from oversight.telemetry import TRACE_HEADER

    if configured:
        monkeypatch.setenv("OVERSIGHT_EVAL_TRACE_DIR", str(tmp_path / "evidence"))
    else:
        monkeypatch.delenv("OVERSIGHT_EVAL_TRACE_DIR", raising=False)
    factory = Scripts([script()])
    async with live_server(live_settings(tmp_path), factory) as server:
        response = await server.client.post(
            "/agents",
            headers={TRACE_HEADER: attempt_id},
            json={"task": TASK.prompt, "slot": "A", "model": "fake"},
        )
        assert response.status_code == 422
        message = response.json()["error"]["message"]
        assert message == f"invalid {TRACE_HEADER}: expected 32 lowercase hex characters"
        assert factory.calls == 0
