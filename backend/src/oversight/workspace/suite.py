"""The test runner: one fixed pytest command, in the demo repo's own environment.

The command runs with cwd at the tree's root. The interpreter, plugin directory, report file and
HOME are absolute and derived from the runtime root; the plugin lives outside the tree. This module
is called under the tree's lock, and the result is parsed from the plugin's report, never from
pytest's text.
"""

import json
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from oversight.workspace.locks import offload
from oversight.workspace.runner import Spawn
from oversight_contracts.events import SuiteResult

OUTPUT_TAIL = 8192
FAILED_CAP = 20


@dataclass(frozen=True)
class SuiteRun:
    result: SuiteResult
    output: str
    """The tail of pytest's output, 8 KB at most, with the workspace's location removed."""


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
    ]
    try:
        process = await spawn(argv, cwd=tree_root, timeout_s=timeout_s, env=env)
    finally:
        lines = await offload(lambda: _take_report(report))
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
    return SuiteRun(result=result, output=redact(text)[-OUTPUT_TAIL:])


__all__ = ["SuiteRun", "Tally", "parse_report", "run_suite", "summarize"]
