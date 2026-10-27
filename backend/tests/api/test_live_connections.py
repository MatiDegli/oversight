"""Live API fixtures keep separate commands independent of idle connection expiry."""

from pathlib import Path

import pytest
from fastapi import Request
from live_helpers import live_server, live_settings

pytestmark = pytest.mark.anyio


async def test_live_commands_use_fresh_connections(tmp_path: Path) -> None:
    # A run may take longer than Uvicorn's keep-alive window. Its idle socket can expire
    # as the next command is sent, so these API tests must not reuse that socket.
    async with live_server(live_settings(tmp_path)) as server:

        async def client_port(request: Request) -> int:
            assert request.client is not None
            return request.client.port

        server.app.add_api_route("/test-client-port", client_port)
        first = await server.client.get("/test-client-port")
        second = await server.client.get("/test-client-port")
        assert first.status_code == second.status_code == 200
        assert first.json() != second.json()
