"""Grant commands use the established HTTP errors and remain unavailable in replay."""

from pathlib import Path

import pytest
from decision_helpers import observed_body
from fastapi.testclient import TestClient
from live_helpers import error_code, is_type, live_server, live_settings

from oversight.app import ReplayConfig, create_app
from oversight.changes import ModeController
from oversight.changes._mode import StartCapability
from oversight.settings import REPO_ROOT
from oversight_contracts.commands import SetMergeMode
from oversight_contracts.grants import GrantCreated, GrantRevoked, GrantScope

pytestmark = pytest.mark.anyio


async def test_unknown_grant_route(tmp_path: Path):
    async with live_server(live_settings(tmp_path)) as server:
        response = await server.client.post("/grants/grant-999/revoke")
        assert error_code(response) == (404, "not_found")


async def test_active_grant_revoke_returns_accepted_and_records_once(tmp_path: Path):
    async with live_server(live_settings(tmp_path)) as server:
        manager = server.live._manager
        assert manager is not None
        ctx = manager._ctx
        ctx.mode = ModeController(availability=lambda: StartCapability(True, "Test startup"))
        await manager.set_merge_mode(SetMergeMode(mode="merge_when_safe", revision=0))
        # Seed a confirmed grant; the change-manager tests cover personal creation eligibility.
        created = GrantCreated(
            grant_id="grant-1",
            reason="possible_callers",
            scope=GrantScope(kind="run", value=server.live.run_id, expires="run"),
            change_id="change-1",
            revision=1,
            head_commit=ctx.state.snapshot.commit,
            run_id=server.live.run_id,
            granted_by="person",
        )
        await ctx.grants.create(ctx, created)
        assert ctx.grants.active(ctx.grants.entries[created.grant_id])
        response = await server.client.post(f"/grants/{created.grant_id}/revoke")
        assert response.status_code == 202
        revoked = [e.payload for e in server.events() if e.type == "grant.revoked"]
        assert revoked == [GrantRevoked(grant_id=created.grant_id)]


async def test_unsupported_grant_maps_to_invalid_request_without_events(tmp_path: Path):
    async with live_server(live_settings(tmp_path)) as server:
        created = (await server.client.post("/agents", json={"task": "Update the value"})).json()
        await server.until(is_type("agent.status", state="ready_for_review"))
        change_id = created["change_id"]
        before = server.events()
        response = await server.client.post(
            f"/changes/{change_id}/approve",
            json={
                **observed_body(server, change_id),
                "grant": {
                    "reason": "protected_path",
                    "scope": {
                        "kind": "module",
                        "value": "shop.mod",
                        "expires": "run",
                    },
                },
            },
        )
        assert error_code(response) == (422, "invalid_request")
        assert "not yet supported" in response.json()["error"]["message"]
        assert server.events() == before


def test_replay_refuses_revoke_without_parsing_a_body(tmp_path: Path):
    app = create_app(
        live_settings(tmp_path),
        replay=ReplayConfig(REPO_ROOT / "fixtures/events/handwritten-level1.jsonl", speed=0),
    )
    with TestClient(app, base_url="http://localhost:8710") as client:
        response = client.post("/grants/grant-1/revoke", content="not json")
        assert response.status_code == 501
