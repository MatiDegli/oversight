"""The test runner: one fixed pytest command, in the demo repo's own environment.

The command runs with cwd at the tree's root. The interpreter, plugin directory, report file and
HOME are absolute and derived from the runtime root; the plugin lives outside the tree. This module
is called under the tree's lock, and the result is parsed from the plugin's report, never from
pytest's text.
"""

import asyncio
import json
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from oversight.workspace.junit import MAX_XML_BYTES, parse_junit
from oversight.workspace.locks import offload
from oversight.workspace.runner import ProcessResult, Spawn
from oversight_contracts.events import CaseResult, SuiteExecution, SuiteResult

OUTPUT_TAIL = 8192
FAILED_CAP = 20


@dataclass(frozen=True)
class SuiteRun:
    result: SuiteResult
    output: str
    cases: tuple[CaseResult, ...] = ()
    problems: tuple[str, ...] = ("JUnit report unavailable",)
    tested_tree: str | None = None
    tested_commit: str | None = None
    """The tail of pytest's output, 8 KB at most, with the workspace's location removed."""


class SuiteInterrupted(asyncio.CancelledError):
    """Cancellation with the partial report, for publication before propagating Stop."""

    def __init__(self, run: SuiteRun) -> None:
        self.run = run


class SuiteUnavailable(OSError):
    """A failed spawn with its report and original error; model text stays unchanged."""

    def __init__(self, run: SuiteRun, cause: OSError) -> None:
        super().__init__(cause.errno, cause.strerror)
        self.run = run
        self.cause = cause


@dataclass
class Tally:
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    collection_errors: int = 0
    failed_tests: list[str] = field(default_factory=lambda: list[str]())


def parse_report(lines: Iterable[str]) -> Tally:
    """Count what the plugin reported. A passed call is a pass, a failure or error in any phase is a
    failure, a skip or xfail is a skip, and a collection error is a failure named by its file."""
    tally = Tally()
    skipped_nodes: set[str] = set()
    for line in lines:
        try:
            parsed: object = json.loads(line)
        except json.JSONDecodeError:
            continue  # the process was killed in the middle of a line
        if not isinstance(parsed, dict):
            continue
        record = cast(dict[str, object], parsed)
        kind, nodeid, outcome = record.get("kind"), str(record.get("nodeid")), record.get("outcome")
        if kind == "collect":
            tally.failed += 1
            tally.collection_errors += 1
            tally.failed_tests.append(nodeid)
        elif kind == "test":
            when = record.get("when")
            if outcome == "failed":
                tally.failed += 1
                tally.failed_tests.append(nodeid)
            elif when == "call" and outcome == "passed":
                tally.passed += 1
            elif when in ("setup", "call") and outcome == "skipped" and nodeid not in skipped_nodes:
                skipped_nodes.add(nodeid)
                tally.skipped += 1
    return tally


def summarize(
    tally: Tally, *, returncode: int | None, timed_out: bool, report_found: bool, duration_ms: int
) -> SuiteResult:
    """The outcome: passed on exit 0, failed on 1, timeout when killed (counts so far), error for
    exits 2 to 5 (5 is no tests), a signal, a missing report or a collection error."""
    if timed_out:
        outcome = "timeout"
    elif not report_found or tally.collection_errors or returncode not in (0, 1):
        outcome = "error"
    else:
        outcome = "passed" if returncode == 0 else "failed"
    failed_tests = list(dict.fromkeys(tally.failed_tests))[:FAILED_CAP]
    return SuiteResult(
        outcome=outcome,
        passed=tally.passed,
        failed=tally.failed,
        skipped=tally.skipped,
        duration_ms=duration_ms,
        failed_tests=failed_tests,
    )


def _take_report(path: Path) -> list[str] | None:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return None
    path.unlink(missing_ok=True)
    return lines


async def run_suite(
    *,
    python: Path,
    plugin_dir: Path,
    runtime_root: Path,
    tree_root: Path,
    timeout_s: float,
    redact: Callable[[str], str],
    spawn: Spawn,
) -> SuiteRun:
    report = runtime_root / "tmp" / f"{uuid.uuid4().hex}.jsonl"
    junit = report.with_suffix(".xml")
    env = {
        "HOME": str(runtime_root / "tmp" / "home"),
        "PYTHONPATH": str(plugin_dir),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "OVERSIGHT_REPORT": str(report),
    }
    argv = [
        str(python),
        "-m",
        "pytest",
        "-q",
        "--color=no",
        "-p",
        "no:cacheprovider",
        "-p",
        "oversight_report",
        "-o",
        "junit_family=xunit1",
        f"--junitxml={junit}",
    ]
    failure: OSError | asyncio.CancelledError | None = None
    try:
        process = await spawn(argv, cwd=tree_root, timeout_s=timeout_s, env=env)
    except (OSError, asyncio.CancelledError) as error:
        failure = error
        process = ProcessResult(None, "", "", isinstance(error, asyncio.CancelledError), False, 0)
    finally:
        lines = await offload(lambda: _take_report(report))
        cases, problems = await offload(lambda: _take_cases(junit))
    tally = parse_report(lines or [])
    result = summarize(
        tally,
        returncode=process.returncode,
        timed_out=process.timed_out,
        report_found=lines is not None,
        duration_ms=process.duration_ms,
    )
    text = process.stdout
    if process.stderr.strip():
        text += "\n" + process.stderr
    # JUnit adds one terminal footer; keep the tool response in its existing format.
    text = "\n".join(row for row in text.split("\n") if f"generated xml file: {junit}" not in row)
    if process.timed_out:
        problems += ("suite timed out",)
    run = SuiteRun(result, redact(text)[-OUTPUT_TAIL:], cases, problems)
    if isinstance(failure, asyncio.CancelledError):
        raise SuiteInterrupted(run) from failure
    if isinstance(failure, OSError):
        raise SuiteUnavailable(run, failure) from failure
    return run


def _take_cases(path: Path) -> tuple[tuple[CaseResult, ...], tuple[str, ...]]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as report:
            parsed = parse_junit(report.read(MAX_XML_BYTES + 1))
    except FileNotFoundError:
        return (), ("JUnit report unavailable",)
    finally:
        path.unlink(missing_ok=True)
    return (
        tuple(
            CaseResult(test_id=key, outcome=value) for key, value in sorted(parsed.outcomes.items())
        ),
        tuple(p.code for p in parsed.problems),
    )


def validate_suite_execution(
    execution: SuiteExecution,
    run: SuiteRun,
    *,
    change_id: str,
    agent_id: str,
    revision: int | None,
    phase: str,
) -> None:
    """Validate captured identity, tree and report before publishing a projection.

    Counters count phases; they are never compared with the number of cases.
    Retention may omit cases but cannot invent, alter or certify partial evidence.
    """
    if (execution.change_id, execution.agent_id, execution.revision, execution.phase) != (
        change_id,
        agent_id,
        revision,
        phase,
    ):
        raise ValueError("suite identity or phase differs from captured execution")
    if (execution.tested_tree, execution.tested_commit) != (run.tested_tree, run.tested_commit):
        raise ValueError("suite tree or commit differs from captured execution")
    fields = {"cases", "cases_complete"}
    if execution.suite.model_dump(exclude=fields) != run.result.model_dump(exclude=fields):
        raise ValueError("suite counters differ from captured report")
    full = {case.test_id: case for case in run.cases}
    cases = execution.suite.cases
    if cases is None or any(full.get(case.test_id) != case for case in cases):
        raise ValueError("suite cases differ from captured report")
    if execution.suite.cases_complete is not (not run.problems and len(cases) == len(full)):
        raise ValueError("suite completeness differs from captured report")


__all__ = ["SuiteRun", "Tally", "parse_report", "run_suite", "summarize"]
