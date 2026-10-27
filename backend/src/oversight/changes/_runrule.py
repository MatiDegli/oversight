"""Pure run evidence: failure, subsequent test weakening, then absence of failure.

Absence means "no longer fails", including skipped/deleted tests, not proof of
execution. Callers supply per-test diff facts; this module does not detect them.
Event seq orders the trace. Tool step numbers are labels, not ordering keys.
"""

from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Literal

from oversight_contracts.events import ServerEvent

type WeakeningKind = Literal["removed", "loosened", "skipped", "deleted", "unknown"]
type RunRuleStatus = Literal["findings", "none", "unknown"]


@dataclass(frozen=True)
class TestRun:
    """Complete only when the run finished and every failure id was recorded."""

    seq: int
    step: int
    failed_ids: tuple[str, ...]
    failed_count: int
    complete: bool


@dataclass(frozen=True)
class Edit:
    """One edit event; step is absent unless a following edit_file step names it.

    change.updated and agent.focus can describe the same edit. Both are retained
    in event order; evaluation reports only the first witness per test id.
    """

    seq: int
    path: str
    step: int | None = None


type TraceEntry = TestRun | Edit


@dataclass(frozen=True)
class RunTrace:
    agent_id: str
    entries: tuple[TraceEntry, ...]


@dataclass(frozen=True, kw_only=True)
class Weakening:
    """A pytest node id, or path plus qualified test name (TestClass::test_name).

    A function id covers its parameters; a parametrized id covers only itself.
    Known facts need an identity. Unknown detector facts may be path-only or
    unidentified: neither can establish a finding, and both make status unknown.
    """

    kind: WeakeningKind
    test_id: str | None = None
    path: str | None = None
    test_name: str | None = None

    def __post_init__(self) -> None:
        if self.test_id is not None:
            if self.path is not None or self.test_name is not None or "::" not in self.test_id:
                raise ValueError("use a qualified test_id or path plus test_name")
        elif self.kind != "unknown" and (not self.path or not self.test_name):
            raise ValueError("known weakenings require a per-test identity")
        elif self.test_name is not None and not self.path:
            raise ValueError("test_name requires path")

    @property
    def target_id(self) -> str | None:
        if self.test_id is not None:
            return self.test_id
        if self.path and self.test_name:
            return f"{self.path}::{self.test_name}"
        return None


@dataclass(frozen=True)
class RunRuleFinding:
    test_id: str
    kind: WeakeningKind
    failing_run: TestRun
    edit: Edit
    later_run: TestRun


@dataclass(frozen=True)
class RunRuleResult:
    """Unknown takes precedence, retaining any independently proven findings."""

    status: RunRuleStatus
    findings: tuple[RunRuleFinding, ...] = ()


def trace_from_events(agent_id: str, events: Iterable[ServerEvent]) -> RunTrace:
    """Copy structured evidence, ignoring summaries, reads and human edits.

    Sort by seq so replay batches and live events yield the same trace. Edit
    events precede their tool step; only the next own step can label them.
    """
    entries: list[TraceEntry] = []
    pending: list[int] = []
    for event in sorted(events, key=lambda event: event.seq):
        if event.type == "agent.step" and event.payload.agent_id == agent_id:
            step = event.payload
            if step.tool == "edit_file":
                for index in pending:
                    edit = entries[index]
                    if isinstance(edit, Edit):
                        entries[index] = replace(edit, step=step.step)
            pending.clear()
            if step.tool == "run_tests" and step.tests is not None:
                tests = step.tests
                ids = tuple(tests.failed_tests)
                entries.append(
                    TestRun(
                        event.seq,
                        step.step,
                        ids,
                        tests.failed,
                        step.result == "ok"
                        and tests.outcome in {"passed", "failed"}
                        and tests.failed == len(ids) == len(set(ids)),
                    )
                )
        elif event.type == "agent.focus" and event.payload.agent_id == agent_id:
            if event.payload.mode == "edit":
                for focus in event.payload.ranges:
                    pending.append(len(entries))
                    entries.append(Edit(event.seq, focus.path))
        elif event.type == "change.updated" and event.payload.agent_id == agent_id:
            if event.payload.author == "agent":
                pending.append(len(entries))
                entries.append(Edit(event.seq, event.payload.file.path))
    return RunTrace(agent_id, tuple(entries))


def _matches(target: str, test_id: str) -> bool:
    return target == test_id or (
        "[" not in target and test_id.startswith(target + "[") and test_id.endswith("]")
    )


def _witness(
    failed: TestRun, edit: Edit, runs: tuple[TestRun, ...], test_id: str, kind: WeakeningKind
) -> tuple[RunRuleFinding | None, bool]:
    """A listed failure remains known even in a truncated run."""
    unknown = False
    later = tuple(run for run in runs if run.seq > edit.seq)
    for run in later:
        if test_id in run.failed_ids:
            unknown = False
            continue
        if run.complete:
            return RunRuleFinding(test_id, kind, failed, edit, run), False
        unknown = True
    return None, unknown or not later


def evaluate(trace: RunTrace, weakenings: Iterable[Weakening]) -> RunRuleResult:
    """Find the first witnessed chain for each concrete failed pytest id.

    A missing later run or truncated/unfinished runs can leave a comparison
    unknown. A later complete run resolves earlier uncertainty about absence.
    A known failure after the edit needs no complete list to remain a failure.
    A proven chain resolves other edit comparisons for that concrete test;
    detector uncertainty and uncertainty about other tests remain independent.
    Diff facts identify tests, not individual edit revisions: the edit witness
    proves only that the agent edited that test's file between the two runs.
    """
    runs = tuple(entry for entry in trace.entries if isinstance(entry, TestRun))
    edits = tuple(entry for entry in trace.entries if isinstance(entry, Edit))
    findings: dict[str, RunRuleFinding] = {}
    uncertain_ids: set[str] = set()
    unknown = False
    facts = sorted(set(weakenings), key=lambda fact: (fact.target_id or "", fact.kind))
    for fact in facts:
        if fact.kind == "unknown":
            unknown = True
            continue
        target = fact.target_id
        assert target is not None  # guaranteed by Weakening's per-test identity
        path = target.split("::", 1)[0]
        test_ids = tuple(
            dict.fromkeys(
                test_id for run in runs for test_id in run.failed_ids if _matches(target, test_id)
            )
        ) or (target,)
        for edit in edits:
            if edit.path != path:
                continue
            before = tuple(run for run in runs if run.seq < edit.seq)
            # An omitted failure before the edit cannot establish or disprove a chain.
            uncertain_before = any(not run.complete for run in before)
            for test_id in test_ids:
                failed_runs = tuple(run for run in before if test_id in run.failed_ids)
                if not failed_runs and uncertain_before:
                    uncertain_ids.add(test_id)
                for failed in failed_runs:
                    finding, uncertain = _witness(failed, edit, runs, test_id, fact.kind)
                    if uncertain:
                        uncertain_ids.add(test_id)
                    if finding is not None:
                        findings.setdefault(test_id, finding)
    unknown |= bool(uncertain_ids.difference(findings))
    status: RunRuleStatus = "unknown" if unknown else "findings" if findings else "none"
    return RunRuleResult(status, tuple(findings.values()))


def run_rule_line(result: RunRuleResult) -> str:
    """One neutral line; passing here means absent from a complete failure list."""
    parts: list[str] = []
    for finding in result.findings:
        edit = str(finding.edit.step) if finding.edit.step is not None else "unknown"
        name = " ".join(finding.test_id.split())
        parts.append(
            f"1 test failed, was {finding.kind}, then no longer failed: {name} "
            f"(steps {finding.failing_run.step}, {edit}, {finding.later_run.step}; "
            f"edit event {finding.edit.seq})"
        )
    if result.status == "unknown":
        parts.append("Run comparison unknown")
    return "; ".join(parts) if parts else "No test failure-weakening sequence found"
