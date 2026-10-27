"""Reserved endpoints answer before reading any request body or touching live state."""

from pathlib import Path
from typing import Any, Literal
from unittest.mock import Mock

import httpx
import pytest
from fastapi import FastAPI

from oversight.api import add_static, create_router, install_error_handlers
from oversight.api.commands import Commands
from oversight.events import EventStore, read_store
from oversight_contracts.commands import ErrorResponse

pytestmark = pytest.mark.anyio
ROOT = Path(__file__).resolve().parents[3]
ROUTES = [
    ("GET", "/merge-mode"),
    ("PUT", "/merge-mode"),
    ("POST", "/plans"),
    ("GET", "/plans/{id}"),
    ("PUT", "/plans/{id}"),
    ("POST", "/plans/{id}/start"),
    ("POST", "/plans/{id}/reject"),
]


@pytest.mark.parametrize("mode", ["live", "replay"])
@pytest.mark.parametrize("identifier", ["plan-999", "not-an-id"])
@pytest.mark.parametrize("method,path", ROUTES)
async def test_reserved_routes_never_read_bodies_or_cause_effects(
    tmp_path: Path, mode: Literal["live", "replay"], identifier: str, method: str, path: str
) -> None:
    # A real store and the production router; any command call is observable. `receive` is the
    # body source, so even trying to read malformed JSON fails this test before a 501 is returned.
    commands = Mock(spec=Commands)
    async with EventStore(
        tmp_path / "events.sqlite", "run-20261027-140312", mode="replay"
    ) as store:
        app = FastAPI()
        app.include_router(create_router(store, mode, commands if mode == "live" else None))
        install_error_handlers(app)
        before = store.graph(), read_store(store.path)
        scope = {
            "type": "http",
            "method": method,
            "path": path.replace("{id}", identifier),
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
            "http_version": "1.1",
            "scheme": "http",
            "server": ("test", 80),
            "client": ("test", 1),
            "root_path": "",
        }
        sent: list[Any] = []

        async def receive() -> Any:
            pytest.fail("Reserved endpoints must not read the body, even invalid JSON")

        async def send(message: Any) -> None:
            sent.append(message)

        await app(scope, receive, send)
        start = next(message for message in sent if message["type"] == "http.response.start")
        body = b"".join(message.get("body", b"") for message in sent)
        assert start["status"] == 501
        assert ErrorResponse.model_validate_json(body).error.code == "not_implemented"
        assert (store.graph(), read_store(store.path)) == before
        assert commands.mock_calls == []


@pytest.mark.parametrize("prefix", ["merge-mode", "plans"])
async def test_reserved_prefixes_do_not_fall_back_to_the_static_ui(
    tmp_path: Path, prefix: str
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<title>UI</title>")
    app = FastAPI()
    store = EventStore(tmp_path / "unused.sqlite", "run-20261027-140312", mode="replay")
    app.include_router(create_router(store, "replay"))
    install_error_handlers(app)
    add_static(app, dist)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/{prefix}/unknown/route")
        assert response.status_code == 404
        assert ErrorResponse.model_validate(response.json()).error.code == "not_found"
        reserved = await client.get(f"/{prefix}" if prefix == "merge-mode" else "/plans/plan-999")
        assert reserved.status_code == 501
    # Keep this assertion independent of the backend's prefix list and route registration.
    config = (ROOT / "frontend/vite.config.ts").read_text()
    assert f'"/{prefix}"' in config
