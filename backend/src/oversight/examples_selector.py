"""Deterministic examples admission over a captured main graph; no runtime side effects."""

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from oversight.graph import node_cone
from oversight_contracts.examples import TaskExample
from oversight_contracts.graph import GraphSnapshot, ImpactCone

SHORT_TASK_CHARS = 80
LONG_TASK_CHARS = 1600
MANY_NAMED_NODES = 3
MAX_NAMED_NODES = 20
LARGE_CONE_ENTRIES = 15


@dataclass(frozen=True)
class TargetCone:
    node_id: str
    cone: ImpactCone | None
    """None means unavailable, never a fabricated empty cone or zero test reach."""


@dataclass(frozen=True)
class ExamplesSelection:
    offer: bool
    reasons: tuple[str, ...]
    targets: tuple[str, ...]
    cones: tuple[TargetCone, ...]
    ambiguous: tuple[str, ...]
    named_candidates: int


def _resolve(task: str, snapshot: GraphSnapshot) -> tuple[tuple[str, ...], tuple[str, ...], int]:
    labels: defaultdict[str, set[str]] = defaultdict(set)
    for node in snapshot.nodes:
        for label in (node.id, node.qualname, node.name):
            if label:
                labels[label].add(node.id)
    # Consume longest full references first: a qualified reference must not also name its
    # short-name suffix or a module prefix. Identifier/dotted boundaries exclude substrings.
    matches = sorted(
        (
            (match.start(), match.end(), label)
            for label in labels
            for match in re.finditer(r"(?<![\w.])" + re.escape(label) + r"(?![\w.])", task)
        ),
        key=lambda match: (-(match[1] - match[0]), match[0], match[2]),
    )
    spans: list[tuple[int, int]] = []
    targets: set[str] = set()
    ambiguous: set[str] = set()
    candidates: set[str] = set()
    for start, end, label in matches:
        if any(start < right and end > left for left, right in spans):
            continue
        spans.append((start, end))
        ids = labels[label]
        # An exact id takes precedence over another definition's colliding short name.
        resolved = {label} if label in ids else ids
        candidates.update(resolved)
        if len(resolved) == 1:
            targets.update(resolved)
        else:
            ambiguous.add(label)
    return tuple(sorted(targets)), tuple(sorted(ambiguous)), len(candidates)


def select_examples(
    task: str,
    snapshot: GraphSnapshot,
    *,
    examples_only: bool | None,
    supplied: Sequence[TaskExample],
    cone_depth: int,
    cone_limit: int,
) -> ExamplesSelection:
    """Reason order is policy order below, independent of graph storage order.

    Candidate count includes ambiguous definitions; aliases and repeated mentions count once.
    Parse errors anywhere can hide callers/tests, so all cones are unavailable in that snapshot.
    Unknown evidence conservatively offers examples; ambiguity alone is diagnostic, not an offer.
    """
    if cone_depth < 0 or cone_limit < 0:
        raise ValueError("cone bounds must not be negative")
    task = task.strip()
    targets, ambiguous, candidates = _resolve(task, snapshot)
    bad_paths = {error.path for error in snapshot.parse_errors}
    cones = tuple(
        TargetCone(
            target,
            None
            if bad_paths
            else node_cone(snapshot, target, max_depth=cone_depth, max_nodes=cone_limit),
        )
        for target in targets
    )
    known = [fact.cone for fact in cones if fact.cone is not None]
    rules = (
        ("examples_only", examples_only is True),
        ("supplied_unconfirmed", bool(supplied)),
        ("long_task", len(task) > LONG_TASK_CHARS),
        ("many_named_targets", len(targets) >= MANY_NAMED_NODES),
        ("too_many_named_candidates", candidates > MAX_NAMED_NODES),
        ("large_cone", any(len(cone.entries) >= LARGE_CONE_ENTRIES for cone in known)),
        ("cone_omitted", any(c.omitted_by_depth or c.omitted_by_limit for c in known)),
        ("short_task_no_target", len(task) < SHORT_TASK_CHARS and not targets),
        (
            "short_task_no_test_calls",
            len(task) < SHORT_TASK_CHARS
            and bool(targets)
            and len(known) == len(targets)
            and all(c.tests_reaching == 0 for c in known),
        ),
        (
            "target_parse_error",
            any(node.id in targets and node.path in bad_paths for node in snapshot.nodes),
        ),
        ("snapshot_parse_errors", bool(bad_paths)),
    )
    reasons = tuple(code for code, applies in rules if applies)
    return ExamplesSelection(
        bool(reasons),
        reasons + (("ambiguous_name",) if ambiguous else ()),
        targets,
        cones,
        ambiguous,
        candidates,
    )
