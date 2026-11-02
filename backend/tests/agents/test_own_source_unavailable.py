"""Unavailable own suites retain their error receipt at the model tool boundary."""

import pytest

from oversight.agents.tools import Toolbox
from oversight.workspace import SuiteRun, SuiteUnavailable
from oversight.workspace.suite import OwnSourceTestsUnavailable
from oversight_contracts.events import SuiteResult

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("kind", ["own", "generic-suite", "file"])
async def test_unavailable_tool_text_and_error_suite(small_tree, monkeypatch, kind):
    workspace, tree = small_tree
    run = SuiteRun(
        SuiteResult(outcome="error", passed=0, failed=0, skipped=0, duration_ms=0, failed_tests=[]),
        "tests unavailable: missing original venv",
        problems=("missing original venv",),
    )
    cause = (
        OwnSourceTestsUnavailable("missing original venv")
        if kind == "own"
        else OSError(5, "denied")
    )

    async def refused(*args, **kwargs):
        if kind == "file":
            raise cause
        raise SuiteUnavailable(run, cause)

    monkeypatch.setattr(workspace, "run_tests", refused)
    outcome = await Toolbox(workspace, tree, test_timeout_s=27).call("run_tests", "{}")
    assert outcome.ok is False
    assert outcome.content == (
        "Error: tests unavailable: missing original venv"
        if kind == "own"
        else "Error: cannot access the file: denied"
    )
    assert outcome.suite_run is (None if kind == "file" else run)
