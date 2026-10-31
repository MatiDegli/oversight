"""POST /agents, approve and reject over a real server on the live app, with the scripted model. The
routes of Request changes and Stop are in test_revise_stop.py."""

import asyncio
from pathlib import Path

import pytest
from decision_helpers import observed_body
from fastapi.testclient import TestClient
from live_helpers import (
    DEADLINE_S,
    GatedModel,
    Scripts,
    error_code,
    is_type,
    live_server,
    live_settings,
    meta_settings,
)

from oversight.api.commands import NOT_BUILT
from oversight.app import ReplayConfig, create_app
from oversight.settings import REPO_ROOT
from oversight_contracts.commands import AgentCreated

pytestmark = pytest.mark.anyio


async def test_unknown_and_malformed_change_ids_are_not_found(tmp_path: Path) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        assert error_code(
            await s.client.post(
                "/changes/change-9/approve",
                json={
                    **observed_body(s, "change-9"),
                    "revision": max(1, observed_body(s, "change-9")["revision"]),
                },
            )
        ) == (404, "not_found")
        assert error_code(
            await s.client.post("/changes/not-an-id/reject", json=observed_body(s, "not-an-id"))
        ) == (404, "not_found")


async def test_approve_before_the_proposal_is_wrong_state_and_a_racing_start_slot_busy(
    tmp_path: Path,
) -> None:
    model = GatedModel()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: model) as s,
    ):
        first, second = await asyncio.gather(
            s.client.post("/agents", json={"task": "Do it."}),
            s.client.post("/agents", json={"task": "Do it too."}),
        )
        codes = sorted([first.status_code, second.status_code])
        assert codes == [201, 409]
        busy = first if first.status_code == 409 else second
        assert error_code(busy) == (409, "slot_busy")
        created = AgentCreated.model_validate((second if busy is first else first).json())
        assert created.slot == "A"
        await model.entered.wait()  # the run is working: nothing is proposed yet
        response = await s.client.post(
            f"/changes/{created.change_id}/approve",
            json={
                **observed_body(s, created.change_id),
                "revision": max(1, observed_body(s, created.change_id)["revision"]),
            },
        )
        assert error_code(response) == (409, "wrong_state")
        assert error_code(await s.client.post("/agents", json={"task": "x"})) == (409, "slot_busy")


async def test_a_second_decision_is_wrong_state_and_one_resolution_is_logged(
    tmp_path: Path,
) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        created = AgentCreated.model_validate(
            (await s.client.post("/agents", json={"task": "t"})).json()
        )
        await s.until(is_type("agent.status", state="ready_for_review"))
        approve = await s.client.post(
            f"/changes/{created.change_id}/approve",
            json={
                **{
                    **observed_body(s, created.change_id),
                    "revision": max(1, observed_body(s, created.change_id)["revision"]),
                },
                "comment": "ok",
            },
        )
        assert approve.status_code == 202
        assert approve.content == b""
        again = await s.client.post(
            f"/changes/{created.change_id}/reject", json=observed_body(s, created.change_id)
        )
        assert error_code(again) == (409, "wrong_state")
        await s.until(is_type("agent.status", state="done"))
        resolved = [e for e in s.events() if e.type == "change.resolved"]
        assert [(e.payload.decision, e.payload.comment) for e in resolved] == [("approved", "ok")]


@pytest.mark.parametrize(
    "body",
    [
        {"task": "t", "slot": "B"},
        {"task": ""},
        {"task": "   "},
        {"task": "t", "model": "muse-spark"},
        {"slot": "A"},
    ],
)
async def test_bad_starts_are_invalid_request(tmp_path: Path, body: dict[str, str]) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        assert error_code(await s.client.post("/agents", json=body)) == (422, "invalid_request")


@pytest.mark.parametrize("configured", ["fake", "muse-spark"])
async def test_a_start_on_muse_spark_open_is_a_model_this_server_does_not_run(
    tmp_path: Path, configured: str
) -> None:
    # Muse Spark Open is verified (docs/api-probe.md), so it is no longer refused as unverified; but
    # only the configured profiles are offered, and none of them is Open: slot B would run it only
    # if OVERSIGHT_AGENT_MODEL_B named it.
    settings = live_settings(tmp_path) if configured == "fake" else meta_settings(tmp_path)
    scripts = Scripts()
    async with asyncio.timeout(DEADLINE_S), live_server(settings, scripts) as s:
        response = await s.client.post("/agents", json={"task": "t", "model": "muse-spark-open"})
        assert error_code(response) == (422, "invalid_request")
        assert response.json()["error"]["message"] == f"this server runs agents on {configured}"
    assert scripts.calls == 0  # no client of any provider was made


async def test_a_start_on_an_unverified_profile_is_refused_and_takes_no_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No shipped profile is unverified now: the test marks the configured fake one.
    monkeypatch.setattr("oversight.app.UNVERIFIED_PROFILES", frozenset({"fake"}))
    scripts = Scripts()
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path), scripts) as s:
        response = await s.client.post("/agents", json={"task": "t"})
        assert error_code(response) == (422, "invalid_request")
        assert response.json()["error"]["message"] == "fake cannot run agents yet: unverified"
        assert scripts.calls == 0
        monkeypatch.undo()
        assert (await s.client.post("/agents", json={"task": "t"})).status_code == 201


async def test_a_meta_start_without_a_key_lists_setup_problems_before_the_brake(
    tmp_path: Path,
) -> None:
    settings = meta_settings(tmp_path, meta_api_key=None, budget_usd=None)
    calls: list[object] = []
    async with asyncio.timeout(DEADLINE_S), live_server(settings, calls.append) as s:  # pyright: ignore[reportArgumentType]
        response = await s.client.post("/agents", json={"task": "t"})
        assert error_code(response) == (503, "not_configured")
        assert response.json()["error"]["missing"] == [
            {"name": "META_API_KEY", "problem": "missing", "slots": ["A"]},
            {"name": "OVERSIGHT_BUDGET_USD", "problem": "missing", "slots": ["A"]},
        ]
    assert calls == []


async def test_unbuilt_commands_answer_501_without_reading_the_body(tmp_path: Path) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        for method, path, _ in NOT_BUILT:
            url = path.replace("{agent_id}", "agent-1").replace("{change_id}", "change-1")
            response = await s.client.request(method, url, content=b"not json")
            assert error_code(response) == (501, "not_implemented"), url


def test_a_replay_takes_no_commands(tmp_path: Path) -> None:
    log = REPO_ROOT / "fixtures" / "events" / "handwritten-level1.jsonl"
    app = create_app(live_settings(tmp_path), ReplayConfig(log=log))
    client = TestClient(app, base_url="http://127.0.0.1:8710")
    for url in (
        "/agents",
        "/agents/agent-1/stop",
        "/agents/agent-1/message",
        "/agents/agent-1/pause",
        "/agents/agent-1/resume",
        "/changes/change-1/approve",
        "/changes/change-1/reject",
        "/changes/change-1/request-changes",
        "/changes/change-1/revert",
    ):
        assert error_code(client.post(url, content=b"not json")) == (501, "not_implemented")
