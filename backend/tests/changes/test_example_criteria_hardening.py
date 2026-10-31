"""Criteria diagnostics, integrity and interrupted installation use no provider I/O."""

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from changes_helpers import Harness, assert_main_clean
from test_example_criteria import (
    RequiredCriteria,
    example,
    render_criteria,
    run,
    verify_run,
)
from test_rules_reservation import terminal_code
from test_rules_transaction import configure_transaction, execute
from test_rules_transaction import reserved as rules_reserved

from oversight.changes import _mode
from oversight.changes._example_criteria import refusal_code
from oversight.workspace import ProcessResult, content_hash
from oversight_contracts.events import ChangeResolved, ErrorReport, SuiteExecution

reserved = rules_reserved
INTEGRITY = "code=integrity_invalid; reason=scripted integrity problem"


@pytest.mark.anyio
async def test_diagnostic_counts_exclude_hostile_ids(h: Harness, monkeypatch) -> None:
    await h.open(1)
    ctx, rec = h.manager._ctx, h.manager._ctx.state.changes["change-1"]
    rendered = render_criteria([example()], rec.change_id)
    hostile_id = "\n\"'\\é雪☃"
    rec.criteria = RequiredCriteria(rendered.path, rendered.sha256, ("missing",), (hostile_id,))
    monkeypatch.setattr(
        ctx, "run", AsyncMock(return_value=ProcessResult(0, rendered.source, "", False, False, 0))
    )
    await verify_run(ctx, rec, run(), phase="proposal")
    [error] = h.payloads(ErrorReport)
    assert error.message == "example_criteria: case_uncollected; required_cases=1"
    assert set(hostile_id).isdisjoint(error.message)


@pytest.mark.parametrize(
    "problems,expected",
    [
        ((INTEGRITY, "example_criteria: report_unverifiable"), "integrity_invalid"),
        ((INTEGRITY,), "suite_failed"),
        (("example_criteria: report_unverifiable",), "suite_failed"),
        (("example_criteria: report_unverifiable", INTEGRITY), "suite_failed"),
    ],
)
def test_refusal_code(problems, expected: str) -> None:
    assert refusal_code(replace(run(), problems=problems)) == expected


@pytest.mark.anyio
@pytest.mark.parametrize("criteria", [False, True])
@pytest.mark.parametrize("integrity", [False, True])
async def test_rules_preserve_integrity_code(
    h: Harness, reserved, monkeypatch, criteria: bool, integrity: bool
) -> None:
    configure_transaction(h, monkeypatch)
    rec = h.manager._ctx.state.changes["change-1"]
    if criteria:
        path = "tests/test_mod.py"
        rec.criteria = RequiredCriteria(
            path, content_hash(rec.head_files[path].encode()), (path + "::test_value",), ("basic",)
        )
    original = h.workspace.run_tests

    async def observed(*args, **kwargs):
        result = await original(*args, **kwargs)
        return (
            replace(result, problems=(*result.problems, INTEGRITY))
            if integrity
            else replace(result, result=result.result.model_copy(update={"outcome": "failed"}))
        )

    monkeypatch.setattr(h.workspace, "run_tests", observed)
    pre = await h.main_head()
    await execute(h, reserved)
    assert terminal_code(h) == ("integrity_invalid" if integrity else "suite_failed")
    [combined] = [event for event in h.payloads(SuiteExecution) if event.phase == "combined"]
    assert combined.suite.outcome == (
        "error" if criteria and integrity else "passed" if integrity else "failed"
    )
    assert await h.main_head() == pre and not h.payloads(ChangeResolved)
    await assert_main_clean(h)
    assert not _mode.RULES_ACTIVATION
