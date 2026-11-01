"""Without session C, added-test probes remain unknown and cannot run an open suite."""

from unittest.mock import AsyncMock

import pytest
from changes_helpers import Harness
from test_probes_wiring import add_test

from oversight.workspace import IsolationPolicy, IsolationUnavailable
from oversight_contracts.events import SuiteExecution
from oversight_contracts.review import ChangeFacts


@pytest.mark.anyio
@pytest.mark.parametrize("session_c", [False, True])
async def test_harness_isolation_is_explicit(h: Harness, session_c: bool):
    assert h.workspace.isolation.checked is session_c
    assert h.workspace.isolation.born_with_c is session_c


@pytest.mark.anyio
async def test_probes_skip_unknown_without_c(h: Harness, monkeypatch):
    monkeypatch.setattr(
        "oversight.workspace.capability.check_effect",
        AsyncMock(
            side_effect=IsolationUnavailable(
                f"host unavailable at {h.workspace.runtime_root}/launcher"
            )
        ),
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
    assert all(
        "host unavailable at .runtime/launcher" in p and str(h.workspace.runtime_root) not in p
        for p in facts.problems
        if "_probe unavailable" in p
    )
    assert not any(e.phase.endswith("_probe") for e in h.payloads(SuiteExecution))
    assert calls == ["required", "required"]


@pytest.mark.anyio
async def test_nt_probe_unavailable_before_any_checkout(h, monkeypatch):
    from types import SimpleNamespace

    from oversight.changes import _probes

    await h.open(1)
    await add_test(h, "def test_added():\n    assert True\n")
    checkout, run, discovery = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(_probes, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(_probes, "base_probe_checkout", checkout)
    monkeypatch.setattr(_probes, "_run", run)
    monkeypatch.setattr(_probes, "added_cases", discovery)
    before = len(h.workspace.calls)
    assert await h.finish(1)
    facts = h.payloads(ChangeFacts)[-1]
    assert not facts.added_tests
    assert any("base_probe unavailable: native Windows" in p for p in facts.problems)
    assert not any(e.phase.endswith("_probe") for e in h.payloads(SuiteExecution))
    assert not any(
        c.args[0] in {"restore", "checkout"} or "--detach" in c.args
        for c in h.workspace.calls[before:]
    )
    assert not list(h.workspace.tmp_dir.glob("base-probe-*"))
    for spy in (checkout, run, discovery):
        spy.assert_not_called()
