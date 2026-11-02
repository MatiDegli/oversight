"""Own report admission with injected processes on Linux; no native acceptance."""

import asyncio
import json
from pathlib import Path

import pytest

from oversight.workspace import source_nt as nt
from oversight.workspace import suite
from oversight.workspace.measured_report import CallReport
from oversight.workspace.runner import ProcessResult

from .test_source_nt_env import guard_policy

GUARD = {"kind": "guard", "ok": True}
TEST = {"kind": "test", "nodeid": "test_x.py::test_x", "when": "call", "outcome": "passed"}


def report(*records):
    return "".join(json.dumps(r) + "\n" for r in records).encode()


async def injected_suite(tmp_path, data, *, mode="pass", prepare_error=None):
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    process_error = OSError("Windows job cleanup did not complete before its deadline")

    def prepare(*_):
        if prepare_error:
            raise prepare_error
        return nt.OwnEnvironment(Path("original-python"), guard=guard_policy(tmp_path))

    async def spawn(argv, *, env=None, **kwargs):
        assert env is not None
        assert argv[0] == "original-python"
        if argv[1] == "-c":
            return ProcessResult(0, '{"ok":true}', "", False, False, 1)
        path = Path(env["OVERSIGHT_REPORT"])
        if data is not None:
            await asyncio.to_thread(path.write_bytes, data)
        if "OVERSIGHT_CALL_REPORT" in env:
            await asyncio.to_thread(Path(env["OVERSIGHT_CALL_REPORT"]).write_text, "forged")
        # Deliberately passing evidence must never repair a refused plugin report.
        path.with_suffix(".xml").write_text(
            '<testsuite><testcase classname="test_x" name="test_x"/></testsuite>'
        )
        if mode == "cancel":
            raise asyncio.CancelledError
        if mode == "cleanup":
            raise process_error
        return ProcessResult(
            1 if mode == "fail" else 0, "diagnostic", "", mode == "timeout", False, 2
        )

    try:
        return await suite.run_suite(
            python=Path("unused"),
            plugin_dir=tmp_path,
            runtime_root=tmp_path,
            tree_root=tmp_path,
            timeout_s=13,
            redact=lambda s: s.replace("credential", "[redacted]"),
            spawn=spawn,
            _own_prepare=prepare,
            measured_targets=(),
        )
    except suite.SuiteUnavailable as error:
        if mode == "cleanup":
            assert error.cause is process_error
            assert error.own_source_reason is None
        raise
    finally:
        assert list(scratch.iterdir()) == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "data",
    [
        None,
        report({"kind": "start"}),
        report({"kind": "guard", "ok": False}),
        report({"kind": "guard", "ok": "true"}),
        report(TEST, GUARD),
        report(GUARD, TEST, {"kind": "guard", "ok": False, "reason": "late refusal"}),
        report({"kind": "guard", "ok": False}, GUARD, TEST),
        report(GUARD, TEST) + b'{"kind":',
        report(GUARD, TEST).rstrip(b"\n"),
        report(GUARD, TEST) + b"\xff\n",
        report(GUARD, []),
        report(GUARD, {"kind": "unknown"}),
        report({**GUARD, "reason": []}, TEST),
        report(GUARD, {**TEST, "when": "unknown"}),
        b'{"kind":"guard","ok":false,"ok":true}\n' + report(TEST),
        report(GUARD, TEST) + b"[" * 1500 + b"]" * 1500 + b"\n",
    ],
)
async def test_invalid_guard_discards_all_evidence_even_with_green_junit(
    tmp_path, monkeypatch, data
):
    monkeypatch.setattr(suite, "read_calls", lambda *_: CallReport(complete=True, problems=()))
    with pytest.raises(suite.SuiteUnavailable) as error:
        await injected_suite(tmp_path, data)
    run = error.value.run
    assert error.value.own_source_reason
    assert run.result.outcome == "error"
    assert (run.result.passed, run.result.failed, run.result.skipped) == (0, 0, 0)
    assert run.result.failed_tests == [] and run.cases == () and run.measured is None
    assert run.result.cases == [] and run.result.cases_complete is False
    assert error.value.own_source_reason in run.problems
    assert "tests unavailable: " + error.value.own_source_reason in run.output


@pytest.mark.anyio
@pytest.mark.parametrize("limit", ["MAX_REPORT_BYTES", "MAX_REPORT_LINE_BYTES"])
async def test_oversized_report_discards_partial_green_tally(tmp_path, monkeypatch, limit):
    monkeypatch.setattr(suite, limit, 150)
    with pytest.raises(suite.SuiteUnavailable) as error:
        await injected_suite(tmp_path, report(GUARD, TEST) + b" " * 151 + b"\n")
    assert error.value.run.result.passed == 0 and error.value.run.cases == ()


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["pass", "fail", "timeout", "cancel", "cleanup"])
@pytest.mark.parametrize("admitted", [False, True])
async def test_guarded_outcomes_cancellation_and_cleanup(tmp_path, mode, admitted):
    data = (
        report(GUARD, {**TEST, "outcome": "failed" if mode == "fail" else "passed"})
        if admitted
        else report(TEST)
    )
    if mode == "cancel":
        with pytest.raises(suite.SuiteInterrupted) as error:
            await injected_suite(tmp_path, data, mode=mode)
        run = error.value.run
        assert run.result.outcome == ("timeout" if admitted else "error")
    elif not admitted or mode == "cleanup":
        with pytest.raises(suite.SuiteUnavailable) as unavailable:
            await injected_suite(tmp_path, data, mode=mode)
        run = unavailable.value.run
        assert run.result.outcome == "error" and run.cases == ()
        assert (run.result.passed, run.result.failed, run.result.skipped) == (0, 0, 0)
    else:
        run = await injected_suite(tmp_path, data, mode=mode)
        assert (
            run.result.outcome == {"pass": "passed", "fail": "failed", "timeout": "timeout"}[mode]
        )
        assert (run.result.passed, run.result.failed) == ((0, 1) if mode == "fail" else (1, 0))
    if not admitted:
        assert run.result.passed == run.result.failed == 0 and run.cases == ()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "message", ["credential " + "x" * 400, "C:/private/credential", "missing original venv"]
)
async def test_preparation_reason_is_bounded_redacted_and_never_probes(tmp_path, message):
    with pytest.raises(suite.SuiteUnavailable) as error:
        await injected_suite(tmp_path, None, prepare_error=OSError(message))
    reason = error.value.own_source_reason
    assert reason and len(reason) <= 240 and "credential" not in reason and "private" not in reason
    assert error.value.run.result.outcome == "error"
