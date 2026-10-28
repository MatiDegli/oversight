"""The open-session notice reaches both the startup log and the existing event stream."""

from unittest.mock import AsyncMock

import pytest
from live_helpers import live_server, live_settings

from oversight.workspace import OPEN_NOTICE, IsolationUnavailable


@pytest.mark.anyio
async def test_startup_without_c_notifies_log_and_event(tmp_path, caplog, monkeypatch):
    monkeypatch.setattr(
        "oversight.workspace.capability.check_effect",
        AsyncMock(side_effect=IsolationUnavailable("host unavailable")),
    )
    async with live_server(live_settings(tmp_path)) as server:
        assert not server.live.workspace.isolation.available
        assert any(OPEN_NOTICE in record.message for record in caplog.records)
        reports = [event.payload for event in server.events() if event.type == "error"]
        assert any(
            r.source == "tests" and not r.fatal and OPEN_NOTICE in r.message for r in reports
        )
