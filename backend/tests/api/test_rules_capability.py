"""Startup binds the live workspace and configured reviewer, without caching authority."""

from unittest.mock import Mock

import pytest
from live_helpers import live_server, live_settings

from oversight.changes import WrongState, _mode
from oversight_contracts.commands import SetMergeMode
from oversight_contracts.policy import MergeModeState

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("activation", [False, True])
async def test_startup_binds_a_live_adapter_and_never_reconfirms(tmp_path, monkeypatch, activation):
    if activation:
        monkeypatch.setattr(_mode, "RULES_ACTIVATION", True)
    settings = live_settings(tmp_path, cross_review=True, review_model="fake-b")
    async with live_server(settings) as server:
        manager = server.live._manager
        assert manager is not None
        capability = manager._ctx.mode.capability()
        assert capability.confirmed is activation
        if not activation:
            assert capability.reason == (
                "Rules mode is closed until Revert, protected paths and follow-main land"
            )
        forbidden = Mock(side_effect=AssertionError("Read re-ran startup confirmation"))
        monkeypatch.setattr(server.live.workspace, "check_isolation", forbidden)
        monkeypatch.setattr(server.live.workspace._isolation, "check", forbidden)
        monkeypatch.setattr("oversight.workspace.capability.check_effect", forbidden)
        server.live.workspace._isolation.invalidate("Failure after application startup")
        before = server.events()
        with pytest.raises(WrongState) as refused:
            await manager.set_merge_mode(SetMergeMode(mode="merge_when_safe", revision=0))
        assert str(refused.value) == "Network isolation C is unavailable for this session"
        assert not manager._ctx.mode.capability().confirmed
        assert manager.merge_mode() == MergeModeState()
        assert server.events() == before
        forbidden.assert_not_called()
