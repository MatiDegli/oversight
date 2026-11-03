"""Fixed pytest execution in the demo environment or a validated original own-source venv.

The command runs with cwd at the disposable tree's root. Interpreter/plugin paths are absolute;
HOME and reports stay under runtime. Own-source imports map the original venv to the tested copy.
The trusted plugin lives outside the tree. This module runs under the tree's lock and parses
results from the plugin's report, never from pytest's text.
"""

import asyncio
import json
import ntpath
import re
import sys
import time
import uuid
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, cast

from oversight.limits import MAX_REPORT_BYTES, MAX_REPORT_LINE_BYTES, SizeLimitError
from oversight.test_output import retain_failure
from oversight.workspace._platform import unlink_retry
from oversight.workspace.junit import MAX_XML_BYTES, parse_junit
from oversight.workspace.locks import offload
from oversight.workspace.measured_report import CallReport, CallTarget, read_calls
from oversight.workspace.runner import NtCleanupRunner, ProcessResult, Spawn
from oversight.workspace.source_nt import OwnEnvironment
from oversight_contracts.events import CaseResult, SuiteExecution, SuiteResult

OUTPUT_TAIL = 8192
FAILED_CAP = 20

# Constant child program: neither project metadata nor finder code is evaluated by the server.
_OWN_PROBE = """
import importlib.util, json, os, platform, re
from importlib.metadata import version, PackageNotFoundError
versions = {'python': platform.python_version()}
for name in ('pytest', 'pluggy'):
    try:
        versions[name] = version(name)
    except PackageNotFoundError:
        versions[name] = 'missing'
def compatible(name, minimum):
    parts = re.match(r'^(\\d+)\\.(\\d+)', versions[name])
    return parts is not None and tuple(map(int, parts.groups())) >= minimum
ok = (compatible('python', (3, 10)) and compatible('pytest', (8, 2))
      and compatible('pluggy', (1, 5)))
try:
    policy = json.loads(os.environ['OVERSIGHT_IMPORT_GUARD'])
    expected = os.path.realpath(os.environ['OVERSIGHT_TRUSTED_PLUGIN'])
    def normalized(p):
        return os.path.normcase(os.path.realpath(p))
    def trusted(name, origin):
        if not origin:
            return False
        if name.startswith('oversight_'):
            return normalized(origin) == normalized(os.path.join(
                os.path.dirname(expected), name + '.py'))
        return any(os.path.commonpath([normalized(lib), normalized(origin)]) == normalized(lib)
                   for lib in policy['libraries'])
    names = ('pytest', 'pluggy', 'oversight_calls', 'oversight_report')
    for name in names:
        spec = importlib.util.find_spec(name)
        ok = ok and spec is not None and trusted(name, spec.origin)
    if ok:
        import pytest, pluggy, oversight_calls, oversight_report
        for name in names:
            module = __import__(name)
            ok = ok and trusted(name, module.__file__)
            ok = ok and module.__spec__ is not None and trusted(name, module.__spec__.origin)
        if ok:
            oversight_report.guard_imports()
except BaseException:
    ok = False
print(json.dumps(dict(versions, ok=ok)))
"""


def _require_guard(raw: str | None, tree: Path) -> None:
    try:
        parsed: object = json.loads(raw or "", object_pairs_hook=_unique_record)
        if not isinstance(parsed, dict):
            raise ValueError
        policy = cast(dict[str, Any], parsed)
        if (
            any(not isinstance(policy.get(k), str) or not policy[k] for k in ("tree", "source"))
            or not isinstance(policy.get("libraries"), list)
            or not policy["libraries"]
            or any(not isinstance(p, str) or not p for p in policy["libraries"])
            or not isinstance(policy.get("packages"), list)
            or any(
                not isinstance(p, str) or not p or any(not n.isidentifier() for n in p.split("."))
                for p in policy["packages"]
            )
        ):
            raise ValueError
        for path in (policy["tree"], policy["source"], *policy["libraries"]):
            if not Path(path).is_absolute() and not re.match(r"^[A-Za-z]:[\\/]", path):
                raise ValueError
        if ntpath.normcase(policy["tree"]) != ntpath.normcase(str(tree)):
            raise ValueError
    except (ValueError, TypeError, RecursionError):
        raise OwnSourceTestsUnavailable("invalid import guard policy") from None


async def _probe_environment(
    python: Path,
    tree: Path,
    env: dict[str, str],
    deadline: float,
    clock: Callable[[], float],
    spawn: Spawn,
) -> None:
    _require_guard(env.get("OVERSIGHT_IMPORT_GUARD"), tree)
    left = deadline - clock()
    if left <= 0:
        raise OwnSourceTestsUnavailable("suite deadline expired")
    result = await spawn(
        [str(python), "-c", _OWN_PROBE],
        cwd=tree,
        env=env,
        timeout_s=min(left, 10),
        output_cap=4096,
    )
    try:
        payload = json.loads(result.stdout)
        if (
            result.returncode != 0
            or result.timed_out
            or result.truncated
            or payload["ok"] is not True
        ):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        # Never echo child text or paths, even when a launcher/plugin fails before the probe.
        versions: list[str] = []
        for name in ("python", "pytest", "pluggy"):
            match = re.search(r'"' + name + r'":\s*"(missing|[0-9.]{1,32})"', result.stdout[:4096])
            versions.append(name + "=" + (match[1] if match else "unknown"))
        raise OwnSourceTestsUnavailable("probe failed (" + ", ".join(versions) + ")") from None


@dataclass(frozen=True)
class SuiteRun:
    result: SuiteResult
    output: str
    cases: tuple[CaseResult, ...] = ()
    problems: tuple[str, ...] = ("JUnit report unavailable",)
    tested_tree: str | None = None
    tested_commit: str | None = None
    measured: CallReport | None = None
    """The tail of pytest's output, 8 KB at most, with the workspace's location removed."""


class SuiteInterrupted(asyncio.CancelledError):
    """Cancellation with the partial report, for publication before propagating Stop."""

    def __init__(self, run: SuiteRun) -> None:
        self.run = run


class OwnSourceTestsUnavailable(OSError):
    """Safe diagnostic only; no paths or unbounded child output cross this boundary."""

    def __init__(self, reason: str, redact: Callable[[str], str] = lambda s: s) -> None:
        reason = " ".join(reason.removeprefix("Own-source environment unavailable: ").split())
        self.reason = _safe_detail(reason, redact) or "own-source tests could not be verified"
        super().__init__(self.reason)


class SuiteUnavailable(OSError):
    """Unavailable execution with its report and original error cause."""

    def __init__(self, run: SuiteRun, cause: OSError) -> None:
        super().__init__(cause.errno, cause.strerror)
        self.run = run
        self.cause = cause

    @property
    def own_source_reason(self) -> str | None:
        return self.cause.reason if isinstance(self.cause, OwnSourceTestsUnavailable) else None


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


def _take_report(path: Path, *, strict: bool = False) -> Iterator[str] | None:
    """Return None if absent; consume bounded lines and remove the report on completion/error."""
    try:
        handle = path.open("rb")
    except FileNotFoundError:
        return None

    def lines() -> Iterator[str]:
        try:
            with handle:
                total = 0
                while row := handle.readline(MAX_REPORT_LINE_BYTES + 1):
                    total += len(row)
                    if total > MAX_REPORT_BYTES:
                        raise SizeLimitError(
                            f"{path.name} exceeds the {MAX_REPORT_BYTES}-byte size limit"
                        )
                    if len(row) > MAX_REPORT_LINE_BYTES:
                        raise SizeLimitError(
                            f"{path.name} exceeds the {MAX_REPORT_LINE_BYTES}-byte size limit"
                        )
                    if strict and not row.endswith(b"\n"):
                        raise ValueError("truncated own-source report")
                    yield from row.decode(
                        "utf-8", errors="strict" if strict else "replace"
                    ).splitlines()
        finally:
            if sys.platform == "win32":
                unlink_retry(path, missing_ok=True, nt=True)
            else:
                path.unlink(missing_ok=True)

    return lines()


def _take_tally(report: Path) -> tuple[Tally, bool, str | None]:
    """Consume the iterator in the cleanup thread; never retain an oversized partial tally."""
    try:
        lines = _take_report(report)
        return parse_report(lines or ()), lines is not None, None
    except SizeLimitError as error:
        return Tally(), False, str(error)


def _unique_record(pairs: list[tuple[str, object]]) -> dict[str, object]:
    record: dict[str, object] = {}
    for key, value in pairs:
        if key in record:
            raise ValueError("duplicate own-source report key")
        record[key] = value
    return record


def _take_own_tally(report: Path) -> tuple[Tally, bool, str | None]:
    """Consume every bounded record before admitting any evidence, including late refusals."""
    records: list[str] = []
    admitted, reason = False, None
    try:
        lines = _take_report(report, strict=True)
        for line in lines or ():
            records.append(line)
            parsed: object = json.loads(line, object_pairs_hook=_unique_record)
            if not isinstance(parsed, dict):
                raise ValueError
            record = cast(dict[str, object], parsed)
            kind = record.get("kind")
            if kind == "guard":
                if record.get("ok") is True and set(record) == {"kind", "ok"}:
                    admitted = True
                else:
                    detail = record.get("reason")
                    reason = reason or (
                        detail
                        if record.get("ok") is False and isinstance(detail, str) and detail
                        else "own-source import guard refused or malformed"
                    )
            elif kind in ("test", "collect"):
                if (
                    not isinstance(record.get("nodeid"), str)
                    or not record["nodeid"]
                    or record.get("outcome") not in ("passed", "failed", "skipped")
                    or (kind == "test" and record.get("when") not in ("setup", "call", "teardown"))
                    or (kind == "collect" and record.get("outcome") != "failed")
                ):
                    reason = reason or "malformed own-source test record"
                if kind == "test" and not admitted:
                    reason = reason or "test record precedes import guard admission"
            elif kind != "start" or set(record) != {"kind"}:
                reason = reason or "malformed own-source report record"
        reason = reason or (None if admitted else "own-source import guard success missing")
    except (ValueError, UnicodeError, RecursionError):
        reason = "malformed, truncated or oversized own-source report"
    if reason:
        return Tally(), False, reason
    return parse_report(records), True, None


def _pytest_argv(
    python: Path,
    junit: Path,
    case_ids: tuple[str, ...],
    pytest_targets: tuple[str, ...],
    pytest_options: tuple[str, ...] = (),
) -> list[str]:
    return [
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
        *pytest_options,
        *(pytest_targets if not case_ids else ()),
        *(["--", *case_ids] if case_ids else []),
    ]


def own_source_environment(
    own: OwnEnvironment, plugin_dir: Path, runtime_root: Path, tree_root: Path
) -> dict[str, str]:
    """Shared readiness/suite policy; artifacts belong only to the actual suite.

    Construct from constants and validated paths, never the inherited environment.
    Own code keeps plugin autoload, with the trusted plugin and copy import guard required.
    """
    _require_guard(own.guard, tree_root)
    assert own.guard is not None
    home = str(runtime_root / "tmp" / "home")
    return {
        "HOME": home,
        "TEMP": home,
        "TMP": home,
        "PYTHONPATH": (";" if sys.platform == "win32" else ":").join(
            map(str, (plugin_dir, tree_root, *own.roots))
        ),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "OVERSIGHT_TRUSTED_PLUGIN": str(plugin_dir / "oversight_report.py"),
        "OVERSIGHT_IMPORT_GUARD": own.guard,
    }


async def run_suite(
    *,
    python: Path,
    plugin_dir: Path,
    runtime_root: Path,
    tree_root: Path,
    timeout_s: float,
    redact: Callable[[str], str],
    spawn: Spawn,
    case_ids: tuple[str, ...] = (),
    pytest_targets: tuple[str, ...] = (),
    pytest_options: tuple[str, ...] = (),
    harness_pack: bool = False,
    measured_targets: tuple[CallTarget, ...] | None = None,
    _own_prepare: Callable[[Path, float], OwnEnvironment] | None = None,
    _clock: Callable[[], float] = time.monotonic,
) -> SuiteRun:
    deadline = _clock() + timeout_s if _own_prepare is not None else None
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
    request, recording = report.with_suffix(".calls-input"), report.with_suffix(".calls")
    if measured_targets is not None:
        env.update(OVERSIGHT_CALL_TARGETS=str(request), OVERSIGHT_CALL_REPORT=str(recording))
    argv = _pytest_argv(python, junit, case_ids, pytest_targets, pytest_options)
    failure: OSError | asyncio.CancelledError | None = None
    try:
        if _own_prepare is not None:
            assert deadline is not None
            try:
                own = await offload(lambda: _own_prepare(tree_root, deadline))
            except OSError as error:
                raise OwnSourceTestsUnavailable(str(error), redact) from error
            python = own.python
            own_env = own_source_environment(own, plugin_dir, runtime_root, tree_root)
            env.pop("PYTEST_DISABLE_PLUGIN_AUTOLOAD")
            env.update(own_env)
            await _probe_environment(python, tree_root, env, deadline, _clock, spawn)
            argv = _pytest_argv(python, junit, case_ids, pytest_targets, pytest_options)
        if measured_targets is not None:
            await offload(
                lambda: request.write_text(json.dumps([asdict(t) for t in measured_targets]))
            )
        if deadline is not None:
            timeout_s = deadline - _clock()
            if timeout_s <= 0:
                raise OwnSourceTestsUnavailable("suite deadline expired")
        process = await spawn(argv, cwd=tree_root, timeout_s=timeout_s, env=env)
    except (OSError, asyncio.CancelledError) as error:
        failure = error
        process = ProcessResult(None, "", "", isinstance(error, asyncio.CancelledError), False, 0)
    finally:
        if isinstance(spawn, NtCleanupRunner) and not spawn.cleanup_complete:
            # Live descendants may still write these artifacts. Keep them for recovery;
            # the wrapper will refuse evidence and restoration while retaining its locks.
            tally, report_found, report_error = Tally(), False, "Windows Job cleanup is unverified"
            cases, problems, measured = (), (report_error,), None
        else:
            take_tally = _take_tally if _own_prepare is None else _take_own_tally
            tally, report_found, report_error = await offload(lambda: take_tally(report))
            cases, problems = await offload(lambda: _take_cases(junit, redact))
            measured = None
            if measured_targets is not None:
                try:
                    measured = await offload(lambda: read_calls(recording, measured_targets))
                finally:
                    if sys.platform == "win32":
                        try:
                            await offload(lambda: unlink_retry(request, missing_ok=True, nt=True))
                        finally:
                            await offload(lambda: unlink_retry(recording, missing_ok=True, nt=True))
                    else:
                        await offload(
                            lambda: (
                                request.unlink(missing_ok=True),
                                recording.unlink(missing_ok=True),
                            )
                        )
    unavailable = None
    if _own_prepare is not None:
        if isinstance(failure, OwnSourceTestsUnavailable):
            unavailable = failure
        elif report_error is not None or isinstance(failure, OSError):
            unavailable = OwnSourceTestsUnavailable(
                report_error or "own-source process did not complete", redact
            )
        if unavailable is not None:
            tally, cases, measured = Tally(), (), None
            problems = (unavailable.reason,)
            report_error = unavailable.reason
            if failure is None:
                failure = unavailable
    elif report_error is not None:
        tally.failed_tests.append(report_error)
    result = summarize(
        tally,
        returncode=process.returncode,
        timed_out=process.timed_out,
        report_found=report_found,
        duration_ms=process.duration_ms,
    )
    if unavailable is not None:
        result = result.model_copy(
            update={"outcome": "error", "cases": [], "cases_complete": False}
        )
    text = process.stdout
    if process.stderr.strip():
        text += "\n" + process.stderr
    if report_error is not None:
        text += "\n" + report_error
    # JUnit adds one terminal footer; keep the tool response in its existing format.
    text = "\n".join(row for row in text.split("\n") if f"generated xml file: {junit}" not in row)
    if process.timed_out:
        problems += ("suite timed out",)
    text = redact(text)
    if unavailable is not None:
        text = text[-(OUTPUT_TAIL - 300) :] + "\ntests unavailable: " + unavailable.reason
    output = retain_failure(text, OUTPUT_TAIL) if harness_pack else text[-OUTPUT_TAIL:]
    if measured is not None and (problems or failure or result.outcome in ("error", "timeout")):
        measured = replace(
            measured, complete=False, problems=(*measured.problems, "suite incomplete")
        )
    run = SuiteRun(result, output, cases, problems, measured=measured)
    if isinstance(failure, asyncio.CancelledError):
        raise SuiteInterrupted(run) from failure
    if isinstance(failure, OSError):
        raise SuiteUnavailable(run, failure) from failure
    return run


# Conservative and local: workspace and events are independent layers.
_DETAIL_PATH = re.compile(r"(?<!\w)/|~/|file:|[A-Za-z]:[\\/]|\\{2}", re.IGNORECASE)


def _safe_detail(message: str | None, redact: Callable[[str], str]) -> str | None:
    if message is None:
        return None
    text = redact(message).strip()
    text = text[:239] + "…" if len(text) > 240 else text
    return text if text and not _DETAIL_PATH.search(text) else None


def _take_cases(
    path: Path, redact: Callable[[str], str] = lambda text: text
) -> tuple[tuple[CaseResult, ...], tuple[str, ...]]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as report:
            parsed = parse_junit(report.read(MAX_XML_BYTES + 1))
    except FileNotFoundError:
        return (), ("JUnit report unavailable",)
    finally:
        if sys.platform == "win32":
            unlink_retry(path, missing_ok=True, nt=True)
        else:
            path.unlink(missing_ok=True)
    return (
        tuple(
            CaseResult(
                test_id=key, outcome=value, detail=_safe_detail(parsed.details.get(key), redact)
            )
            for key, value in sorted(parsed.outcomes.items())
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
