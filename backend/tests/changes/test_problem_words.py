"""The three problem sites use words while retaining timeout/isolation distinctions."""

import ast
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from changes_helpers import Harness
from test_measured_cone import prepared
from test_probes_wiring import add_test

from oversight.changes import _measured, _probes
from oversight.workspace import IsolationUnavailable, WorkspaceError, measured_report
from oversight_contracts.events import ChangeProposed

PRIVATE = "private exception detail"
ERRORS = (
    OSError(PRIVATE),
    PermissionError(PRIVATE),
    TimeoutError(PRIVATE),
    WorkspaceError(PRIVATE),
    ValueError(PRIVATE),
    json.JSONDecodeError(PRIVATE, "", 0),
    TypeError(PRIVATE),
    KeyError(PRIVATE),
    RecursionError(PRIVATE),
)


def assert_words(text: str, expected: str, error: Exception) -> None:
    assert text == expected
    assert all(kind.__name__ not in text for kind in type(error).__mro__)
    assert PRIVATE not in text


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error",
    (*ERRORS, Exception(PRIVATE), IsolationUnavailable(PRIVATE)),
    ids=lambda e: type(e).__name__,
)
async def test_measurement_problem_words(
    h: Harness, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    # Reuse the proposal/selection harness from test_measured_cone.prepared.
    ctx, rec, target, impact = await prepared(h)
    overlay = AsyncMock(side_effect=error)
    monkeypatch.setattr(_measured, "_overlay", overlay)
    async with h.workspace.locks(main=True, agent_id=rec.agent_id):
        measured = await _measured.measure_cone(ctx, rec, target=target, impact=impact)
    overlay.assert_awaited_once()
    expected = (
        "caller measurement budget exhausted"
        if isinstance(error, TimeoutError)
        else "isolation unavailable"
        if isinstance(error, IsolationUnavailable)
        else "caller measurement unavailable"
    )
    assert len(measured.problems) == 1
    assert_words(measured.problems[0], expected, error)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error", (*ERRORS[:4], IsolationUnavailable("host unavailable")), ids=lambda e: type(e).__name__
)
async def test_added_probe_problem_words(
    h: Harness, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    await h.open(1)
    # Reuse the real added-test setup from test_probes_wiring.add_test.
    await add_test(h, "def test_added():\n    assert True\n")
    assert await h.finish(1)
    rec = h.manager._agents["agent-1"]
    proposal = h.payloads(ChangeProposed)[-1]
    if isinstance(error, IsolationUnavailable):
        error = IsolationUnavailable(f"host unavailable at {h.workspace.runtime_root}/launcher")
    runner = AsyncMock(side_effect=error)
    monkeypatch.setattr(h.workspace, "run_tests", runner)
    async with h.workspace.locks(main=True, agent_id=rec.agent_id):
        probes, problems = await _probes.probe_added_tests(
            h.manager._ctx, rec, head=proposal.head_commit, revision=proposal.revision
        )
    assert not probes
    assert runner.await_count == 2
    assert len(problems) == 2
    outcome = "timed out" if isinstance(error, TimeoutError) else "unavailable"
    suffix = (
        ": host unavailable at .runtime/launcher" if isinstance(error, IsolationUnavailable) else ""
    )
    for phase, text in zip(("base_probe", "head_probe"), problems, strict=True):
        assert_words(text, f"added tests: {phase} {outcome}{suffix}", error)
        assert str(h.workspace.runtime_root) not in text


@pytest.mark.parametrize("error", (*ERRORS[:3], *ERRORS[4:]), ids=lambda e: type(e).__name__)
def test_caller_report_problem_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    # Follow test_measured_calls' plain report file + read_calls harness.
    path = tmp_path / "calls"
    path.write_text('{"kind":"start","version":1}\n')
    if isinstance(error, OSError):
        failing = Mock(side_effect=error)
        monkeypatch.setattr("oversight.workspace.measured_report.os.open", failing)
    else:
        failing = Mock(side_effect=error)
        monkeypatch.setattr("oversight.workspace.measured_report.json.loads", failing)
    report = measured_report.read_calls(path, ())
    failing.assert_called_once()
    assert not report.complete and not report.calls
    assert len(report.problems) == 2
    expected = (
        "caller report unavailable" if isinstance(error, OSError) else "caller report unreadable"
    )
    assert_words(report.problems[0], expected, error)
    assert report.problems[1] == "caller report incomplete"


def test_problem_modules_do_not_format_exception_type_names() -> None:
    root = Path(__file__).resolve().parents[2] / "src/oversight"
    paths = ("changes/_measured.py", "changes/_probes.py", "workspace/measured_report.py")
    parsed = []
    offenders = []
    for path in paths:
        tree = ast.parse((root / path).read_text())
        parsed.append(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FormattedValue):
                continue
            for value in ast.walk(node.value):
                if (
                    isinstance(value, ast.Attribute)
                    and value.attr == "__name__"
                    and isinstance(value.value, ast.Call)
                    and isinstance(value.value.func, ast.Name)
                    and value.value.func.id == "type"
                ):
                    offenders.append((path, node.lineno))
    assert tuple(parsed) == paths
    assert not offenders, offenders
