"""Keep full reports internally and publish a deterministic bounded projection."""

import asyncio
from dataclasses import replace
from typing import Literal

from oversight.changes._shortcuts_common import Source
from oversight.changes._state import ChangeRecord
from oversight.changes._support import ChangeContext
from oversight.workspace import SuiteRun, validate_suite_execution
from oversight_contracts.events import CaseResult, SuiteExecution


def retain_cases(
    full_cases: tuple[CaseResult, ...],
    *,
    added_test_ids: frozenset[str],
    limit: Literal[200] = 200,
) -> tuple[list[CaseResult], bool]:
    """Added ids, remaining failures/errors, then other ids, sorted within each group."""
    if limit != 200:
        raise ValueError("the contract retains at most 200 cases")
    unique: dict[str, CaseResult] = {}
    ambiguous = False
    for case in full_cases:
        if case.test_id in unique:
            ambiguous = True
        unique[case.test_id] = case
    ordered = sorted(
        unique.values(),
        key=lambda c: (
            0 if c.test_id in added_test_ids else 1 if c.outcome in {"failed", "error"} else 2,
            c.test_id,
        ),
    )
    return ordered[:limit], not ambiguous and len(ordered) <= limit


def added_ids(rec: ChangeRecord, run: SuiteRun) -> frozenset[str]:
    """Prioritize observed ids of added definitions, and new ids against a known base report.

    Without base execution evidence, changed parametrizations do not establish added ids.
    This is retention only, never an added-test probe or success evidence.
    """
    changed: set[str] = set()
    for path in rec.base_files.keys() | rec.head_files.keys():
        old, new = rec.base_files.get(path), rec.head_files.get(path)
        if old == new or new is None or not path.endswith(".py"):
            continue
        try:
            before = Source(old).tests if old is not None else {}
            after = Source(new).tests
        except (SyntaxError, ValueError, RecursionError):
            continue
        changed.update("::".join([path, *qual.split(".")]) for qual in after if qual not in before)
    baseline = [
        prior
        for prior in rec.suite_runs.values()
        if prior.tested_commit == rec.base_commit and not prior.problems
    ]
    known = {case.test_id for prior in baseline for case in prior.cases}
    return frozenset(
        case.test_id
        for case in run.cases
        if case.test_id.split("[", 1)[0] in changed
        or (bool(baseline) and case.test_id not in known)
    )


async def publish_suite(
    ctx: ChangeContext,
    rec: ChangeRecord,
    run: SuiteRun,
    *,
    phase: Literal["agent", "proposal", "combined", "base_probe", "head_probe"],
    revision: int | None,
) -> SuiteExecution:
    # Keep the caller's locks and record the run even if cancellation arrives during parsing.
    return await ctx.whole(_publish_suite(ctx, rec, run, phase=phase, revision=revision))


async def _publish_suite(
    ctx: ChangeContext,
    rec: ChangeRecord,
    run: SuiteRun,
    *,
    phase: Literal["agent", "proposal", "combined", "base_probe", "head_probe"],
    revision: int | None,
) -> SuiteExecution:
    retention_ids = await asyncio.to_thread(added_ids, rec, run)
    cases, retained = retain_cases(run.cases, added_test_ids=retention_ids)
    suite = run.result.model_copy(
        update={
            "cases": cases,
            "cases_complete": retained and not run.problems,
        }
    )
    if run.tested_tree is None:
        raise ValueError("suite has no captured tree")
    execution = SuiteExecution(
        change_id=rec.change_id,
        agent_id=rec.agent_id,
        revision=revision,
        phase=phase,
        tested_commit=run.tested_commit,
        tested_tree=run.tested_tree,
        suite=suite,
    )
    validate_suite_execution(
        execution,
        run,
        change_id=rec.change_id,
        agent_id=rec.agent_id,
        revision=revision,
        phase=phase,
    )
    await _record(ctx, rec, execution, run)
    return execution


async def _record(
    ctx: ChangeContext,
    rec: ChangeRecord,
    execution: SuiteExecution,
    run: SuiteRun,
) -> None:
    event = await ctx.publish(execution)
    seq = getattr(event, "seq", None)
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
        raise ValueError("suite publisher did not return its stored seq")
    rec.executions[seq] = execution
    rec.suite_runs[seq] = replace(run)
