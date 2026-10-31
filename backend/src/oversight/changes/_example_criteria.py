"""Inert criteria installation and required-suite completeness at existing suite seams."""

from __future__ import annotations

import ast
import asyncio
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace

from oversight.agents import canonical_json
from oversight.changes._state import ChangeRecord, WrongState
from oversight.changes._support import ChangeContext
from oversight.workspace import (
    RenderedCriteria,
    SuiteRun,
    WorkspaceError,
    content_hash,
    render_examples,
)
from oversight.workspace import parse_cases as parse_example_cases
from oversight_contracts.examples import TaskExample


@dataclass(frozen=True)
class RequiredCriteria:
    path: str
    sha256: str
    test_ids: tuple[str, ...]
    example_ids: tuple[str, ...]


def render_criteria(examples: Sequence[TaskExample], change_id: str) -> RenderedCriteria:
    """Preview and installation share strict parsing and the public canonical JSON rule."""
    parsed = parse_example_cases(examples)
    # Validate before canonicalization: duplicate keys must never disappear here.
    normalized = [
        example.model_copy(
            update={
                "inputs": canonical_json(
                    {
                        "node_id": case.node_id,
                        "args": _json(case.args),
                        "kwargs": _json(case.kwargs),
                    }
                ),
                "expected_output": canonical_json(_json(case.expected)),
            }
        )
        for example, case in zip(examples, parsed, strict=True)
    ]
    return render_examples(normalized, change_id)


def _json(text: str) -> object:
    import json

    return json.loads(text)


async def eligible_target(rec: ChangeRecord, node_id: str) -> str | None:
    """Eligibility is based only on the captured base graph and base module source."""
    node = next((node for node in rec.base.graph.nodes if node.id == node_id), None)
    if node is None:
        return "target_unknown"
    if node.kind != "function":
        return "target_not_function"
    parent = next(
        (candidate for candidate in rec.base.graph.nodes if candidate.id == node.parent_id), None
    )
    if parent is None or parent.kind != "module":
        return "target_not_top_level"
    source = rec.base_files.get(node.path)
    if source is None:
        return "target_unparseable"
    return await asyncio.to_thread(_definition, source, node_id.rsplit(":", 1)[-1])


def _definition(source: str, name: str) -> str | None:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return "target_unparseable"
    definitions = [
        statement
        for statement in tree.body
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)) and statement.name == name
    ]
    if not definitions:
        return "target_not_top_level"
    if any(isinstance(statement, ast.AsyncFunctionDef) for statement in definitions):
        return "target_async"
    return None


def _installable(rec: ChangeRecord) -> None:
    if rec.state != "working" or rec.person_intent is not None or rec.criteria is not None:
        raise WrongState("criteria require an unconfirmed working change")


async def install_criteria(
    ctx: ChangeContext, rec: ChangeRecord, confirmed: Sequence[TaskExample]
) -> RequiredCriteria:
    """E3's future confirmation port; no production caller in E4."""
    from oversight.changes._proposal import read, refresh, send_file

    _installable(rec)
    rendered = render_criteria(confirmed, rec.change_id)
    required = RequiredCriteria(
        rendered.path,
        rendered.sha256,
        tuple(test_id for _, test_id in rendered.cases),
        tuple(example_id for example_id, _ in rendered.cases),
    )
    async with ctx.workspace.locks(main=True, agent_id=rec.agent_id):
        _installable(rec)
        for example, case in zip(confirmed, parse_example_cases(confirmed), strict=True):
            if example.pin is None or example.pin.confirmed_by != "person":
                raise ValueError("criteria require person confirmation")
            reason = await eligible_target(rec, case.node_id)
            if reason is not None:
                raise ValueError(reason)
        current = await read(ctx, rec.tree, rendered.path)
        if (
            rendered.path in rec.base_files
            or current not in {None, rendered.source}
            or rec.head_files.get(rendered.path) not in {None, rendered.source}
        ):
            raise ValueError("criteria path already exists")
        if current is None:
            await rec.tree.write_text(rendered.path, rendered.source, expected_hash=None)
        rec.observed_paths.add(rendered.path)
        rec.touched.add(rendered.path)
        await refresh(ctx, rec, {rendered.path})
        await send_file(ctx, rec, rendered.path, author="user")
        rec.criteria = required
    return required


async def _file_problem(ctx: ChangeContext, rec: ChangeRecord, run: SuiteRun) -> str | None:
    required = rec.criteria
    assert required is not None
    try:
        result = await ctx.run(rec.tree, "show", f"{run.tested_tree}:{required.path}")
    except (WorkspaceError, OSError, TimeoutError):
        return "tree_unreadable"
    if result.timed_out or result.truncated:
        return "tree_unreadable"
    if result.returncode != 0:
        # Git distinguishes a missing path from an unavailable tree/object.
        if "does not exist in" in result.stderr or "exists on disk, but not in" in result.stderr:
            return "file_missing"
        return "tree_unreadable"
    if content_hash(result.stdout.encode("utf-8")) != required.sha256:
        return "file_edited"
    return None


def _report_problem(required: RequiredCriteria, run: SuiteRun) -> str | None:
    counts = Counter(case.test_id for case in run.cases)
    if run.problems or any(counts[test_id] > 1 for test_id in required.test_ids):
        return "report_unverifiable"
    if any(counts[test_id] == 0 for test_id in required.test_ids):
        return "case_uncollected"
    if any(case.outcome != "passed" for case in run.cases if case.test_id in required.test_ids):
        return "case_not_passed"
    return None


async def verify_run(
    ctx: ChangeContext, rec: ChangeRecord, run: SuiteRun, *, phase: str
) -> SuiteRun:
    """Normalize only passed proposal/combined evidence; preserve every observed case."""
    required = rec.criteria
    if required is None or phase not in {"proposal", "combined"} or run.tested_tree is None:
        return run
    if run.result.outcome != "passed":
        return run
    code = await _file_problem(ctx, rec, run) or _report_problem(required, run)
    if code is None:
        return run
    normalized = _normalize_run(run, code)
    # Counts avoid exposing model-authored ids, slugs or inputs.
    await ctx.error(
        "tests",
        f"example_criteria: {code}; required_cases={len(required.test_ids)}",
        rec,
        fatal=False,
    )
    return normalized


def _normalize_run(run: SuiteRun, code: str) -> SuiteRun:
    return replace(
        run,
        result=run.result.model_copy(update={"outcome": "error"}),
        problems=(*run.problems, f"example_criteria: {code}"),
    )


def unverified_run(rec: ChangeRecord, run: SuiteRun) -> SuiteRun:
    """Preserve observed proposal evidence when criteria verification was interrupted."""
    if rec.criteria is None or run.tested_tree is None or run.result.outcome != "passed":
        return run
    return _normalize_run(run, "tree_unreadable")


def refusal_code(run: SuiteRun) -> str:
    if (
        run.problems
        and run.problems[-1].startswith("example_criteria: ")
        and any("code=integrity_invalid" in problem for problem in run.problems)
    ):
        return "integrity_invalid"
    return "suite_failed"
