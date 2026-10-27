"""Reported completion counts already include reasoning; absent metrics stay unknown."""

import json
from typing import Any

import pytest
from live_helpers import Scripts, live_server, live_settings  # pyright: ignore[reportMissingImports]
from tools.eval.grade import Tariff, Usage
from tools.eval.ledger import Ledger
from tools.eval.run import execute
from tools.eval.tasks import BenchError, load_tasks

from oversight_contracts.events import UsageTick


def tick(**overrides):
    return UsageTick(
        agent_id="agent-1",
        model="fake-scripted",
        cached_tokens=None,
        spent_usd=0,
        budget_usd=1,
        brake="ok",
        **({"input_tokens": 120, "output_tokens": 30, "cost_usd": None} | overrides),
    )


def test_unknown_metric_is_not_zero_and_does_not_erase_known_counts():
    usage = Usage()
    usage.add(tick(cost_usd=0.01))
    usage.add(tick(input_tokens=None, cost_usd=0.02))
    assert usage.tokens.input == "unknown"
    assert usage.tokens.output == 60
    assert usage.cost == pytest.approx(0.03)
    usage.add(tick())
    assert usage.cost == "unknown"


def test_approved_tariff_fallback_prices_all_input_and_total_output():
    usage = Usage(Tariff(input=1, output=2))
    usage.add(tick())
    assert usage.tokens.model_dump() == {"input": 120, "output": 30}
    assert usage.cost == pytest.approx(0.00018)


@pytest.mark.parametrize(
    "overrides",
    [{"input_tokens": -1}, {"output_tokens": -1}, {"cost_usd": -1}, {"cost_usd": float("nan")}],
)
def test_invalid_accounting_is_refused(overrides):
    with pytest.raises(BenchError):
        Usage().add(tick(**overrides))


@pytest.mark.anyio
async def test_restart_keeps_known_spend_and_refuses_next_reservation(tmp_path):
    tasks = load_tasks()[:2]
    task = tasks[0]
    old, new = task.files()["shop/pricing.py"], task.files(reference=True)["shop/pricing.py"]
    turns: list[dict[str, Any]] = [
        {"tool_calls": [{"name": "read_file", "arguments": {"path": "shop/pricing.py"}}]},
        {
            "tool_calls": [
                {
                    "name": "edit_file",
                    "arguments": {"path": "shop/pricing.py", "old_text": old, "new_text": new},
                }
            ]
        },
        {"tool_calls": [{"name": "finish", "arguments": {"summary": "Fixed."}}]},
    ]
    for turn in turns:
        turn["usage"] = {"input_tokens": 100000, "output_tokens": 20000}
    factory = Scripts([json.dumps({"task": task.prompt, "turns": turns})])
    out = tmp_path / "results" / "r.jsonl"
    rates = Tariff(input=1, output=2)
    async with live_server(live_settings(tmp_path), factory) as server:
        await execute(str(server.client.base_url), "fake", 1, out, [task], rates)
    assert json.loads(out.read_text())["cost_usd"] == pytest.approx(0.42)
    # Simulate a crash after ledger fsync but before materializing the results file.
    out.unlink()
    async with live_server(live_settings(tmp_path), factory) as server:
        with pytest.raises(BenchError, match="cannot reserve"):
            await execute(str(server.client.base_url), "fake", 1, out, tasks, rates)
        assert factory.calls == 1
    ledger = Ledger(out, 1, rates)
    try:
        assert ledger.spent == pytest.approx(0.42)
        assert len(ledger.state.records) == 1
        assert len(out.read_text().splitlines()) == 1
    finally:
        ledger.close()
