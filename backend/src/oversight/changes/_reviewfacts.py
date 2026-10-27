"""Pure review evidence from one run's contract events; no policy or runtime wiring.

Agent ids identify one change per run (contracts.common.ChangeId). Supply the
run's complete trace: absence then means no observed call, not a missing page.
Paths/nodes are historical touches, including user edits and later reversions;
size counters are the latest whole-change totals, never sums of successive edits.
Duration is summed tool time, not wall time or model time. Only failed test ids
exist in this contract; passed/skipped ids cannot be recovered from totals.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import fsum, isfinite
from typing import Literal

from oversight_contracts.events import (
    AgentStatus,
    ChangeProposed,
    ChangeResolved,
    ChangeUpdated,
    ServerEvent,
    SuiteResult,
)

type SuiteOutcome = Literal["passed", "failed", "error", "timeout"]
type DiscrepancyKind = Literal[
    "tests_not_run", "tests_not_passing", "file_not_touched", "file_not_mentioned"
]


@dataclass(frozen=True)
class TestRunFact:
    """One agent run_tests call, including calls without a structured result."""

    seq: int
    step: int
    result: Literal["ok", "error"]
    outcome: SuiteOutcome | None
    passed: int | None
    failed: int | None
    skipped: int | None
    duration_ms: int | None
    failed_tests: tuple[str, ...]
    failed_tests_truncated: bool | None


@dataclass(frozen=True)
class ErrorFact:
    seq: int
    source: str
    message: str | None


@dataclass(frozen=True)
class ReviewFacts:
    agent_id: str
    change_id: str
    files: tuple[str, ...]
    nodes: tuple[str, ...]
    files_changed: int | None
    lines_added: int | None
    lines_removed: int | None
    tests: tuple[TestRunFact, ...]
    steps: int
    errors: tuple[ErrorFact, ...]
    duration_ms: int
    cost_usd: float | None


@dataclass(frozen=True)
class FinishClaims:
    """None means unclaimed/ambiguous; an empty files tuple explicitly claims none."""

    files: tuple[str, ...] | None = None
    ran_tests: bool | None = None
    tests_pass: bool | None = None


@dataclass(frozen=True)
class Discrepancy:
    kind: DiscrepancyKind
    path: str | None = None


def _test_run(
    seq: int, step: int, result: Literal["ok", "error"], suite: SuiteResult | None
) -> TestRunFact:
    return TestRunFact(
        seq,
        step,
        result,
        suite.outcome if suite else None,
        suite.passed if suite else None,
        suite.failed if suite else None,
        suite.skipped if suite else None,
        suite.duration_ms if suite else None,
        tuple(suite.failed_tests) if suite else (),
        len(suite.failed_tests) < suite.failed if suite else None,
    )


def _ordered(events: Iterable[ServerEvent]) -> tuple[ServerEvent, ...]:
    """SSE redelivery is idempotent; conflicting envelopes cannot establish facts."""
    unique: dict[int, ServerEvent] = {}
    for event in events:
        if event.seq in unique and unique[event.seq] != event:
            raise ValueError(f"conflicting events at seq {event.seq}")
        unique[event.seq] = event
    return tuple(unique[key] for key in sorted(unique))


def _agent_matches(events: tuple[ServerEvent, ...], agent_id: str, change_id: str) -> bool:
    bound_changes = {
        event.payload.change_id
        for event in events
        if isinstance(event.payload, (AgentStatus, ChangeUpdated, ChangeProposed, ChangeResolved))
        and event.payload.agent_id == agent_id
    }
    return not bound_changes or bound_changes == {change_id}


def facts_from_events(agent_id: str, change_id: str, events: Iterable[ServerEvent]) -> ReviewFacts:
    """Fold snapshots and observations, ordered by seq, without mutating events.

    Proposal tests are server verification, not agent run_tests calls, and cannot
    substantiate the author's claim to have run tests. agent.focus is not an edit.
    Error records and tool errors remain separate observations (not unique causes).
    Missing/unknown/nonfinite cost on any call leaves total cost unknown. Ledger
    spent_usd is global and is never used as this change's cost.
    """
    files: set[str] = set()
    nodes: set[str] = set()
    totals: tuple[int | None, int | None, int | None] = (None, None, None)
    tests: list[TestRunFact] = []
    errors: list[ErrorFact] = []
    steps, duration = 0, 0
    costs: list[float | None] = []
    ordered = _ordered(events)
    agent_matches = _agent_matches(ordered, agent_id, change_id)
    for event in ordered:
        if isinstance(event.payload, (ChangeUpdated, ChangeProposed)):
            change = event.payload
            if (change.agent_id, change.change_id) != (agent_id, change_id):
                continue
            if isinstance(change, ChangeUpdated):
                files.add(change.file.path)
                nodes.update(change.node_ids)
                count = change.files_changed
            else:
                files.update(file.path for file in change.files)
                count = len(change.files)
            totals = (count, change.lines_added, change.lines_removed)
            nodes.update(node.id for node in change.graph.nodes_added)
            nodes.update(change.graph.nodes_modified)
            nodes.update(change.graph.nodes_removed)
        elif event.type == "agent.step" and agent_matches and event.payload.agent_id == agent_id:
            step = event.payload
            steps += 1
            duration += step.duration_ms
            if step.result == "error":
                errors.append(ErrorFact(event.seq, step.tool, step.error))
            if step.tool == "run_tests":
                tests.append(_test_run(event.seq, step.step, step.result, step.tests))
        elif event.type == "usage.tick" and agent_matches and event.payload.agent_id == agent_id:
            costs.append(event.payload.cost_usd)
        elif event.type == "error":
            report = event.payload
            if (
                (report.agent_id is not None or report.change_id is not None)
                and report.agent_id in {None, agent_id}
                and report.change_id in {None, change_id}
            ):
                errors.append(ErrorFact(event.seq, report.source, report.message))
    known = [cost for cost in costs if cost is not None and isfinite(cost) and cost >= 0]
    return ReviewFacts(
        agent_id,
        change_id,
        tuple(sorted(files)),
        tuple(sorted(nodes)),
        *totals,
        tuple(tests),
        steps,
        tuple(errors),
        duration,
        fsum(known) if costs and len(known) == len(costs) else None,
    )


_RUN_YES = re.compile(r"(?:i )?ran (?:the )?tests|(?:the )?tests were run", re.I)
_RUN_NO = re.compile(r"(?:i )?did not run (?:the )?tests|(?:the )?tests were not run", re.I)
_PASS_YES = re.compile(r"(?:all |the )?tests (?:pass|passed)", re.I)
_PASS_NO = re.compile(r"(?:all |the )?tests (?:failed|fail|did not pass)", re.I)
_FILES = re.compile(
    r"(?:(?:i )?(?:updated|edited|modified|changed|touched|added|removed|deleted|fixed) "
    r"|files(?: (?:changed|touched))?:\s*)(.+)",
    re.I,
)


def _boolean_claim(
    sentences: tuple[str, ...], yes: re.Pattern[str], no: re.Pattern[str]
) -> bool | None:
    values = {True for sentence in sentences if yes.fullmatch(sentence)}
    values.update(False for sentence in sentences if no.fullmatch(sentence))
    return next(iter(values)) if len(values) == 1 else None


def claims_from_summary(text: str, workspace_paths: Iterable[str]) -> FinishClaims:
    """Recognize only complete, explicit English statements, not general prose.

    File statements must be an edit verb or 'Files:' followed by an exact list of
    allowlisted paths (commas/'and', optional backticks). Incidental path mentions,
    quotes, plans, negations and unfamiliar wording stay unknown. Conflicting
    boolean statements stay unknown. Callers supply paths from the change; this
    parser cannot discover claims about arbitrary workspace or absolute paths.
    """
    sentences = tuple(
        part.strip().removeprefix("- ")
        for part in re.split(r"[.!?](?:\s+|$)|\n", text)
        if part.strip()
    )
    allowed = set(workspace_paths)
    files: set[str] = set()
    has_files, no_files = False, False
    for sentence in sentences:
        if sentence.lower() in {"no files changed", "i changed no files"}:
            no_files = True
        match = _FILES.fullmatch(sentence)
        if match is None:
            continue
        paths = [part.strip().strip("`") for part in re.split(r",\s*|\s+and\s+", match[1])]
        if paths and all(path in allowed for path in paths):
            has_files = True
            files.update(paths)
    claimed_files = tuple(sorted(files)) if has_files else (() if no_files else None)
    if has_files and no_files:
        claimed_files = None
    return FinishClaims(
        claimed_files,
        _boolean_claim(sentences, _RUN_YES, _RUN_NO),
        _boolean_claim(sentences, _PASS_YES, _PASS_NO),
    )


def said_versus_did(claims: FinishClaims, facts: ReviewFacts) -> tuple[Discrepancy, ...]:
    """Unknown claims/results prove no discrepancy; timeout/error are not passes."""
    result: list[Discrepancy] = []
    if claims.ran_tests is True and not facts.tests:
        result.append(Discrepancy("tests_not_run"))
    if (
        claims.tests_pass is True
        and facts.tests
        and facts.tests[-1].outcome in {"failed", "error", "timeout"}
    ):
        result.append(Discrepancy("tests_not_passing"))
    if claims.files is not None:
        claimed, touched = set(claims.files), set(facts.files)
        result.extend(Discrepancy("file_not_touched", path) for path in sorted(claimed - touched))
        result.extend(Discrepancy("file_not_mentioned", path) for path in sorted(touched - claimed))
    return tuple(result)


def tests_line(counts: Mapping[str, int] | str | None) -> str:
    """Accept Counter(finding.kind.value), or an explicit unavailable reason.

    test_removed -> removed; assertion_loosened -> checks changed;
    expected_value_changed -> expected values changed; skip_added -> skips added.
    Count findings, not distinct tests. literal_branch/stub_left are non-test
    findings and excluded. parse_error or unfamiliar kinds make facts unknown.
    The adapter must supply a reason when detector input was unavailable/incomplete.
    """
    if counts is None or isinstance(counts, str):
        return f"test facts unknown: {counts or 'not supplied'}"
    phrases = (
        ("test_removed", "removed", "removed"),
        ("assertion_loosened", "check changed", "checks changed"),
        ("expected_value_changed", "expected value changed", "expected values changed"),
        ("skip_added", "skip added", "skips added"),
    )
    supported = {kind for kind, _, _ in phrases} | {"literal_branch", "stub_left", "parse_error"}
    if any(kind not in supported for kind in counts):
        return "test facts unknown: unsupported finding kind"
    if any(count < 0 for count in counts.values()):
        return "test facts unknown: negative count"
    if counts.get("parse_error", 0):
        return "test facts unknown: parse error"
    parts = [
        f"{counts[kind]} {one if counts[kind] == 1 else many}"
        for kind, one, many in phrases
        if counts.get(kind, 0)
    ]
    return "tests in this change: " + (", ".join(parts) or "0 recorded test changes")


def _discrepancy_line(discrepancy: Discrepancy) -> str:
    match discrepancy.kind:
        case "tests_not_run":
            detail = "said tests were run; no agent run_tests call recorded"
        case "tests_not_passing":
            detail = "said tests pass; last agent run did not pass"
        case "file_not_touched":
            detail = f"named {discrepancy.path}; no touch recorded"
        case "file_not_mentioned":
            detail = f"did not name {discrepancy.path}; touch recorded"
    return f"said versus did: {detail}"


def _run_line(run: TestRunFact) -> str:
    prefix = f"tests run at step {run.step} (event {run.seq}): "
    if run.outcome is None:
        return prefix + "result unknown"
    line = (
        prefix + f"{run.outcome}; {run.passed} passed, {run.failed} failed, {run.skipped} skipped"
    )
    if run.failed_tests:
        line += "; failed ids: " + ", ".join(run.failed_tests)
    if run.failed_tests_truncated:
        line += "; failed ids truncated"
    return line


def facts_lines(
    facts: ReviewFacts,
    discrepancies: Iterable[Discrepancy],
    tests_line: str,
    beforeafter_line: str | None = None,
) -> tuple[str, ...]:
    """Fixed order: size/activity, test runs, detector, discrepancies, before/after.

    beforeafter_line is the caller's _beforeafter.facts_line output, verbatim;
    this core imports neither that module nor the detector. No assessment words.
    """
    count = str(facts.files_changed) if facts.files_changed is not None else "unknown"
    added = str(facts.lines_added) if facts.lines_added is not None else "unknown"
    removed = str(facts.lines_removed) if facts.lines_removed is not None else "unknown"
    cost = f"USD {facts.cost_usd:.6f}" if facts.cost_usd is not None else "unknown"
    size = (
        f"size: {count} files, {len(facts.nodes)} observed nodes, +{added}/-{removed} lines; "
        f"{facts.steps} steps, {len(facts.errors)} error records; "
        f"tool duration {facts.duration_ms} ms; cost {cost}"
    )
    lines = [size]
    lines.extend(_run_line(run) for run in facts.tests)
    if not facts.tests:
        lines.append("tests run by agent: 0 recorded calls")
    lines.append(tests_line)
    lines.extend(_discrepancy_line(item) for item in discrepancies)
    if beforeafter_line is not None:
        lines.append(beforeafter_line)
    return tuple(lines)
