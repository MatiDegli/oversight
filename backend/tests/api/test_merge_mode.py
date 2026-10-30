"""HTTP mode authority, validation before lookup, confirmed events and fresh starts."""

import asyncio
import re
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from fastapi import FastAPI
from live_helpers import SERVER_EVENT, error_code, live_server, live_settings

from oversight.api import create_router, install_error_handlers
from oversight.api.commands import LIVE_ONLY, NOT_BUILT, Commands
from oversight.changes import ModeController
from oversight.changes._mode import StartCapability
from oversight.events import EventStore
from oversight_contracts.policy import MergeModeState

pytestmark = pytest.mark.anyio


async def test_live_mode_snapshot_closed_latch_stale_and_noop(tmp_path: Path):
    settings = live_settings(tmp_path, cross_review=True)
    async with live_server(settings) as server:
        assert server.live._manager is not None
        controller = server.live._manager._ctx.mode
        started = server.events()[0].payload
        assert started.merge_mode == MergeModeState()
        before = server.events()
        response = await server.client.get("/merge-mode")
        assert response.status_code == 200
        assert response.json() == {"mode": "ask_me", "revision": 0}
        for revision, mode, status in (
            (0, "merge_when_safe", 409),
            (1, "ask_me", 409),
            (0, "ask_me", 200),
        ):
            response = await server.client.put(
                "/merge-mode", json={"mode": mode, "revision": revision}
            )
            assert response.status_code == status
            if status == 409:
                assert error_code(response) == (409, "wrong_state")
            if mode == "merge_when_safe":
                assert "Rules mode is closed" in response.json()["error"]["message"]
            assert not controller._disables and not controller.authority_suspended()
        assert server.events() == before
    async with live_server(settings) as restarted:
        response = await restarted.client.get("/merge-mode")
        assert response.json() == {"mode": "ask_me", "revision": 0}
        assert restarted.live.workspace.isolation.checked


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        "{}",
        '{"mode":"unknown","revision":0}',
        '{"mode":"ask_me","revision":-1}',
        '{"mode":"ask_me","revision":0,"extra":true}',
    ],
)
async def test_invalid_body_is_rejected_before_command_lookup(tmp_path: Path, body: str):
    commands = Mock(spec=Commands)
    app = FastAPI()
    store = EventStore(tmp_path / "unused.sqlite", "run-20261027-140312")
    app.include_router(create_router(store, "live", commands))
    install_error_handlers(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        response = await c.put(
            "/merge-mode", content=body, headers={"content-type": "application/json"}
        )
    assert error_code(response) == (422, "invalid_request")
    assert commands.mock_calls == []


async def test_put_waits_for_receipt_and_returns_confirmed_state(tmp_path: Path, monkeypatch):
    async with live_server(live_settings(tmp_path)) as server:
        manager = server.live._manager
        assert manager is not None
        ctx = manager._ctx
        ctx.mode = ModeController(availability=lambda: StartCapability(True, "Test only"))
        original = ctx.publish
        entered, release = asyncio.Event(), asyncio.Event()

        async def publish(payload):
            if isinstance(payload, MergeModeState):
                entered.set()
                await release.wait()
            return await original(payload)

        monkeypatch.setattr(ctx, "publish", publish)
        request = asyncio.create_task(
            server.client.put("/merge-mode", json={"mode": "merge_when_safe", "revision": 0})
        )
        try:
            await entered.wait()
            assert not request.done()
            assert (await server.client.get("/merge-mode")).json()["revision"] == 0
        finally:
            release.set()
        response = await request
        assert response.status_code == 200
        assert response.json() == {"mode": "merge_when_safe", "revision": 1}
        response = await server.client.put("/merge-mode", json={"mode": "ask_me", "revision": 1})
        assert response.status_code == 200
        assert response.json() == {"mode": "ask_me", "revision": 2}
        assert len([e for e in server.events() if e.type == "merge_mode.changed"]) == 2
        for event in server.events():
            SERVER_EVENT.validate_python(event.model_dump(mode="json"))


def test_mode_routes_are_live_only_and_only_http_wiring_calls_the_setter():
    entries = [(method, path) for method, path, _ in LIVE_ONLY]
    offset = entries.index(("POST", "/changes/{change_id}/request-changes"))
    assert entries[offset + 1 : offset + 3] == [("GET", "/merge-mode"), ("PUT", "/merge-mode")]
    assert all(path != "/merge-mode" for _, path, _ in NOT_BUILT)
    root = Path(__file__).resolve().parents[2] / "src"
    callers = [
        (p.relative_to(root).as_posix(), line.strip())
        for p in root.rglob("*.py")
        for line in p.read_text().splitlines()
        if re.search(r"\.set_merge_mode\(", line)
    ]
    assert sorted(callers) == sorted(
        [
            ("oversight/app.py", "return await self._manager.set_merge_mode(request)"),
            ("oversight/api/commands.py", "return await commands.set_merge_mode(request)"),
        ]
    )


async def test_disable_receipt_failure_is_409_and_retry_uses_the_same_revision(
    tmp_path, monkeypatch
):
    async with live_server(live_settings(tmp_path)) as server:
        manager = server.live._manager
        assert manager is not None
        ctx = manager._ctx
        ctx.mode = ModeController(availability=lambda: StartCapability(True, "Test only"))
        assert (
            await server.client.put("/merge-mode", json={"mode": "merge_when_safe", "revision": 0})
        ).status_code == 200
        original = ctx.publish

        async def failed(payload):
            raise RuntimeError("Scripted receipt failure")

        monkeypatch.setattr(ctx, "publish", failed)
        before = server.events()
        response = await server.client.put("/merge-mode", json={"mode": "ask_me", "revision": 1})
        assert error_code(response) == (409, "wrong_state")
        assert "not confirmed" in response.json()["error"]["message"]
        assert not ctx.mode._disables and ctx.mode.authority_suspended()
        assert server.events() == before
        monkeypatch.setattr(ctx, "publish", original)
        response = await server.client.put("/merge-mode", json={"mode": "ask_me", "revision": 1})
        assert response.status_code == 200
        assert response.json() == {"mode": "ask_me", "revision": 2}
        assert not ctx.mode._disables and not ctx.mode.authority_suspended()


async def test_disable_http_timeout_still_switches_after_lock_release(tmp_path):
    async with live_server(live_settings(tmp_path)) as server:
        manager = server.live._manager
        assert manager is not None
        ctx = manager._ctx
        ctx.mode = ModeController(
            availability=lambda: StartCapability(True, "Test only"), timeout_s=0.02
        )
        assert (
            await server.client.put("/merge-mode", json={"mode": "merge_when_safe", "revision": 0})
        ).status_code == 200
        async with ctx.mode._lock:
            response = await server.client.put(
                "/merge-mode", json={"mode": "ask_me", "revision": 1}
            )
            assert error_code(response) == (409, "wrong_state")
            assert "timed out" in response.json()["error"]["message"]
            assert len(ctx.mode._disables) == 1 and ctx.mode.authority_suspended()
        await asyncio.gather(*ctx.state.tasks)
        response = await server.client.get("/merge-mode")
        assert response.status_code == 200
        assert response.json() == {"mode": "ask_me", "revision": 2}
        assert not ctx.mode._disables and not ctx.mode.authority_suspended()
