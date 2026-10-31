"""Lead routes, provenance and accounting through the real live server."""

# Tests inspect run-owned services and durable receipts.
# pyright: reportPrivateUsage=false

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from live_helpers import (
    DEADLINE_S,
    FAKE_SCRIPT,
    GatedModel,
    error_code,
    is_type,
    live_server,
    live_settings,
    meta_settings,
    read_ledger,
    two_fakes,
    write_ledger,
)

from oversight.agents import MUSE_SPARK_OPEN, STARTING_DETAIL, ScriptedModel, load_script
from oversight.agents.client import CALL_SCOPE
from oversight.app import ReplayConfig, create_app
from oversight.changes import plan_core
from oversight.settings import REPO_ROOT
from oversight_contracts.events import LeadStatus

pytestmark = pytest.mark.anyio
LEAD_SCRIPT = REPO_ROOT / "fixtures/fake-model/lead-two-plans.json"


def lead_factory() -> Any:
    script, author = load_script(LEAD_SCRIPT), load_script(FAKE_SCRIPT)

    def factory(profile: Any, _slot: Any) -> Any:
        scope = CALL_SCOPE.get()
        chosen = script if scope and scope.role == "lead" else author
        # Parse with the requested profile, preserving usage and tool calls.
        return ScriptedModel(
            replace(chosen, turns=[replace(r, model=profile.model) for r in chosen.turns]), profile
        )

    return factory


async def start_done(s: Any) -> str:
    response = await s.client.post("/leads", json={"task": "Coordinate documentation"})
    assert response.status_code == 201, response.text
    agent_id = response.json()["agent_id"]
    await s.until(is_type("lead.status", agent_id=agent_id, state="done"))
    return agent_id


async def test_start_publishes_starting_with_the_limits_and_answers_201(tmp_path: Path) -> None:
    held = GatedModel()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(two_fakes(tmp_path, max_agents=2), lambda _p, _s: held) as s,
    ):
        response = await s.client.post("/leads", json={"task": "Coordinate"})
        assert response.status_code == 201 and response.json() == {"agent_id": "agent-1"}
        statuses = [e.payload for e in s.events() if e.type == "lead.status"]
        assert statuses[0] == LeadStatus(
            agent_id="agent-1",
            revision=1,
            task="Coordinate",
            model="fake",
            state="starting",
            step=0,
            detail=STARTING_DETAIL,
        )
        assert (
            STARTING_DETAIL
            == "Limits: 12 tool calls, 18 model calls, 300 s, 3 plans of up to 6 subtasks, "
            "2,000 characters per task."
        )
        assert statuses[0].requested_plan_id is None
        assert not s.live._agents and all(v is None for v in s.live._slots.values())


async def test_a_second_lead_while_one_is_active_is_409(tmp_path: Path) -> None:
    held = GatedModel()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(two_fakes(tmp_path, max_agents=2), lambda _p, _s: held) as s,
    ):
        first = await s.client.post("/leads", json={"task": "Coordinate"})
        await held.entered.wait()
        second = await s.client.post("/leads", json={"task": "Another lead"})
        assert first.status_code == 201 and error_code(second) == (409, "wrong_state")
        assert second.json()["error"]["message"] == "a lead is already active"
        assert s.live._count == 1


async def test_get_lead_reads_the_latest_status_and_agents_route_stays_404(tmp_path: Path) -> None:
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(two_fakes(tmp_path, max_agents=2), lead_factory()) as s,
    ):
        agent_id = await start_done(s)
        response = await s.client.get(f"/leads/{agent_id}")
        latest = [e.payload for e in s.events() if e.type == "lead.status"][-1]
        assert LeadStatus.model_validate(response.json()) == latest
        assert latest.state == "done" and latest.detail == "Requested plan-1, plan-2."
        assert latest.revision == 9 and latest.step == 7 and latest.requested_plan_id is None
        assert error_code(await s.client.get(f"/agents/{agent_id}")) == (404, "not_found")
        response = await s.client.get("/leads/agent-999")
        assert error_code(response) == (404, "not_found")
        assert response.json()["error"]["message"] == "there is no lead 'agent-999'"
        assert await start_done(s) == "agent-2"


@pytest.mark.parametrize(
    "body", [{"task": " "}, {"task": "Work", "model": "other"}, {"task": "Work", "model": "fake-b"}]
)
async def test_blank_task_and_unoffered_models_are_422(tmp_path: Path, body: Any) -> None:
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(two_fakes(tmp_path, max_agents=2), lead_factory()) as s,
    ):
        assert error_code(await s.client.post("/leads", json=body)) == (422, "invalid_request")
        assert not s.live.leads.leads and s.live._count == 0


@pytest.mark.parametrize("reason", ["setup", "brake"])
async def test_setup_and_brake_refuse_the_start(tmp_path: Path, reason: str) -> None:
    settings = meta_settings(tmp_path, **({"meta_api_key": None} if reason == "setup" else {}))
    if reason == "brake":
        write_ledger(settings, {"version": 1, "spent_usd": 140, "unknown": []})
    async with asyncio.timeout(DEADLINE_S), live_server(settings, lead_factory()) as s:
        before = s.store.last_seq
        response = await s.client.post("/leads", json={"task": "Coordinate"})
        assert error_code(response) == (
            (503, "not_configured") if reason == "setup" else (403, "spend_brake")
        )
        if reason == "setup":
            assert response.json()["error"]["missing"][0]["name"] == "META_API_KEY"
        assert not s.live.leads.leads and before == s.store.last_seq


async def test_selected_baseten_profile_needs_its_key_and_ignores_reviewer(tmp_path: Path) -> None:
    settings = meta_settings(
        tmp_path,
        meta_api_key=None,
        cross_review=True,
        review_model="muse-spark",
        baseten_api_key="test-key",
    )
    async with asyncio.timeout(DEADLINE_S), live_server(settings, lead_factory()) as s:
        response = await s.client.post(
            "/leads", json={"task": "Coordinate", "model": "muse-spark-open"}
        )
        assert response.status_code == 201, response.text
        await s.until(is_type("lead.status", state="done"))
        statuses = [e.payload for e in s.events() if e.type == "lead.status"]
        assert all(status.model == MUSE_SPARK_OPEN.id for status in statuses)
        assert s.live.ledger.state is not None
        assert s.live.ledger.state.spent_usd == 0
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(replace(settings, baseten_api_key=None), lead_factory()) as s,
    ):
        response = await s.client.post(
            "/leads", json={"task": "Coordinate", "model": "muse-spark-open"}
        )
        assert error_code(response) == (503, "not_configured")
        assert [m["name"] for m in response.json()["error"]["missing"]] == ["BASETEN_API_KEY"]


async def test_lead_plans_carry_started_by_and_no_plan_call_runs(tmp_path: Path) -> None:
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(two_fakes(tmp_path, max_agents=2), lead_factory()) as s,
    ):
        _, snapshot = s.store.graph()
        assert snapshot is not None
        agent_id = await start_done(s)
        events = s.events()
        plans = [
            e.payload for e in events if e.type == "plan.updated" and e.payload.state == "proposed"
        ]
        assert len(plans) == 2 and [len(p.subtasks) for p in plans] == [2, 1]
        assert all(p.started_by == agent_id for p in plans)
        assert all(t.zone == plan_core.whole_graph(snapshot) for p in plans for t in p.subtasks)
        for index, event in enumerate(events):
            if event.type == "plan.updated" and event.payload.state == "proposed":
                following = events[index + 1]
                assert following.type == "lead.status"
                assert following.payload.requested_plan_id == event.payload.plan_id
                assert following.payload.detail == f"Requested {event.payload.plan_id}."
            elif event.type == "lead.status" and event.payload.requested_plan_id is None:
                assert event.payload.detail is None or event.payload.state != "working"
        assert not any(
            e.type in {"queue.updated", "agent.status", "change.resolved"} for e in events
        )
        assert all(e.payload.role == "lead" for e in events if e.type == "usage.tick")
        revisions = [e.payload.revision for e in events if e.type == "lead.status"]
        assert revisions == list(range(1, len(revisions) + 1))


async def test_a_person_queues_a_lead_plan_and_its_agents_have_no_started_by(
    tmp_path: Path,
) -> None:
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(two_fakes(tmp_path, max_agents=2), lead_factory()) as s,
    ):
        lead_id = await start_done(s)
        plan = s.live.plans.get("plan-1")
        response = await s.client.post(
            "/queue", json={"plan_id": plan.plan_id, "plan_revision": plan.revision}
        )
        assert response.status_code == 201, response.text
        item_id = response.json()["queued_task_id"]
        events = await s.until(is_type("plan.updated", plan_id=plan.plan_id, state="running"))
        running = events[-1].payload
        assert running.started_by == lead_id
        for subtask in running.subtasks:
            assert subtask.agent_id is not None and subtask.agent_id != lead_id
            statuses = [
                e.payload
                for e in s.events()
                if e.type == "agent.status" and e.payload.agent_id == subtask.agent_id
            ]
            assert statuses and statuses[-1].plan_id == plan.plan_id
            assert statuses[-1].queued_task_id == item_id and statuses[-1].started_by is None


@pytest.mark.parametrize("unknown", [False, True])
async def test_metered_lead_ticks_reach_the_ledger_and_spend_receipt(
    tmp_path: Path, unknown: bool
) -> None:
    factory = lead_factory()

    def make(profile: Any, slot: Any) -> Any:
        model = factory(profile, slot)
        if unknown:
            model._turns[0] = replace(model._turns[0], usage=type(model._turns[0].usage)())
        return model

    settings = meta_settings(tmp_path)
    async with asyncio.timeout(DEADLINE_S), live_server(settings, make) as s:
        manager = s.live._manager
        assert manager is not None
        original = manager.spend_received
        manager.spend_received = Mock(wraps=original)
        await s.client.post("/leads", json={"task": "Coordinate"})
        await s.until(lambda e: e.type == "lead.status" and e.payload.state in {"done", "halted"})
        ticks = [e.payload for e in s.events() if e.type == "usage.tick"]
        assert ticks and all(
            t.role == "lead" and t.plan_id is None and t.change_id is None for t in ticks
        )
        assert manager.spend_received.call_count == len(ticks)
        ledger = read_ledger(settings)
        if unknown:
            assert ticks[0].input_tokens is None and ticks[0].cost_usd is None and ledger["unknown"]
            assert [e.payload for e in s.events() if e.type == "lead.status"][
                -1
            ].detail == "The spending brake stopped the next model call."
        else:
            assert ledger["spent_usd"] > 0 and ticks[-1].spent_usd == ledger["spent_usd"]


async def test_replay_refuses_lead_posts_and_reads_lead_status(tmp_path: Path) -> None:
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(two_fakes(tmp_path / "live", max_agents=2), lead_factory()) as s,
    ):
        agent_id = await start_done(s)
        log = tmp_path / "lead.jsonl"
        log.write_text("\n".join(e.model_dump_json() for e in s.events()) + "\n")
        expected = (await s.client.get(f"/leads/{agent_id}")).json()
    app = create_app(
        live_settings(tmp_path / "replay"), ReplayConfig(log=log, speed=0, allow_fake_provider=True)
    )
    with TestClient(app, base_url="http://127.0.0.1:8710") as client:
        for path in ("/leads", f"/leads/{agent_id}/stop"):
            assert error_code(client.post(path, content=b"bad json")) == (501, "not_implemented")

        # The replay player can still be committing: read the store's last status, then the route.
        async def replay_finished() -> None:
            async with asyncio.timeout(DEADLINE_S):
                async for event in app.state.store.subscribe(0):
                    if event.type == "lead.status":
                        from pydantic import TypeAdapter

                        from oversight_contracts.events import ServerEvent

                        parsed = TypeAdapter(ServerEvent).validate_json(event.envelope)
                        if parsed.type == "lead.status" and parsed.payload.state == "done":
                            return

        assert client.portal is not None
        client.portal.call(replay_finished)
        assert client.get(f"/leads/{agent_id}").json() == expected
        assert client.get(f"/agents/{agent_id}").status_code == 404


def test_proposal_documents_live_lead_routes() -> None:
    rows = (REPO_ROOT / "docs/proposal.md").read_text().splitlines()
    assert [row for row in rows if row.startswith("| `POST /leads` |")] == [
        "| `POST /leads` | `StartLead {task, model?}` | 201 `LeadCreated` | "
        "`invalid_request`, `not_configured`, `spend_brake`, `wrong_state` |"
    ]
    assert "| `GET /leads/{id}` | none | 200 `LeadStatus` | `not_found` |" in rows
    assert (
        "| `POST /leads/{id}/stop` | `StopLead {revision}` | 202 | `not_found`, `wrong_state` |"
        in rows
    )


async def test_a_lead_zone_is_main_at_request_time_even_if_the_graph_moves(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = load_script(LEAD_SCRIPT)
    script = replace(
        script,
        turns=[
            replace(script.turns[1], tool_calls=[script.turns[1].tool_calls[1]]),
            script.turns[2],
        ],
    )
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(two_fakes(tmp_path), lambda p, _s: ScriptedModel(script, p)) as s,
    ):
        _, snapshot = s.store.graph()
        assert snapshot is not None
        newer = snapshot.model_copy(
            update={
                "version": "new-main",
                "nodes": [],
                "edges": [],
                "positions": {},
            }
        )
        original = s.live.plans.create_and_propose

        async def move(task: Any, drafts: Any, *, started_by: Any) -> Any:
            assert drafts[0].zone == plan_core.whole_graph(snapshot)
            # Main moves after the tool captures its zone, before the plan service captures
            # graph_version under queue_lock. The lead's zone intentionally remains unchecked.
            await s.store.publish(newer)
            return await original(task, drafts, started_by=started_by)

        monkeypatch.setattr(s.live.plans, "create_and_propose", move)
        await start_done(s)
        plan = s.live.plans.get("plan-1")
        assert plan.graph_version == newer.version
        assert plan.subtasks and plan.subtasks[0].zone == plan_core.whole_graph(snapshot)
        assert plan.subtasks[0].zone != plan_core.whole_graph(newer)
