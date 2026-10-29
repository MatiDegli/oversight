"""Observed caller evidence on the retained static cone, never a completeness claim.

Each side uses a fresh detached checkout and a new pytest process with required isolation.
The candidate's selected tests, conftests, files under tests/ and collection configuration
are overlaid (including deletions) on base. Implementation modules stay at their own commit.
The full identical selection runs again when any admitted result differs; nothing invokes
callers outside pytest. Preparation and all executions share twice test_timeout_s.
"""

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from oversight.changes._shortcuts_common import is_test_file
from oversight.changes._shortcuts_unknown import COLLECTION_CONFIG
from oversight.changes._suite_evidence import publish_suite
from oversight.workspace import (
    CallReport,
    CallTarget,
    Invocation,
    IsolationUnavailable,
    SuiteInterrupted,
    SuiteRun,
    SuiteUnavailable,
    base_probe_checkout,
)
from oversight_contracts.events import SuiteExecution
from oversight_contracts.graph import ImpactCone
from oversight_contracts.review import BehaviorDiffRow, ChangeFacts, ChangeTarget, MeasuredCaller

if TYPE_CHECKING:
    from oversight.changes._state import ChangeRecord
    from oversight.changes._support import ChangeContext

MAX_ROWS = 8
MAX_REASONS = 16


@dataclass(frozen=True)
class MeasuredSuite:
    implementation: str
    tree: str
    commit: str | None
    run: SuiteRun
    seq: int | None = None


@dataclass(frozen=True)
class MeasuredEvidence:
    target: ChangeTarget
    impact: ImpactCone
    selection: tuple[str, ...]
    overlay: tuple[str, ...]
    suites: tuple[MeasuredSuite, ...] = ()
    problems: tuple[str, ...] = ()


def _bounded(reasons: list[str]) -> list[str]:
    return list(dict.fromkeys(reason[:240] for reason in reasons))[:MAX_REASONS]


def _reports(evidence: MeasuredEvidence, target: ChangeTarget) -> list[CallReport]:
    if evidence.target != target:
        raise ValueError("caller evidence belongs to another candidate")
    reports: list[CallReport] = []
    for index, suite in enumerate(evidence.suites):
        expected = target.base_commit if index % 2 == 0 else target.head_commit
        if (
            index >= 4
            or suite.implementation != expected
            or suite.run.tested_tree != suite.tree
            or suite.run.tested_commit != suite.commit
            or (
                index % 2 == 1
                and (suite.tree != target.candidate_tree or suite.commit != target.head_commit)
            )
            or (index % 2 == 0 and suite.commit not in (None, target.base_commit))
        ):
            raise ValueError("caller execution differs from captured implementation")
        report = suite.run.measured or CallReport()
        problems = list(report.problems) + list(suite.run.problems)
        cases = tuple(sorted(case.test_id for case in suite.run.cases))
        if cases != evidence.selection:
            problems.append("test selection differs or is incomplete")
        if suite.run.result.outcome in ("error", "timeout"):
            problems.append("suite incomplete")
        reports.append(
            replace(
                report,
                complete=report.complete and not problems,
                problems=tuple(_bounded(problems)),
            )
        )
    return reports


def _admitted(before: Invocation, after: Invocation) -> bool:
    return (
        before.problem is None
        and after.problem is None
        and before.inputs is not None
        and before.inputs == after.inputs
        and before.result is not None
        and after.result is not None
    )


def _matching(
    node_id: str, reports: list[CallReport]
) -> tuple[list[tuple[Invocation, Invocation]], int, int, list[str]]:
    sides = [
        {call.key: call for call in report.calls if call.node_id == node_id}
        for report in reports[:2]
    ]
    before, after = ([*sides, {}, {}])[:2]
    pairs: list[tuple[Invocation, Invocation]] = []
    reasons: list[str] = []
    for key in sorted(before.keys() | after.keys()):
        old, new = before.get(key), after.get(key)
        if old is None or new is None:
            reasons.append("invocations without matching test/caller/ordinal")
        elif not _admitted(old, new):
            reasons.append(old.problem or new.problem or "unequal typed entry inputs")
        else:
            pairs.append((old, new))
    return pairs, len(before) - len(pairs), len(after) - len(pairs), reasons


def _caller(node_id: str, reports: list[CallReport], problems: tuple[str, ...]) -> MeasuredCaller:
    pairs, unmatched_before, unmatched_after, reasons = _matching(node_id, reports)
    diagnostics = list(problems) + [p for report in reports for p in report.problems]
    if len(reports) < 2:
        diagnostics.append("paired caller recording unavailable")
    complete = (
        len(reports) >= 2 and all(r.complete for r in reports) and not diagnostics and not reasons
    )
    if len(reports) > 2:
        original = [{c.key: c for c in r.calls if c.node_id == node_id} for r in reports[:2]]
        repeated = [{c.key: c for c in r.calls if c.node_id == node_id} for r in reports[2:]]
        if len(repeated) != 2 or repeated != original:
            complete = False
            diagnostics.append("unstable or incomplete repeated caller sequence")
    differing = 0
    rows: list[BehaviorDiffRow] = []
    repeat = [{call.key: call for call in report.calls} for report in reports[2:]]
    for before, after in pairs:
        if before.result == after.result:
            continue
        confirmed = len(reports) == 4 and all(r.complete for r in reports)
        unstable = False
        if confirmed:
            old, new = (side.get(before.key) for side in repeat)
            confirmed = old is not None and new is not None and _admitted(old, new)
            if confirmed:
                assert old is not None and new is not None
                unstable = (
                    old.inputs != before.inputs
                    or old.result != before.result
                    or new.result != after.result
                )
                confirmed = not unstable
        if confirmed:
            differing += 1
        else:
            complete = False
            diagnostics.append(
                "unstable caller observations" if unstable else "caller repetition incomplete"
            )
        if len(rows) < MAX_ROWS and (confirmed or unstable):
            assert (
                before.inputs is not None and before.result is not None and after.result is not None
            )
            rows.append(
                BehaviorDiffRow(
                    inputs=before.inputs[:2048],
                    before=before.result[:2048],
                    after=after.result[:2048],
                    unstable=unstable,
                )
            )
    if not pairs:
        complete = False
        reasons.append("no comparable caller invocations observed")
    if not complete:
        reasons.extend(diagnostics or ["caller recording incomplete"])
    outcome = "changed" if differing else "unchanged" if pairs and complete else "not_compared"
    return MeasuredCaller(
        node_id=node_id,
        outcome=outcome,
        compared_count=len(pairs),
        differing_count=differing,
        rows=rows,
        unmatched_before=unmatched_before,
        unmatched_after=unmatched_after,
        complete=complete,
        not_compared=_bounded(reasons),
        problems=_bounded(diagnostics),
    )


def project_measured(evidence: MeasuredEvidence, target: ChangeTarget) -> list[MeasuredCaller]:
    """Pure re-projection used both to publish and to reject manipulated public evidence."""
    reports = _reports(evidence, target)
    return [_caller(entry.node_id, reports, evidence.problems) for entry in evidence.impact.entries]


def validate_measured(
    facts: ChangeFacts,
    measured: MeasuredEvidence | None,
    executions: Mapping[int, SuiteExecution],
    runs: Mapping[int, SuiteRun],
    impact: ImpactCone | None,
) -> None:
    target = ChangeTarget.model_validate(facts.model_dump(include=set(ChangeTarget.model_fields)))
    if measured is not None and measured.impact != impact:
        raise ValueError("caller evidence differs from retained static cone")
    expected = project_measured(measured, target) if measured is not None else None
    if facts.measured_cone != expected:
        raise ValueError("facts caller measurements differ from captured evidence")
    for index, suite in enumerate(measured.suites if measured is not None else ()):
        if suite.seq is None:
            raise ValueError("caller measurement lacks its execution seq")
        execution = executions.get(suite.seq)
        if (
            execution is None
            or runs.get(suite.seq) != suite.run
            or execution.revision != facts.revision
            or execution.phase != ("base_probe" if index % 2 == 0 else "head_probe")
        ):
            raise ValueError("caller measurement lacks its captured execution")


def unavailable_measured(target: ChangeTarget, impact: ImpactCone, reason: str) -> MeasuredEvidence:
    return MeasuredEvidence(target, impact, (), (), problems=(reason,))


async def _overlay(
    ctx: "ChangeContext",
    rec: "ChangeRecord",
    target: ChangeTarget,
    deadline: float,
) -> tuple[str, ...]:
    """Use committed paths, including binary support that is absent from the source maps."""
    paths: set[str] = set()
    for commit in (target.base_commit, target.head_commit):
        names = await ctx.workspace.git_ok(
            rec.tree, "ls-tree", "-r", "--name-only", "-z", commit, timeout_s=_remaining(deadline)
        )
        paths.update(names.rstrip("\0").split("\0"))
    return tuple(
        sorted(
            path
            for path in paths
            if is_test_file(path)
            or "tests" in PurePosixPath(path).parts[:-1]
            or PurePosixPath(path).name in COLLECTION_CONFIG
        )
    )


def _targets(rec: "ChangeRecord", impact: ImpactCone, *, base: bool) -> tuple[CallTarget, ...]:
    retained = {entry.node_id for entry in impact.entries}
    return tuple(
        CallTarget(n.id, n.path, n.qualname, n.start_line, n.end_line)
        for n in (rec.base.graph if base else rec.head.graph).nodes
        if n.id in retained and n.kind in ("function", "method")
    )


def _remaining(deadline: float) -> float:
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise TimeoutError
    return seconds


async def _execute(
    ctx: "ChangeContext",
    rec: "ChangeRecord",
    evidence: MeasuredEvidence,
    side: int,
    deadline: float,
    suites: list[MeasuredSuite],
    timer: asyncio.Timeout,
) -> None:
    commit = evidence.target.base_commit if side == 0 else evidence.target.head_commit
    checkout = base_probe_checkout(
        ctx.workspace,
        agent=rec.tree,
        base=commit,
        head=evidence.target.head_commit,
        paths=evidence.overlay if side == 0 else (),
        timeout_s=_remaining(deadline),
    )
    try:
        tree = await checkout.__aenter__()
        expected = (
            await ctx.workspace.git_ok(tree, "write-tree", timeout_s=_remaining(deadline))
        ).strip()
        committed = (
            await ctx.workspace.git_ok(
                tree, "rev-parse", "HEAD^{tree}", timeout_s=_remaining(deadline)
            )
        ).strip()
        tested_commit = commit if expected == committed else None
        try:
            run = await ctx.workspace.run_tests(
                tree,
                timeout_s=_remaining(deadline),
                case_ids=evidence.selection,
                isolation="required",
                measured_targets=_targets(rec, evidence.impact, base=side == 0),
            )
        except (SuiteInterrupted, SuiteUnavailable) as error:
            run = error.run
            if run.tested_tree is not None:
                await _retain(ctx, rec, evidence, side, expected, tested_commit, run, suites)
            if isinstance(error, SuiteInterrupted) or run.tested_tree is None:
                raise
            return
        await _retain(ctx, rec, evidence, side, expected, tested_commit, run, suites)
    finally:
        # Cleanup is outside the shared computation timeout, in the lock-owning task.
        if not timer.expired():
            timer.reschedule(None)
        await checkout.__aexit__(None, None, None)
        if not timer.expired():
            timer.reschedule(deadline)


async def _retain(
    ctx: "ChangeContext",
    rec: "ChangeRecord",
    evidence: MeasuredEvidence,
    side: int,
    tree: str,
    commit: str | None,
    run: SuiteRun,
    suites: list[MeasuredSuite],
) -> None:
    execution = await publish_suite(
        ctx,
        rec,
        run,
        phase="base_probe" if side == 0 else "head_probe",
        revision=evidence.target.revision,
    )
    seq = next(seq for seq, item in rec.executions.items() if item is execution)
    suites.append(
        MeasuredSuite(
            evidence.target.base_commit if side == 0 else evidence.target.head_commit,
            tree,
            commit,
            run,
            seq,
        )
    )


async def measure_cone(
    ctx: "ChangeContext",
    rec: "ChangeRecord",
    *,
    target: ChangeTarget,
    impact: ImpactCone,
) -> MeasuredEvidence:
    deadline = time.monotonic() + 2 * rec.task.limits.test_timeout_s
    suites: list[MeasuredSuite] = []
    evidence = MeasuredEvidence(target, impact, (), ())
    try:
        async with asyncio.timeout_at(deadline) as timer:
            proposals = [
                run
                for seq, run in rec.suite_runs.items()
                if rec.executions[seq].phase == "proposal"
                and rec.executions[seq].revision == target.revision
            ]
            if len(proposals) != 1 or proposals[0].problems or not proposals[0].cases:
                return replace(evidence, problems=("proposal test selection unavailable",))
            selection = tuple(sorted(case.test_id for case in proposals[0].cases))
            if len(set(selection)) != len(selection):
                return replace(evidence, problems=("ambiguous proposal test selection",))
            evidence = replace(
                evidence, selection=selection, overlay=await _overlay(ctx, rec, target, deadline)
            )
            if not ctx.workspace.isolation.available:
                raise IsolationUnavailable("isolation unavailable")
            for side in (0, 1):
                await _execute(ctx, rec, evidence, side, deadline, suites, timer)
            initial = replace(evidence, suites=tuple(suites))
            reports = _reports(initial, target)
            if any(
                old.result != new.result
                for entry in impact.entries
                for old, new in _matching(entry.node_id, reports)[0]
            ):
                for side in (0, 1):
                    await _execute(ctx, rec, evidence, side, deadline, suites, timer)
    except asyncio.CancelledError:
        # The proposal catches Stop, publishes this private partial evidence, then propagates it.
        rec.measured_cones[target.revision] = replace(
            evidence, suites=tuple(suites), problems=("caller measurement interrupted",)
        )
        raise
    except Exception as error:
        isolation_failed = isinstance(error, IsolationUnavailable) or (
            isinstance(error, SuiteUnavailable) and isinstance(error.cause, IsolationUnavailable)
        )
        reason = (
            "caller measurement budget exhausted"
            if isinstance(error, TimeoutError)
            else "isolation unavailable"
            if isolation_failed
            else f"caller measurement unavailable ({type(error).__name__})"
        )
        evidence = replace(evidence, problems=(reason,))
    return replace(evidence, suites=tuple(suites))
