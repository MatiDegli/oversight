import asyncio
import json
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from oversight.limits import MAX_REPORT_LINE_BYTES
from oversight.workspace.runner import DEFAULT_OUTPUT_CAP, ProcessResult, run_process
from oversight.workspace.suite import OUTPUT_TAIL, parse_report, run_suite, summarize


def line(kind: str, nodeid: str = "", when: str = "", outcome: str = "") -> str:
    return json.dumps({"kind": kind, "nodeid": nodeid, "when": when, "outcome": outcome})


def run(lines: list[str], *, returncode: int | None, timed_out: bool = False, found: bool = True):
    return summarize(
        parse_report(lines),
        returncode=returncode,
        timed_out=timed_out,
        report_found=found,
        duration_ms=12,
    )


def test_all_passing() -> None:
    lines = [line("start")] + [
        line("test", f"t.py::t{i}", phase, "passed")
        for i in range(3)
        for phase in ("setup", "call", "teardown")
    ]
    result = run(lines, returncode=0)
    assert (result.outcome, result.passed, result.failed, result.skipped) == ("passed", 3, 0, 0)
    assert result.failed_tests == []
    assert result.duration_ms == 12


def test_failures_and_errors_in_any_phase_count_as_failed() -> None:
    lines = [
        line("test", "t.py::fails", "setup", "passed"),
        line("test", "t.py::fails", "call", "failed"),
        line("test", "t.py::fails", "teardown", "passed"),
        line("test", "t.py::setup_error", "setup", "failed"),
        line("test", "t.py::teardown_error", "setup", "passed"),
        line("test", "t.py::teardown_error", "call", "passed"),
        line("test", "t.py::teardown_error", "teardown", "failed"),
        line("test", "t.py::ok", "call", "passed"),
    ]
    result = run(lines, returncode=1)
    assert result.outcome == "failed"
    assert (result.passed, result.failed) == (2, 3)
    assert result.failed_tests == ["t.py::fails", "t.py::setup_error", "t.py::teardown_error"]


def test_skips_and_xfails_are_skipped_and_xpass_is_passed() -> None:
    lines = [
        line("test", "t.py::skipped", "setup", "skipped"),
        line("test", "t.py::xfailed", "setup", "passed"),
        line("test", "t.py::xfailed", "call", "skipped"),
        line("test", "t.py::xpassed", "call", "passed"),
        line("test", "t.py::skipped", "teardown", "passed"),
    ]
    result = run(lines, returncode=0)
    assert (result.outcome, result.passed, result.skipped, result.failed) == ("passed", 1, 2, 0)


def test_a_collection_error_is_an_error_and_names_the_file() -> None:
    lines = [line("test", "t.py::ok", "call", "passed"), line("collect", "broken/test_x.py")]
    result = run(lines, returncode=2)
    assert result.outcome == "error"
    assert (result.passed, result.failed) == (1, 1)
    assert result.failed_tests == ["broken/test_x.py"]
    assert run(lines, returncode=1).outcome == "error"


def test_only_the_first_twenty_failures_are_named_and_each_once() -> None:
    lines = [line("test", f"t.py::t{i:02}", "call", "failed") for i in range(30)]
    lines += [line("test", "t.py::t00", "teardown", "failed")]
    result = run(lines, returncode=1)
    assert result.failed == 31
    assert result.failed_tests == [f"t.py::t{i:02}" for i in range(20)]


@pytest.mark.parametrize("code", [2, 3, 4, 5, -9, None])
def test_other_exit_codes_and_signals_are_errors(code: int | None) -> None:
    assert run([line("start")], returncode=code).outcome == "error"


def test_no_tests_is_exit_5_and_an_error() -> None:
    result = run([line("start")], returncode=5)
    assert (result.outcome, result.passed, result.failed) == ("error", 0, 0)


def test_a_missing_report_is_an_error_even_on_exit_zero() -> None:
    assert run([], returncode=0, found=False).outcome == "error"


def test_a_timeout_keeps_the_counts_so_far_and_a_cut_line_is_ignored() -> None:
    lines = [
        line("test", "t.py::a", "call", "passed"),
        line("test", "t.py::b", "call", "failed"),
        '{"kind": "test", "nodeid": "t.py::c", "wh',
    ]
    result = run(lines, returncode=-15, timed_out=True)
    assert (result.outcome, result.passed, result.failed) == ("timeout", 1, 1)


def test_garbage_lines_are_ignored() -> None:
    result = run(
        ["", "[]", "42", "not json", line("test", "t.py::a", "call", "passed")], returncode=0
    )
    assert (result.outcome, result.passed) == ("passed", 1)


@pytest.mark.anyio
async def test_a_long_run_still_ends_its_output_with_the_summary(tmp_path: Path) -> None:
    """More than the runner's cap of output: the tail kept is the real end, not the 8 KB before
    the cap. A script stands in for the interpreter and ignores the pytest arguments."""
    fake_python = tmp_path / "fake-python"
    size = DEFAULT_OUTPUT_CAP + 100_000
    fake_python.write_text(
        f"#!/bin/sh\nhead -c {size} /dev/zero | tr '\\0' 'x'\necho\necho '== 2 passed in 0.1s =='\n"
    )
    fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)
    run = await run_suite(
        python=fake_python,
        plugin_dir=tmp_path,
        runtime_root=tmp_path,
        tree_root=tmp_path,
        timeout_s=60,
        redact=lambda text: text,
        spawn=run_process,
    )
    assert len(run.output) == OUTPUT_TAIL
    assert run.output.endswith("\n== 2 passed in 0.1s ==\n")


@pytest.mark.anyio
async def test_an_oversized_report_names_the_report_and_is_removed(tmp_path: Path) -> None:
    report: Path | None = None

    async def spawn(
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout_s: float,
        env: Mapping[str, str] | None = None,
        output_cap: int = DEFAULT_OUTPUT_CAP,
    ) -> ProcessResult:
        nonlocal report
        assert env is not None
        report = Path(env["OVERSIGHT_REPORT"])
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_bytes(b"x" * (MAX_REPORT_LINE_BYTES + 1))
        return ProcessResult(0, "pytest finished", "", False, False, 12)

    run = await run_suite(
        python=tmp_path / "python",
        plugin_dir=tmp_path,
        runtime_root=tmp_path,
        tree_root=tmp_path,
        timeout_s=60,
        redact=lambda text: text,
        spawn=spawn,
    )
    assert report is not None
    assert run.result.outcome == "error"
    reason = f"{report.name} exceeds the {MAX_REPORT_LINE_BYTES}-byte size limit"
    assert run.result.failed_tests == [reason]
    assert run.output.endswith(reason)
    assert not await asyncio.to_thread(report.exists)
