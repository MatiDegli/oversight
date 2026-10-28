"""Optional Baseten prices affect receipts, never Meta credits or admission."""

import asyncio
import json
from pathlib import Path

import pytest
from live_helpers import DEADLINE_S, meta_settings, write_ledger

from oversight.agents import MUSE_SPARK_OPEN, ModelReply, ScriptedModel, Usage, parse_script
from oversight.app import LiveRun
from oversight.runtime_accounting import ReviewAccounting
from oversight.settings import Settings
from oversight_contracts.commands import StartAgent
from oversight_contracts.review import ChangeTarget

from .test_cross_review_lifecycle import events, until


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", None),
        ("oops", None),
        ("0", None),
        ("-1", None),
        ("nan", None),
        ("inf", None),
        ("0.25", 0.25),
    ],
)
def test_baseten_prices_parse_positive_amounts(tmp_path, monkeypatch, raw, expected):
    env_file = tmp_path / "settings.env"
    for name in ("BASETEN_INPUT_USD_PER_M", "BASETEN_OUTPUT_USD_PER_M"):
        monkeypatch.delenv(name, raising=False)
    env_file.write_text(f"BASETEN_INPUT_USD_PER_M={raw}\nBASETEN_OUTPUT_USD_PER_M={raw}\n")
    settings = Settings.load(env_file)
    assert settings.baseten_input_usd_per_m == expected
    assert settings.baseten_output_usd_per_m == expected
    monkeypatch.setenv("BASETEN_INPUT_USD_PER_M", "2")
    monkeypatch.setenv("BASETEN_OUTPUT_USD_PER_M", "4")
    settings = Settings.load(env_file)
    assert (settings.baseten_input_usd_per_m, settings.baseten_output_usd_per_m) == (2, 4)


@pytest.mark.anyio
@pytest.mark.parametrize("prices", [(2.0, 4.0), (None, None), (2.0, None), (None, 4.0)])
@pytest.mark.parametrize("known_usage", [False, True])
async def test_baseten_agent_and_review_receipts_do_not_charge_meta(
    tmp_path: Path, baseten_verified, prices, known_usage: bool
):
    settings = meta_settings(
        tmp_path,
        agent_model_b="muse-spark-open",
        baseten_api_key="test-key",
        baseten_input_usd_per_m=prices[0],
        baseten_output_usd_per_m=prices[1],
    )
    write_ledger(settings, {"spent_usd": 149, "unknown": []})
    usage = (
        {"input_tokens": 1000, "output_tokens": 100, "cached_tokens": 300} if known_usage else {}
    )
    script = json.dumps(
        {
            "task": "finish",
            "turns": [
                {
                    "tool_calls": [{"name": "finish", "arguments": {"summary": "Done."}}],
                    "usage": usage,
                }
            ],
        }
    )
    live = LiveRun(settings, lambda p, slot: ScriptedModel(parse_script(script, p), p))
    async with asyncio.timeout(DEADLINE_S):
        await live.startup()
        try:
            ledger = live.ledger.state
            brake = live.brake_reason()
            assert brake is not None  # Meta is already above its brake threshold.
            created = await live.start(StartAgent(slot="B", task="finish"))
            await until(live, "agent.status", agent_id=created.agent_id, state="ready_for_review")
            target = ChangeTarget(
                change_id=created.change_id,
                revision=1,
                base_commit="base",
                head_commit="head",
                main_commit="main",
                candidate_tree="tree",
            )
            await ReviewAccounting(live, target, MUSE_SPARK_OPEN, created.agent_id).record(
                ModelReply(
                    text=None, tool_calls=[], model=MUSE_SPARK_OPEN.model, usage=Usage(**usage)
                )
            )
            ticks = [e.payload for e in events(live) if e.type == "usage.tick"]
            assert [tick.role for tick in ticks] == ["agent", "review"]
            expected = 0.0024 if all(p is not None for p in prices) and known_usage else None
            for tick in ticks:
                assert tick.cost_usd == (pytest.approx(expected) if expected is not None else None)
                assert tick.spent_usd == 149 and tick.brake == "ok"
            assert live.ledger.state == ledger == live.ledger.read()
            assert live._usage.spent_usd == 149
            assert live.brake_reason() == brake
        finally:
            await live.shutdown()
