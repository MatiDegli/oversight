"""Suite configuration belongs to each workspace, including the disabled default."""

from unittest.mock import AsyncMock

import pytest

from oversight.workspace import configure_suite
from oversight.workspace.suite import SuiteRun
from oversight_contracts.events import SuiteResult


@pytest.mark.anyio
async def test_harness_arm_is_owned_by_each_workspace(workspace, make_workspace, monkeypatch):
    other = make_workspace("other")
    run = SuiteRun(
        SuiteResult(
            outcome="passed", passed=1, failed=0, skipped=0, duration_ms=0, failed_tests=[]
        ),
        "",
    )
    suite = AsyncMock(return_value=run)
    monkeypatch.setattr("oversight.workspace.suite_guard.run_suite", suite)
    assert workspace.harness_pack is False
    assert other.harness_pack is False
    for enabled in (False, True, False):
        configure_suite(workspace, harness_pack=enabled)
        assert workspace.harness_pack is enabled
        assert other.harness_pack is False
        await workspace.run_tests(workspace.main, timeout_s=5)
        assert suite.call_args.kwargs["harness_pack"] is enabled
