"""Server-owned added-case probes; never agent tools or model-visible results."""

import ast
import asyncio
from dataclasses import replace

from oversight.changes._beforeafter import FactKind, RunProblem, TestRun, affected_tests
from oversight.changes._shortcuts_common import Source, is_test_file
from oversight.changes._state import ChangeRecord
from oversight.changes._suite_evidence import added_ids, publish_suite
from oversight.changes._support import ChangeContext
from oversight.workspace import (
    IsolationUnavailable,
    SuiteInterrupted,
    SuiteRun,
    SuiteUnavailable,
    Tree,
    WorkspaceError,
    base_probe_checkout,
)
from oversight_contracts.events import SuiteExecution
from oversight_contracts.review import AddedTestProbe


def added_cases(rec: ChangeRecord, revision: int) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """New definitions use exact proposal ids, including pytest-generated parameter ids.

    Static ids also cover an added plain test that collection omitted. Changes to
    parameter definitions require a clean report from the original base; without
    it their identity is ambiguous and is reported, never guessed. Fixture hooks
    may affect other files, so unresolved parameter sources suppress all probes.
    """
    current = [
        run
        for seq, run in rec.suite_runs.items()
        if rec.executions[seq].phase == "proposal" and rec.executions[seq].revision == revision
    ]
    baseline = [
        run
        for seq, run in rec.suite_runs.items()
        if rec.executions[seq].phase == "agent"
        and run.tested_commit == rec.base_commit
        and not run.problems
    ]
    known = {c.test_id for run in baseline for c in run.cases}
    ids = {test_id for run in current for test_id in added_ids(rec, run)}
    problems: list[str] = []
    ambiguous_parameters = False
    for path, text in sorted(rec.head_files.items()):
        if not is_test_file(path) or text == rec.base_files.get(path):
            continue
        try:
            after = Source(text)
            before = Source(rec.base_files.get(path, ""))
        except (SyntaxError, ValueError, RecursionError):
            problems.append(f"added tests: cannot resolve definitions in {path}")
            continue
        if not baseline and _parameter_sources_changed(before, after):
            problems.append(f"added tests: base parameter ids unavailable for {path}")
            ambiguous_parameters = True
        if any("#" in qual for qual in after.tests):
            problems.append(f"added tests: duplicate definitions in {path}")
            ids.difference_update(
                test_id for test_id in tuple(ids) if test_id.startswith(path + "::")
            )
            continue
        for qual, node in after.tests.items():
            prefix = "::".join([path, *qual.split(".")])
            old = before.tests.get(qual)
            if old is not None and ast.dump(old) == ast.dump(node):
                continue
            param = any(after.resolve(d) == "pytest.mark.parametrize" for d in node.decorator_list)
            observed = {
                c.test_id
                for run in current
                for c in run.cases
                if c.test_id == prefix or c.test_id.startswith(prefix + "[")
            }
            if old is None:
                if param and (not observed or any(run.problems for run in current)):
                    problems.append(f"added tests: parameter ids unavailable for {prefix}")
                ids.update(observed or (set() if param else {prefix}))
            elif param:
                if baseline:
                    ids.update(observed - known)
                elif [ast.dump(d) for d in node.decorator_list] != [
                    ast.dump(d) for d in old.decorator_list
                ]:
                    problems.append(f"added tests: base parameter ids unavailable for {prefix}")
    return (() if ambiguous_parameters else tuple(sorted(ids))), tuple(problems)


def _parameter_sources_changed(before: Source, after: Source) -> bool:
    """Fixture and class parameters can add ids without changing any test body."""

    def sources(source: Source) -> dict[str, tuple[str, ...]]:
        found: dict[str, tuple[str, ...]] = {}
        for qual, node in {**source.classes, **source.functions}.items():
            if qual in source.tests:
                continue  # Test decorators are handled with the individual definitions.
            decorators: list[str] = []
            for decorator in node.decorator_list:
                resolved = source.resolve(decorator)
                fixture = (
                    resolved == "pytest.fixture"
                    and isinstance(decorator, ast.Call)
                    and any(keyword.arg == "params" for keyword in decorator.keywords)
                )
                if resolved == "pytest.mark.parametrize" or fixture:
                    decorators.append(ast.dump(decorator))
            if decorators:
                found[qual] = tuple(decorators)
            if node.name == "pytest_generate_tests":
                found[qual] = (ast.dump(node),)
        return found

    return sources(before) != sources(after)


def comparison(
    ids: tuple[str, ...], before: SuiteExecution, after: SuiteExecution
) -> tuple[tuple[str, FactKind], ...]:
    """Use only the bounded published evidence, never infer absence from a cut report."""
    incomplete = before.suite.cases_complete is not True or after.suite.cases_complete is not True
    old = TestRun({c.test_id: c.outcome for c in before.suite.cases or []})
    new = TestRun(
        {c.test_id: c.outcome for c in after.suite.cases or []},
        (RunProblem("incomplete probe evidence"),) if incomplete else (),
    )
    return tuple((fact.test_id, fact.kind) for fact in affected_tests(ids, old, new))


def _probe_paths(rec: ChangeRecord, ids: tuple[str, ...]) -> tuple[str, ...]:
    """The selected test files and changed test support, with implementation left at base."""
    return tuple(
        sorted(
            {test_id.split("::", 1)[0] for test_id in ids}
            | {
                path
                for path in rec.base_files.keys() | rec.head_files.keys()
                if is_test_file(path) and rec.base_files.get(path) != rec.head_files.get(path)
            }
        )
    )


async def probe_added_tests(
    ctx: ChangeContext, rec: ChangeRecord, *, head: str, revision: int
) -> tuple[tuple[AddedTestProbe, ...], tuple[str, ...]]:
    """One selected-case run per side, two test timeouts plus bounded subprocess cleanup.

    Setup and tree capture share each side's timeout with pytest. Publication is
    outside the process budget, as for ordinary suite evidence. The caller holds
    main/agent locks; interruption keeps partial evidence and runs cleanup.
    """
    ids, diagnostics = await asyncio.to_thread(added_cases, rec, revision)
    if not ids:
        return (), diagnostics
    paths = _probe_paths(rec, ids)
    problems = list(diagnostics)
    seqs: list[int | None] = []
    for phase in ("base_probe", "head_probe"):
        run: SuiteRun | None = None
        try:
            async with asyncio.timeout(rec.task.limits.test_timeout_s) as deadline:
                if phase == "base_probe":
                    # Its finally must run outside the expired side budget.
                    checkout = base_probe_checkout(
                        ctx.workspace,
                        agent=rec.tree,
                        base=rec.base_commit,
                        head=head,
                        paths=paths,
                        timeout_s=rec.task.limits.test_timeout_s,
                    )
                    try:
                        tree = await checkout.__aenter__()
                        run = await _run(ctx, rec, tree, ids, deadline)
                    finally:
                        if not deadline.expired():
                            deadline.reschedule(None)
                        # Cleanup runs in the lock-owning task, even on the side deadline.
                        await checkout.__aexit__(None, None, None)
                else:
                    run = await _run(ctx, rec, rec.tree, ids, deadline)
        except SuiteInterrupted as error:
            await publish_suite(ctx, rec, error.run, phase=phase, revision=revision)
            raise
        except (TimeoutError, WorkspaceError, OSError) as error:
            detail = (
                f"added tests: {phase} timed out"
                if isinstance(error, TimeoutError)
                else f"added tests: {phase} unavailable"
            )
            if isinstance(error, IsolationUnavailable):
                detail += ": " + ctx.workspace.redact(str(error))
            problems.append(detail)
            if run is None:
                seqs.append(None)
                continue
            run = replace(run, problems=(*run.problems, "probe cleanup incomplete"))
        assert run is not None
        execution = await publish_suite(ctx, rec, run, phase=phase, revision=revision)
        seqs.append(next(seq for seq, item in rec.executions.items() if item is execution))
        problems.extend(f"added tests: {phase}: {p}" for p in run.problems)
        if execution.suite.cases_complete is not True:
            problems.append(f"added tests: {phase}: incomplete retained cases")
    before_seq, after_seq = seqs
    if before_seq is None or after_seq is None:
        return (), tuple(problems)
    if rec.executions[after_seq].tested_commit != head:
        return (), (*problems, "added tests: head worktree differs from proposed commit")
    facts = comparison(ids, rec.executions[before_seq], rec.executions[after_seq])
    probes = tuple(
        AddedTestProbe(test_id=test_id, before_seq=before_seq, after_seq=after_seq, result=kind)
        for test_id, kind in facts
    )
    for probe in probes:
        if probe.result == "unknown":
            reason = _unknown_reason(probe, rec.executions[before_seq], rec.executions[after_seq])
            problems.append(f"added tests: {probe.test_id}: {reason}")
    return probes, tuple(problems)


def _unknown_reason(probe: AddedTestProbe, before: SuiteExecution, after: SuiteExecution) -> str:
    base = before.suite
    if base.outcome == "error" or any(
        case.outcome == "error" and case.test_id in {probe.test_id, probe.test_id.split("::", 1)[0]}
        for case in base.cases or []
    ):
        return "base setup or import error"
    complete = before.suite.cases_complete and after.suite.cases_complete
    return "before case not collected" if complete else "incomplete probe evidence"


async def _run(
    ctx: ChangeContext,
    rec: ChangeRecord,
    tree: Tree,
    ids: tuple[str, ...],
    deadline: asyncio.Timeout,
) -> SuiteRun:
    try:
        return await ctx.workspace.run_tests(
            tree, timeout_s=rec.task.limits.test_timeout_s, case_ids=ids, isolation="required"
        )
    except SuiteUnavailable as error:
        return error.run
    except SuiteInterrupted as error:
        if not deadline.expired():
            raise
        # A side's overall deadline preserves its partial report and tree identity.
        return replace(error.run, problems=(*error.run.problems, "probe deadline exceeded"))
