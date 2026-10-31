"""Explicit immutable public projection; worker protocol and diagnostics stay private."""

import json
from dataclasses import dataclass, replace
from itertools import islice
from typing import Any, cast

from oversight.changes._behavior import BehaviorDiff, BehaviorRow, FunctionBehavior, result_display
from oversight.changes._behavior_reasons import NOT_PROBED_REASONS
from oversight.changes._review_identity import direct_result
from oversight.changes._review_samples import Observation, OutcomeKind, Selection
from oversight.workspace import TypedResult
from oversight_contracts.review import BehaviorDiffRow, NodeBehaviorDiff, ParameterExtensionDiff

MAX_RENDERED = 1024


@dataclass(frozen=True)
class CapturedRow:
    inputs: str
    before: str
    after: str
    unstable: bool
    observation: Observation | None = None


@dataclass(frozen=True)
class CapturedGroup:
    kind: OutcomeKind
    count: int
    witness: Observation | None


@dataclass(frozen=True)
class CapturedFunction:
    node_id: str
    compared: int
    differing: int
    rows: tuple[CapturedRow, ...]
    not_probed: str | None
    groups: tuple[CapturedGroup, ...] | None = None
    unconfirmed: int = 0

    def review_samples(self) -> Selection:
        samples: list[Observation] = []
        problems: list[str] = []

        if self.groups is None:
            for row in self.rows:
                if row.observation is not None:
                    samples.append(row.observation)
                else:
                    problems.append(f"review cards: {self.node_id}: row witness unavailable")
            covered = sum(not row.unstable for row in self.rows)
        else:
            covered = sum(group.count for group in self.groups)
            for group in self.groups:
                if group.witness is None:
                    problems.append(
                        f"review cards: {self.node_id}: "
                        f"{group.count} differing observations not retained "
                        f"({group.kind.description})"
                    )
                else:
                    samples.append(group.witness)
            if self.unconfirmed:
                problems.append(
                    f"review cards: {self.node_id}: "
                    f"{self.unconfirmed} differences unstable or incomplete"
                )
        if self.not_probed:
            problems.append(f"review cards: {self.node_id}: behavior comparison incomplete")
        if self.differing > covered:
            problems.append(
                f"review cards: {self.node_id}: "
                f"{self.differing - covered} differing observations not retained"
            )
        return Selection(tuple(samples), tuple(problems))


@dataclass(frozen=True)
class CapturedExtension:
    node_id: str
    parameters: tuple[str, ...]
    run_count: int
    rejected_before: int
    rows: tuple[CapturedRow, ...]
    not_probed: str | None


@dataclass(frozen=True)
class BehaviorEvidence:
    revision: int
    functions: tuple[CapturedFunction, ...]
    extensions: tuple[CapturedExtension, ...] = ()


def _shorten(value: Any, limit: int) -> Any:
    """Clip public display structure, leaving JSON escaping to the serializer."""
    if isinstance(value, str):
        return value[:limit] + "..." if len(value) > limit else value
    if isinstance(value, list):
        values = cast(list[Any], value)
        items = [_shorten(item, limit) for item in values[:limit]]
        return [*items, "..."] if len(values) > limit else items
    if isinstance(value, dict):
        mapping = cast(dict[str, Any], value)
        clipped = {
            _shorten(key, limit): _shorten(item, limit)
            for key, item in islice(mapping.items(), limit)
        }
        if len(mapping) > limit:
            clipped["..."] = "..."
        return clipped
    return value


def _render(result: TypedResult, *, bounded: bool = True) -> str:
    # Enumerate the public value fields, never serialize the private result object.
    kind = result["kind"]
    display = result_display(result)
    prefix = "items of iterator: " if result.get("observation") == "iterator_items" else ""
    rendered = json.dumps(display, ensure_ascii=True, separators=(",", ":"))
    if not bounded:
        return prefix + rendered
    limit = MAX_RENDERED
    while len(prefix) + len(rendered) > MAX_RENDERED:
        if limit == 0:
            # Unexpected scalar leaves may not shrink; retain only the validated result kind.
            return prefix + json.dumps({"kind": kind}, ensure_ascii=True, separators=(",", ":"))
        limit //= 2
        clipped = {
            key: value if key == "kind" else _shorten(value, limit)
            for key, value in display.items()
        }
        rendered = json.dumps(clipped, ensure_ascii=True, separators=(",", ":"))
    return prefix + rendered


def _capture_row(node_id: str, row: BehaviorRow) -> CapturedRow:
    before, after = _render(row.before, bounded=False), _render(row.after, bounded=False)
    identity = tuple(
        f"{result['kind']}:{result.get('observation', '')}:{result.get('fingerprint', '')}"
        for result in (row.before, row.after)
    )
    results = direct_result(row.before), direct_result(row.after)
    reason = None
    if not row.repeat_complete or not row.inputs_equal:
        reason = "comparison or repetition incomplete"
    elif row.unstable or identity[0] == identity[1]:
        reason = "unstable or equal observation"
    elif any(not result.get("fingerprint") for result in (row.before, row.after)):
        reason = "typed identity unavailable"
    elif any(len(text) > MAX_RENDERED for text in (row.call, before, after)):
        reason = "display sample truncated"
    elif None in results or before == after:
        reason = "typed display incomplete"
    observation = Observation(
        node_id,
        (row.call, before, after),
        (row.input_identity or row.call, results[0] or identity[0], results[1] or identity[1]),
        reason,
    )
    return CapturedRow(
        row.call[:MAX_RENDERED],
        _render(row.before),
        _render(row.after),
        row.unstable,
        observation,
    )


def _public_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    parts = reason.split("; ")
    if any(part not in NOT_PROBED_REASONS for part in parts):
        return "probe unavailable"
    phrases = {NOT_PROBED_REASONS[part] for part in parts}
    return "; ".join(
        phrase for phrase in dict.fromkeys(NOT_PROBED_REASONS.values()) if phrase in phrases
    )


def _capture_function(item: FunctionBehavior) -> CapturedFunction:
    rows = [_capture_row(item.node_id, row) for row in item.rows]
    groups: list[CapturedGroup] | None = None
    if item.groups is not None:
        groups = []
        for group in item.groups:
            witness = None
            for index, row in enumerate(item.rows):
                if row == group.witness:
                    observed = rows[index].observation
                    if observed is not None:
                        witness = replace(
                            observed,
                            group=group.kind,
                            coverage=group.count,
                            differing=item.differing,
                        )
                        rows[index] = replace(rows[index], observation=witness)
                    break
            groups.append(CapturedGroup(group.kind, group.count, witness))
    return CapturedFunction(
        item.node_id,
        item.compared,
        item.differing,
        tuple(rows),
        _public_reason(item.reason),
        tuple(groups) if groups is not None else None,
        item.unconfirmed,
    )


def capture_behavior(measured: BehaviorDiff | None) -> BehaviorEvidence | None:
    """Detach all public evidence from mutable nested worker results after the probe."""
    if measured is None:
        return None
    return BehaviorEvidence(
        measured.revision,
        tuple(_capture_function(item) for item in measured.functions),
        tuple(
            CapturedExtension(
                item.node_id,
                item.parameters,
                item.run_count,
                item.rejected_before,
                tuple(
                    CapturedRow(
                        row.call[:MAX_RENDERED],
                        _render(row.before),
                        _render(row.after),
                        row.unstable,
                    )
                    for row in item.rows
                ),
                _public_reason(item.reason),
            )
            for item in measured.extensions
        ),
    )


def project_behavior(
    evidence: BehaviorEvidence | None, *, revision: int
) -> list[NodeBehaviorDiff] | None:
    """Pure reconstruction and candidate check; no record reads or I/O."""
    if evidence is None:
        return None
    if evidence.revision != revision:
        raise ValueError("behavior evidence belongs to another revision")
    return [
        NodeBehaviorDiff(
            node_id=item.node_id,
            compared_count=item.compared,
            differing_count=item.differing,
            rows=[
                BehaviorDiffRow(
                    inputs=row.inputs, before=row.before, after=row.after, unstable=row.unstable
                )
                for row in item.rows
            ],
            not_probed=item.not_probed,
        )
        for item in evidence.functions
    ]


def project_extensions(
    evidence: BehaviorEvidence | None, *, revision: int
) -> list[ParameterExtensionDiff] | None:
    """Evidence only: these rows never enter behavior counts or review-card selection."""
    if evidence is None:
        return None
    if evidence.revision != revision:
        raise ValueError("parameter extension evidence belongs to another revision")
    return [
        ParameterExtensionDiff(
            node_id=item.node_id,
            parameters=list(item.parameters),
            run_count=item.run_count,
            rejected_before=item.rejected_before,
            rows=[
                BehaviorDiffRow(
                    inputs=row.inputs, before=row.before, after=row.after, unstable=row.unstable
                )
                for row in item.rows
            ],
            not_probed=item.not_probed,
        )
        for item in evidence.extensions
    ] or None
