"""Delivery A exposes reserved routes and rejects unguarded bodies before handlers."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from live_helpers import live_settings

from oversight.api.commands import NOT_BUILT
from oversight.app import ReplayConfig, create_app
from oversight.settings import REPO_ROOT


@pytest.mark.parametrize("replay", [False, True])
def test_all_reservations_ignore_malformed_bodies_and_queries(tmp_path: Path, replay: bool) -> None:
    config = (
        ReplayConfig(log=REPO_ROOT / "fixtures/events/handwritten-level1.jsonl") if replay else None
    )
    app = create_app(live_settings(tmp_path), replay=config)
    client = TestClient(app, base_url="http://127.0.0.1:8710")
    for method, route, _ in NOT_BUILT:
        response = client.request(
            method,
            route.format(
                agent_id="agent-9",
                change_id="change-9",
                plan_id="plan-9",
                queued_task_id="queued-task-9",
            )
            + "?agent=unknown&limit=wrong&path=../escape",
            content=b"bad json",
        )
        assert response.status_code == 501, route
        assert response.json()["error"]["code"] == "not_implemented"


@pytest.mark.parametrize("decision", ["approve", "reject", "request-changes"])
@pytest.mark.parametrize(
    "body",
    [
        None,
        {},
        {"revision": 1},
        {"head_commit": "h"},
        {"revision": 1, "head_commit": None},
        {"revision": None, "head_commit": "h"},
    ],
)
def test_decision_guards_are_required_before_lookup(
    tmp_path: Path, decision: str, body: object
) -> None:
    client = TestClient(create_app(live_settings(tmp_path)), base_url="http://127.0.0.1:8710")
    response = client.post(f"/changes/absent/{decision}", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


@pytest.mark.parametrize("agent", [None, "agent-1"])
@pytest.mark.parametrize("with_hash", [False, True])
def test_save_file_null_or_missing_hash_never_reaches_the_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: str | None, with_hash: bool
) -> None:
    app = create_app(live_settings(tmp_path))

    async def forbidden(*_: object) -> None:
        raise AssertionError("SaveFile 1.3 must reject before the handler")

    monkeypatch.setattr(app.state.live, "save_file", forbidden)
    body = {"path": "new.py", "content": "x", "agent_id": agent}
    if with_hash:
        body["base_hash"] = None
    response = TestClient(app, base_url="http://127.0.0.1:8710").put("/files", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
