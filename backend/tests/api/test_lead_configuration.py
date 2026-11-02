"""Live construction retains the run snapshot in prompts, tools, events and enforcement."""

# Tests inspect composed limits; no provider client is used.
# pyright: reportPrivateUsage=false

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from backend.tests.agents.lead_helpers import call, plan, turn
from backend.tests.api.lead_helpers import ThreadModels, idle, replies, send, start_idle
from live_helpers import DEADLINE_S, error_code, is_type, live_server, two_fakes

from oversight.agents import build_lead_prompt, build_lead_tool_schemas, build_starting_detail
from oversight.agents import prompt_version as version

pytestmark = pytest.mark.anyio


async def test_snapshot_survives_environment_and_settings_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    settings = two_fakes(
        tmp_path,
        max_leads=7,
        lead_max_turns=70,
        lead_max_tool_calls=25,
        lead_max_model_calls=30,
        lead_max_wall_clock_s=321,
        lead_max_plans=4,
        lead_max_subtasks=8,
        lead_max_task_chars=3000,
        lead_history_chars=48000,
    )
    factory = ThreadModels()
    async with asyncio.timeout(DEADLINE_S), live_server(settings, factory) as s:
        first = await start_idle(s)
        snapshot = s.live._lead_limits
        for field in type(snapshot).model_fields:
            monkeypatch.setenv("OVERSIGHT_LEAD_MAX_" + field.upper(), "1")
        monkeypatch.setenv("OVERSIGHT_MAX_LEADS", "1")
        monkeypatch.setenv("OVERSIGHT_LEAD_HISTORY_CHARS", "1")
        s.live._settings = replace(
            s.live._settings,
            max_leads=1,
            lead_max_turns=1,
            lead_max_tool_calls=1,
            lead_max_model_calls=1,
            lead_max_wall_clock_s=1,
            lead_max_plans=1,
            lead_max_subtasks=1,
            lead_max_task_chars=1,
            lead_history_chars=1,
        )
        second = await start_idle(s)
        for agent_id in (first, second):
            lead = s.live.leads.leads[agent_id]
            assert lead.limits is snapshot and lead.run is not None
            assert lead.status.limits is snapshot
            assert lead.run.limits is snapshot and lead.run.tools.limits is snapshot
            assert lead.run.prompt == build_lead_prompt(snapshot)
            assert lead.run.schemas == build_lead_tool_schemas(snapshot)
            assert lead.run.history.history_chars == 48000
            statuses = [
                e.payload
                for e in s.events()
                if e.type == "lead.status" and e.payload.agent_id == agent_id
            ]
            assert statuses[0].revision == 1
            assert statuses[0].detail == build_starting_detail(snapshot)
            assert all(status.limits == snapshot for status in statuses)
        started = next(e.payload for e in s.events() if e.type == "run.started")
        assert started.max_leads == 7
        assert started.prompt_version == version(
            settings.harness_pack, settings.history_prune_tokens, lead_limits=snapshot
        )
        assert started.limits == s.live._limits
        assert started.limits.test_timeout_s == 60 and started.limits.max_wall_clock_s == 600
        expected = [
            "Lead capacity: 7 active leads. These settings stay fixed for this run.",
            "Lead limits: 70 turns per thread; 25 tool calls, 30 model calls and 321 s per turn; "
            "4 plans per thread; 8 subtasks per plan; 3000 characters per task.",
        ]
        assert [r.message for r in caplog.records if r.message.startswith("Lead ")] == expected
        assert any(
            r.message == "Test limit: 60 s. Agent time limit: 600 s per round. "
            "These settings stay fixed for this run."
            for r in caplog.records
        )


@pytest.mark.parametrize("turns", [1, 2, 52])
async def test_configured_last_turn_replies_then_halts_without_idle(
    tmp_path: Path, turns: int
) -> None:
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(two_fakes(tmp_path, lead_max_turns=turns), ThreadModels()) as s,
    ):
        after = s.store.last_seq
        assert (await s.client.post("/leads", json={"task": "thread"})).status_code == 201
        for index in range(1, turns):
            await idle(s, after)
            after = s.store.last_seq
            assert (
                await s.client.post("/leads/agent-1/message", json={"text": f"turn {index + 1}"})
            ).status_code == 202
        await s.until(is_type("lead.status", state="halted"), after)
        events = [e for e in s.events() if e.seq > after]
        assert len(replies(s)) == turns
        assert events[-2].type == "lead.reply"
        assert events[-1].payload.detail == f"Reached the limit of {turns} turns."
        assert not any(e.type == "lead.status" and e.payload.state == "idle" for e in events)
        assert error_code(
            await s.client.post("/leads/agent-1/message", json={"text": "too late"})
        ) == (409, "wrong_state")
        assert all(e.payload.limits.turns == turns for e in s.events() if e.type == "lead.status")


async def test_raised_tool_model_and_plan_budgets_reach_live_execution(tmp_path: Path) -> None:
    script = [turn(call("status")) for _ in range(19)]
    script += [turn(call("request_plan", **plan())) for _ in range(4)]
    factory = ThreadModels(
        [
            [*script, {"text": "All four proposed"}],
            [turn(call("request_plan", **plan())), {"text": "Budget used"}],
        ]
    )
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(
            two_fakes(tmp_path, lead_max_tool_calls=25, lead_max_model_calls=26, lead_max_plans=4),
            factory,
        ) as s,
    ):
        await start_idle(s)
        assert len(factory.built[0].requests) == 24
        assert len(s.live.plans.plans) == 4
        assert replies(s)[0].payload.cut is None
        assert s.live.leads.leads["agent-1"].status.step == 23
        await send(s)
        assert len(s.live.plans.plans) == 4
        assert "already requested 4 plans" in str(factory.built[-1].requests)


@pytest.mark.parametrize(
    "limit,cut",
    [("tool_calls", "tool_calls"), ("model_calls", "model_calls"), ("wall_clock_s", "time")],
)
async def test_live_turn_cuts_enforce_lower_limits(tmp_path: Path, limit: str, cut: str) -> None:
    from backend.tests.api.test_lead_concurrency import HeldScript

    scripts = ThreadModels([[turn(call("status")), {"text": "next call"}]])

    def factory(profile, slot):
        model = HeldScript(scripts(profile, slot))
        if cut != "time":
            model.release.set()
        return model

    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(two_fakes(tmp_path, **{"lead_max_" + limit: 1}), factory) as s,
    ):
        await start_idle(s)
        assert replies(s)[0].payload.cut == cut
        assert len(scripts.built) == 1
