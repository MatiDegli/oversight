"""Without session C, added-test probes remain unknown and cannot run an open suite."""

from unittest.mock import AsyncMock

import pytest
from changes_helpers import Harness
from test_probes_wiring import add_test

from oversight.workspace import IsolationPolicy, IsolationUnavailable
from oversight_contracts.events import SuiteExecution
from oversight_contracts.review import ChangeFacts


@pytest.mark.anyio
async def test_probes_skip_unknown_without_c(h: Harness, monkeypatch):
    monkeypatch.setattr(
        "oversight.workspace.capability.check_effect",
        AsyncMock(side_effect=IsolationUnavailable("host unavailable")),
    )
    await h.workspace.check_isolation()
    assert not h.workspace.isolation.born_with_c
    await h.open(1)
    await add_test(h, "def test_added():\n    assert True\n")
    original = h.workspace.run_tests
    calls = []

    async def capture(tree, *, timeout_s, case_ids=(), isolation: IsolationPolicy = "best_effort"):
        if case_ids:
            calls.append(isolation)
        return await original(tree, timeout_s=timeout_s, case_ids=case_ids, isolation=isolation)

    monkeypatch.setattr(h.workspace, "run_tests", capture)
    assert await h.finish(1)
    facts = h.payloads(ChangeFacts)[-1]
    assert not facts.added_tests
    assert any("base_probe unavailable" in p for p in facts.problems)
    assert any("head_probe unavailable" in p for p in facts.problems)
    assert not any(e.phase.endswith("_probe") for e in h.payloads(SuiteExecution))
    assert calls == ["required", "required"]
