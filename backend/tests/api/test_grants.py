"""Grant commands use the established HTTP errors and remain unavailable in replay."""

from pathlib import Path

import pytest
from decision_helpers import observed_body
from fastapi.testclient import TestClient
from live_helpers import error_code, is_type, live_server, live_settings

from oversight.app import ReplayConfig, create_app
from oversight.settings import REPO_ROOT

pytestmark = pytest.mark.anyio


async def test_unknown_grant_route(tmp_path: Path):
    async with live_server(live_settings(tmp_path)) as server:
        response = await server.client.post("/grants/grant-999/revoke")
        assert error_code(response) == (404, "not_found")


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
