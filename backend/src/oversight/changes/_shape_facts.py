"""Capture bounded structural observations, without changing review decisions."""

import asyncio
from dataclasses import dataclass
from functools import partial

from oversight.changes._shape import compare_shape
from oversight.changes._shape_duplicates import compare_duplicates
from oversight.changes._state import ChangeRecord
from oversight_contracts.review import ShapeFact

SHAPE_TIMEOUT_S = 4.0  # Per producer: bounds the wait, not the worker thread.


@dataclass(frozen=True)
class ShapeCapture:
    facts: tuple[ShapeFact, ...] | None
    complete: bool | None
    problems: tuple[str, ...]


async def capture_shape(rec: ChangeRecord, *, base_tree: str, head_tree: str) -> ShapeCapture:
    """Snapshot on the loop under the caller's locks, then run each pure producer."""
    changed = frozenset(node.id for node in rec.graph.nodes_added) | frozenset(
        rec.graph.nodes_modified
    )
    base_files, head_files = dict(rec.base_files), dict(rec.head_files)
    base, head = rec.base.graph, rec.head.graph
    producers = (
        (
            "size and import checks",
            partial(compare_shape, base, head, changed, base_tree=base_tree, head_tree=head_tree),
        ),
        (
            "duplicate check",
            partial(
                compare_duplicates, base, head, base_files, head_files, changed, head_tree=head_tree
            ),
        ),
    )
    facts: list[ShapeFact] = []
    problems: list[str] = []
    failures: list[str] = []
    complete = True
    for label, producer in producers:
        try:
            result = await asyncio.wait_for(asyncio.to_thread(producer), SHAPE_TIMEOUT_S)
        except TimeoutError:
            failures.append(f"shape: {label} timed out")
        except Exception:
            failures.append(f"shape: {label} unavailable")
        else:
            facts.extend(result.facts)
            complete = complete and result.complete
            problems.extend(result.problems)
    if len(failures) == len(producers):
        return ShapeCapture(None, None, tuple(failures))
    return ShapeCapture(tuple(facts), complete and not failures, tuple(problems + failures))


def project_shape(c: ShapeCapture | None) -> tuple[list[ShapeFact] | None, bool | None]:
    """Detach published models and nested evidence from retained capture."""
    if c is None:
        return None, None
    shape = [fact.model_copy(deep=True) for fact in c.facts] if c.facts is not None else None
    shape_complete = c.complete
    return shape, shape_complete
