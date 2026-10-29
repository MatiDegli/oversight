"""Candidate facts: captured source observations, never a merge verdict."""

import asyncio
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from functools import partial
from pathlib import PurePosixPath

from oversight.changes._behavior_publish import BehaviorEvidence, project_behavior
from oversight.changes._measured import MeasuredEvidence, project_measured, validate_measured
from oversight.changes._probes import added_cases, comparison
from oversight.changes._run_findings import RunEvidence, project_run_findings
from oversight.changes._shortcut_detector import (
    DetectionResult,
    Finding,
    ShortcutKind,
    detect_shortcuts,
)
from oversight.changes._shortcuts_common import FuncNode, Source, is_test_file
from oversight.changes._shortcuts_skips import DECORATOR_SKIP, module_markers
from oversight.changes._shortcuts_unknown import COLLECTION_CONFIG
from oversight.changes._state import ChangeRecord
from oversight.changes._summary import SummaryEvidence, build_summary
from oversight.changes._summary_capture import capture_summary
from oversight.changes._support import ChangeContext
from oversight.workspace import SuiteRun, validate_suite_execution
from oversight_contracts.events import SuiteExecution
from oversight_contracts.graph import ImpactCone
from oversight_contracts.policy import EvidenceRef
from oversight_contracts.review import (
    AddedTestProbe,
    ChangeFacts,
    ChangeTarget,
    FinishDeclaration,
    IntegrityFact,
)

MISSING_PRODUCERS = (
    "zone producer unavailable",
    "reach producer unavailable",
)


@dataclass(frozen=True, kw_only=True)
class IntegrityStatus:
    """Source producer status, independent of unrelated ChangeFacts producers."""

    complete: bool
    prohibited_paths: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class AddedTestsInventory:
    """Exact added case ids; only a complete empty inventory certifies no additions."""

    test_ids: tuple[str, ...]
    complete: bool


@dataclass(frozen=True)
class SourceObservation:
    changes: tuple[tuple[str, str | None, str | None], ...]
    detected: DetectionResult
    captured: bool


def observe_source(
    base_files: Mapping[str, str], head_files: Mapping[str, str], *, captured: bool = False
) -> SourceObservation:
    """One changed-file input and detector run for both source facts and status."""
    changes = tuple(
        (path, base_files.get(path), head_files.get(path))
        for path in sorted(base_files.keys() | head_files.keys())
        if base_files.get(path) != head_files.get(path)
    )
    return SourceObservation(
        changes,
        detect_shortcuts(changes, grouped=False),
        captured or bool(base_files or head_files),
    )


def integrity_status(
    base_files: Mapping[str, str],
    head_files: Mapping[str, str],
    *,
    observation: SourceObservation | None = None,
    captured: bool = False,
) -> IntegrityStatus:
    """Empty maps require an explicit successful capture witness."""
    source = observation or observe_source(base_files, head_files, captured=captured)
    prohibited = tuple(
        path
        for path, old, _ in source.changes
        if PurePosixPath(path).name in COLLECTION_CONFIG
        or PurePosixPath(path).name == "conftest.py"
        or (old is not None and is_test_file(path))
    )
    return IntegrityStatus(
        complete=source.captured and source.detected.status != "unknown",
        prohibited_paths=prohibited,
    )


def added_tests_inventory(rec: ChangeRecord, revision: int) -> AddedTestsInventory:
    """Preserve the inventory producer's own diagnostics and bounded-report status."""
    ids, problems = added_cases(rec, revision)
    current = [
        execution
        for execution in rec.executions.values()
        if execution.phase == "proposal" and execution.revision == revision
    ]
    return AddedTestsInventory(
        test_ids=ids,
        complete=not problems
        and bool(current)
        and all(execution.suite.cases_complete is True for execution in current),
    )


@dataclass(frozen=True)
class FactsEvidence:
    agent_id: str
    target: ChangeTarget
    declared: FinishDeclaration | None
    edited_paths: tuple[str, ...]
    integrity: tuple[IntegrityFact, ...]
    problems: tuple[str, ...]
    executions: Mapping[int, SuiteExecution]
    runs: Mapping[int, SuiteRun]
    added_tests: tuple[AddedTestProbe, ...] = ()
    integrity_status: IntegrityStatus | None = None
    summary_evidence: SummaryEvidence = field(default_factory=SummaryEvidence)
    behavior_evidence: BehaviorEvidence | None = None
    measured: MeasuredEvidence | None = None
    impact: ImpactCone | None = None
    run_evidence: RunEvidence | None = None


def validate_change_facts(facts: ChangeFacts, evidence: FactsEvidence) -> None:
    """Check the exact captured candidate and its individual execution/source evidence.

    Zones and reach have no producer here: their empty values cannot
    certify completeness. A list of execution seqs alone establishes no success.
    """
    if (
        ChangeTarget.model_validate(facts.model_dump(include=set(ChangeTarget.model_fields)))
        != evidence.target
    ):
        raise ValueError("facts target differs from captured candidate")
    if facts.declared != evidence.declared or facts.edited_paths != list(evidence.edited_paths):
        raise ValueError("facts declaration or edits differ from captured evidence")
    if facts.test_runs != sorted(evidence.executions) or set(evidence.runs) != set(
        evidence.executions
    ):
        raise ValueError("facts executions differ from captured evidence")
    for seq, execution in evidence.executions.items():
        if execution.revision is not None and execution.revision > facts.revision:
            raise ValueError("execution belongs to a future revision")
        validate_suite_execution(
            execution,
            evidence.runs[seq],
            change_id=facts.change_id,
            agent_id=evidence.agent_id,
            revision=execution.revision,
            phase=execution.phase,
        )
        # The candidate's verification is tied to its committed tree, not just a seq.
        if (
            execution.phase == "proposal"
            and execution.revision == facts.revision
            and (
                execution.tested_commit != facts.head_commit
                or execution.tested_tree != facts.candidate_tree
            )
        ):
            raise ValueError("proposal execution tested another candidate")
    if facts.summary != build_summary(
        evidence.summary_evidence, evidence.executions, revision=facts.revision
    ):
        raise ValueError("facts summary differs from captured evidence")
    if facts.behavior_diff != project_behavior(evidence.behavior_evidence, revision=facts.revision):
        raise ValueError("facts behavior differs from captured evidence")
    validate_measured(facts, evidence.measured, evidence.executions, evidence.runs, evidence.impact)
    integrity, findings, diagnostics = project_run_findings(
        evidence.run_evidence,
        evidence.integrity,
        evidence.executions,
        evidence.target,
        evidence.agent_id,
    )
    if facts.run_findings != findings:
        raise ValueError("facts run findings differ from captured witnesses")
    if not set(diagnostics).issubset(facts.problems):
        raise ValueError("facts omit run rule uncertainty")
    if facts.integrity != integrity:
        raise ValueError("facts integrity differs from captured source observations")
    if facts.added_tests != list(evidence.added_tests):
        raise ValueError("facts probes differ from captured evidence")
    for probe in facts.added_tests:
        before = evidence.executions.get(probe.before_seq)
        after = evidence.executions.get(probe.after_seq)
        if (
            before is None
            or after is None
            or (
                before.phase != "base_probe"
                or after.phase != "head_probe"
                or before.revision != facts.revision
                or after.revision != facts.revision
                or after.tested_commit != facts.head_commit
                or after.tested_tree != facts.candidate_tree
            )
        ):
            raise ValueError("probe execution differs from candidate")
        if comparison((probe.test_id,), before, after)[0][1] != probe.result:
            raise ValueError("probe result differs from captured evidence")
    if facts.zone is not None or facts.reach is not None:
        raise ValueError("unbuilt producers cannot supply facts")
    if facts.complete or facts.problems != list(evidence.problems):
        raise ValueError("facts completeness differs from captured evidence")


def _identities(
    finding: Finding,
    files: Mapping[str, str],
    runs: Mapping[int, SuiteRun],
) -> tuple[list[str | None], str | None]:
    """Resolve removals in base and modifications in head; never invent parameter ids."""
    try:
        source = Source(files[finding.path])
    except (KeyError, SyntaxError, ValueError, RecursionError):
        return [None], f"integrity identity unresolved: {finding.path}:{finding.line}"
    enclosing = [
        (qual, node)
        for qual, node in source.tests.items()
        if min([node.lineno, *[d.lineno for d in node.decorator_list]])
        <= finding.line
        <= (node.end_lineno or node.lineno)
    ]
    if not enclosing and finding.kind == ShortcutKind.SKIP_ADDED:
        # A class decorator affects its collected methods; a module marker affects
        # its tests. Keep one observation per test rather than collapsing the file.
        classes = [
            qual
            for qual, node in source.classes.items()
            if any(
                d.lineno == finding.line and source.resolve(d) in DECORATOR_SKIP
                for d in node.decorator_list
            )
        ]
        if len(classes) == 1:
            enclosing = [
                (qual, node)
                for qual, node in source.tests.items()
                if qual.startswith(classes[0] + ".")
            ]
        elif any(line == finding.line for _, line in module_markers(source)):
            enclosing = list(source.tests.items())
    if not enclosing:
        return [None], f"integrity identity ambiguous: {finding.path}:{finding.line}"
    ids: list[str | None] = []
    problems: list[str] = []
    for qual, node in enclosing:
        resolved, problem = _test_ids(finding.path, source, qual, node, runs)
        ids.extend(resolved)
        if problem:
            problems.append(problem)
    return ids, "; ".join(problems) or None


def _test_ids(
    path: str,
    source: Source,
    qual: str,
    node: FuncNode,
    runs: Mapping[int, SuiteRun],
) -> tuple[list[str | None], str | None]:
    prefix = "::".join([path, *qual.split(".")])
    if "#" in qual:
        return [None], f"integrity duplicate definition: {prefix}"
    # Collection/decorator semantics can change ids. Only reports from this exact
    # source version verify parametrizations, never a report from the other tree.
    parametrized = any(source.resolve(d) == "pytest.mark.parametrize" for d in node.decorator_list)
    if parametrized:
        ids = sorted(
            {
                case.test_id
                for run in runs.values()
                for case in run.cases
                if case.test_id.startswith(prefix + "[") and not run.problems
            }
        )
        if ids:
            return [str(test_id) for test_id in ids], None
        return [None], f"integrity parameter ids unavailable: {prefix}"
    return [prefix], None


def source_facts(
    rec: ChangeRecord,
    *,
    base_tree: str,
    head_tree: str,
    observation: SourceObservation | None = None,
) -> tuple[tuple[IntegrityFact, ...], tuple[str, ...]]:
    source = observation or observe_source(rec.base_files, rec.head_files)
    detected = source.detected
    problems = [f"detector unknown: {reason}" for reason in detected.reasons]
    facts: list[IntegrityFact] = []
    for finding in detected:
        if finding.kind == ShortcutKind.UNKNOWN:
            continue  # No contract kind for unknown: preserve it in problems, never certify it.
        removed = finding.kind == ShortcutKind.TEST_REMOVED
        tree = base_tree if removed else head_tree
        test_ids: list[str | None] = [None]
        if finding.kind in {
            ShortcutKind.TEST_REMOVED,
            ShortcutKind.ASSERTION_LOOSENED,
            ShortcutKind.SKIP_ADDED,
            ShortcutKind.EXPECTED_VALUE_CHANGED,
        }:
            runs = {seq: run for seq, run in rec.suite_runs.items() if run.tested_tree == tree}
            test_ids, problem = _identities(
                finding, rec.base_files if removed else rec.head_files, runs
            )
            if problem:
                problems.append(problem)
        for test_id in test_ids:
            facts.append(
                IntegrityFact(
                    kind=finding.kind.value,
                    path=finding.path,
                    line=finding.line,
                    text=finding.message,
                    test_id=test_id,
                    node_id=None,
                    evidence=[EvidenceRef(git_object=tree, path=finding.path)],
                )
            )
    return tuple(facts), tuple(problems)


async def capture_facts(
    ctx: ChangeContext, rec: ChangeRecord, *, head: str, revision: int
) -> FactsEvidence:
    """Capture under main and agent locks, before the proposal publication task."""
    tree = (await ctx.git(rec.tree, "rev-parse", f"{head}^{{tree}}")).strip()
    base_tree = (await ctx.git(rec.tree, "rev-parse", f"{rec.base_commit}^{{tree}}")).strip()
    main = (await ctx.git(ctx.workspace.main, "rev-parse", "HEAD")).strip()
    target = ChangeTarget(
        change_id=rec.change_id,
        revision=revision,
        base_commit=rec.base_commit,
        head_commit=head,
        main_commit=main,
        candidate_tree=tree,
    )
    integrity, diagnostics, status = await ctx.whole(_source_facts(ctx, rec, base_tree, tree))
    run_evidence = RunEvidence(tuple(rec.run_edits), tuple(rec.run_suites))
    executions, runs = deepcopy(rec.executions), deepcopy(rec.suite_runs)
    _, _, run_problems = await ctx.whole(
        asyncio.to_thread(
            project_run_findings, run_evidence, integrity, executions, target, rec.agent_id
        )
    )
    problems = list(MISSING_PRODUCERS) + list(diagnostics) + list(run_problems)
    for seq, execution in sorted(rec.executions.items()):
        run = rec.suite_runs[seq]
        problems.extend(f"suite {seq}: {problem}" for problem in run.problems)
        if execution.suite.cases_complete is not True:
            problems.append(f"suite {seq}: incomplete retained cases")
    if rec.declared is None:
        problems.append("finish declaration unavailable")
    if not any(e.phase == "proposal" and e.revision == revision for e in rec.executions.values()):
        problems.append("proposal suite evidence unavailable")
    edited = tuple(
        sorted(
            rec.observed_paths
            | rec.touched
            | {
                p
                for p in rec.base_files.keys() | rec.head_files.keys()
                if rec.base_files.get(p) != rec.head_files.get(p)
            }
        )
    )
    inventory = await asyncio.to_thread(added_tests_inventory, rec, revision)
    return FactsEvidence(
        rec.agent_id,
        target,
        rec.declared,
        edited,
        integrity,
        tuple(problems),
        executions,
        runs,
        run_evidence=run_evidence,
        integrity_status=status,
        summary_evidence=capture_summary(
            rec, added_test_ids=inventory.test_ids if inventory.complete else None
        ),
    )


def build_change_facts(evidence: FactsEvidence) -> ChangeFacts:
    """Construct and validate after change.proposed, with no I/O or mutable reads."""
    integrity, findings, _ = project_run_findings(
        evidence.run_evidence,
        evidence.integrity,
        evidence.executions,
        evidence.target,
        evidence.agent_id,
    )
    facts = ChangeFacts(
        **evidence.target.model_dump(),
        summary=build_summary(
            evidence.summary_evidence, evidence.executions, revision=evidence.target.revision
        ),
        behavior_diff=project_behavior(
            evidence.behavior_evidence, revision=evidence.target.revision
        ),
        declared=evidence.declared,
        edited_paths=list(evidence.edited_paths),
        test_runs=sorted(evidence.executions),
        integrity=integrity,
        added_tests=list(evidence.added_tests),
        run_findings=findings,
        zone=None,
        reach=None,
        complete=False,
        problems=list(evidence.problems),
        measured_cone=project_measured(evidence.measured, evidence.target)
        if evidence.measured
        else None,
    )
    validate_change_facts(facts, evidence)
    return facts


async def _source_facts(
    ctx: ChangeContext, rec: ChangeRecord, base: str, head: str
) -> tuple[tuple[IntegrityFact, ...], tuple[str, ...], IntegrityStatus]:
    observation = await asyncio.to_thread(observe_source, rec.base_files, rec.head_files)
    facts, problems = await asyncio.to_thread(
        partial(source_facts, rec, base_tree=base, head_tree=head, observation=observation)
    )
    return (
        facts,
        problems,
        integrity_status(rec.base_files, rec.head_files, observation=observation),
    )
