"""Unpublished, server-owned behavior measurements, retained separately per revision.

Finite inputs and capped typed results are observations, never an equivalence claim.
C removes the network only; detached checkout disposal contains ordinary file effects.
"""

import asyncio
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from oversight.changes._behavior_inputs import BehaviorInputs, behavior_inputs, module_name
from oversight.workspace import (
    BehaviorObservation,
    BehaviorTarget,
    IsolationUnavailable,
    Tree,
    TypedResult,
    Workspace,
    WorkspaceError,
    base_probe_checkout,
)

if TYPE_CHECKING:
    from oversight.changes._state import ChangeRecord
    from oversight.changes._support import ChangeContext

MAX_ROWS = 8


@dataclass(frozen=True)
class BehaviorRow:
    call: str
    before: TypedResult
    after: TypedResult
    unstable: bool


@dataclass(frozen=True)
class FunctionBehavior:
    node_id: str
    compared: int = 0
    differing: int = 0
    rows: tuple[BehaviorRow, ...] = ()
    skipped_binding: int = 0
    reason: str | None = None

    @property
    def measurement(self) -> str:
        if self.reason:
            return "not probed: " + self.reason
        if self.differing:
            return f"{self.differing} differing inputs measured"
        return "no difference measured on these inputs"


@dataclass(frozen=True)
class BehaviorDiff:
    revision: int
    functions: tuple[FunctionBehavior, ...]


def targets(rec: "ChangeRecord") -> tuple[FunctionBehavior, ...]:
    nodes = {node.id: node for graph in (rec.base.graph, rec.head.graph) for node in graph.nodes}
    added = {node.id for node in rec.graph.nodes_added}
    removed = set(rec.graph.nodes_removed)
    found: list[FunctionBehavior] = []
    for node_id in sorted(set(rec.graph.nodes_modified) | added | removed):
        node = nodes[node_id]
        reason = None
        if node_id in added:
            reason = "added (no base version)"
        elif node_id in removed:
            reason = "removed (no proposed version)"
        elif node.kind in ("method", "class"):
            reason = "method or class (not probed yet)"
        elif (
            node.kind != "function" or not node.parent_id or nodes[node.parent_id].kind != "module"
        ):
            reason = "unsupported signature"
        found.append(FunctionBehavior(node_id, reason=reason))
    return tuple(found)


def _prepare(
    rec: "ChangeRecord", found: list[FunctionBehavior], deadline: float
) -> dict[str, BehaviorInputs]:
    nodes = {node.id: node for node in rec.head.graph.nodes}
    inputs: dict[str, BehaviorInputs] = {}
    for index, result in enumerate(found):
        if result.reason is not None:
            continue
        node = nodes[result.node_id]
        generated = behavior_inputs(
            module_name(node.path), node.name, rec.base_files, rec.head_files, deadline=deadline
        )
        found[index] = replace(
            result, reason=generated.reason, skipped_binding=generated.skipped_binding
        )
        if generated.reason is None:
            inputs[node.id] = generated
    return inputs


def _requests(
    rec: "ChangeRecord",
    inputs: dict[str, BehaviorInputs],
    side: int,
    selected: tuple[str, int] | None = None,
) -> tuple[BehaviorTarget, ...]:
    nodes = {node.id: node for node in rec.head.graph.nodes}
    return tuple(
        BehaviorTarget(
            node_id,
            module_name(nodes[node_id].path),
            nodes[node_id].name,
            tuple(
                item.before if side == 0 else item.after
                for index, item in enumerate(generated.inputs)
                if selected is None or selected == (node_id, index)
            ),
        )
        for node_id, generated in inputs.items()
        if selected is None or selected[0] == node_id
    )


async def _compare(
    workspace: Workspace,
    rec: "ChangeRecord",
    trees: list[Tree],
    inputs: dict[str, BehaviorInputs],
    found: list[FunctionBehavior],
    deadline: float,
    completed: set[str],
) -> None:
    sides = [
        await workspace.run_behavior(
            tree, _requests(rec, inputs, side), timeout_s=_remaining(deadline)
        )
        for side, tree in enumerate(trees)
    ]
    for position, result in enumerate(found):
        if result.node_id not in inputs:
            continue
        node_id = result.node_id
        before, after = (
            side.get(node_id, BehaviorObservation(problem="budget exhausted")) for side in sides
        )
        if before.problem or after.problem:
            problem = (
                f"import failed on {'base' if before.problem else 'proposed'} side"
                if "import failed" in (before.problem, after.problem)
                else (before.problem or after.problem)
            )
            found[position] = replace(result, reason=problem)
            completed.add(node_id)
            continue
        measured = [
            (index, old, new)
            for index, (old, new) in enumerate(zip(before.results, after.results, strict=True))
            if old["kind"] != "unrepresentable" and new["kind"] != "unrepresentable"
        ]
        if not measured:
            found[position] = replace(result, reason="unrepresentable input")
            completed.add(node_id)
            continue
        # Count every measured difference; retained rows alone are bounded at eight.
        differing = [index for index, old, new in measured if old != new]
        result = replace(result, compared=len(measured), differing=len(differing))
        found[position] = result
        rows: list[BehaviorRow] = []
        for index in differing:
            unstable = False
            for _ in range(2):
                for side, tree in enumerate(trees):
                    rerun = await workspace.run_behavior(
                        tree,
                        _requests(rec, inputs, side, (node_id, index)),
                        timeout_s=_remaining(deadline),
                    )
                    observation = rerun[node_id]
                    original = (before, after)[side].results[index]
                    unstable |= bool(observation.problem or observation.results != (original,))
            if len(rows) < MAX_ROWS:
                rows.append(
                    BehaviorRow(
                        inputs[node_id].inputs[index].call,
                        before.results[index],
                        after.results[index],
                        unstable,
                    )
                )
                found[position] = replace(result, rows=tuple(rows))

        completed.add(node_id)


def _remaining(deadline: float) -> float:
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise TimeoutError
    return seconds


async def probe_behavior(
    ctx: "ChangeContext", rec: "ChangeRecord", *, head: str, revision: int
) -> BehaviorDiff:
    """One shared deadline for analysis, both sides and every rerun: twice test_timeout_s.

    The caller owns main/agent locks. Cleanup runs outside the computation deadline,
    in that same task, retaining the existing bounded Git and namespace cleanup.
    """
    found = list(targets(rec))
    deadline = time.monotonic() + 2 * rec.task.limits.test_timeout_s
    stack = AsyncExitStack()
    completed: set[str] = set()
    try:
        async with asyncio.timeout_at(deadline):
            inputs = await ctx.whole(asyncio.to_thread(_prepare, rec, found, deadline))
            if not inputs:
                return BehaviorDiff(revision, tuple(found))
            if not ctx.workspace.isolation.available:
                raise IsolationUnavailable("session isolation unavailable")
            trees: list[Tree] = []
            for commit in (rec.base_commit, head):
                trees.append(
                    await stack.enter_async_context(
                        base_probe_checkout(
                            ctx.workspace,
                            agent=rec.tree,
                            base=commit,
                            head=commit,
                            paths=(),
                            timeout_s=_remaining(deadline),
                        )
                    )
                )
            await _compare(ctx.workspace, rec, trees, inputs, found, deadline, completed)
    except (TimeoutError, WorkspaceError, OSError) as error:
        reason = (
            "budget exhausted"
            if isinstance(error, TimeoutError)
            else "isolation unavailable"
            if isinstance(error, IsolationUnavailable)
            else f"probe unavailable ({type(error).__name__})"
        )
        found = [
            replace(result, reason=result.reason or reason)
            if result.node_id not in completed
            else result
            for result in found
        ]
    finally:
        await stack.aclose()
    return BehaviorDiff(revision, tuple(found))


def unavailable_behavior(rec: "ChangeRecord", revision: int, reason: str) -> BehaviorDiff:
    return BehaviorDiff(
        revision, tuple(replace(item, reason=item.reason or reason) for item in targets(rec))
    )
