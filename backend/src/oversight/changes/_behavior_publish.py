"""Explicit immutable public projection; worker protocol and diagnostics stay private."""

import json
from dataclasses import dataclass
from typing import Any, cast

from oversight.changes._behavior import BehaviorDiff
from oversight.workspace import TypedResult
from oversight_contracts.review import BehaviorDiffRow, NodeBehaviorDiff

MAX_RENDERED = 1024


@dataclass(frozen=True)
class CapturedRow:
    inputs: str
    before: str
    after: str
    unstable: bool


@dataclass(frozen=True)
class CapturedFunction:
    node_id: str
    compared: int
    differing: int
    rows: tuple[CapturedRow, ...]
    not_probed: str | None


@dataclass(frozen=True)
class BehaviorEvidence:
    revision: int
    functions: tuple[CapturedFunction, ...]


def _shorten(value: Any, limit: int) -> Any:
    """Clip public display structure, leaving JSON escaping to the serializer."""
    if isinstance(value, str):
        return value[:limit] + "..." if len(value) > limit else value
    if isinstance(value, list):
        values = cast(list[Any], value)
        items = [_shorten(item, limit) for item in values[:limit]]
        return [*items, "..."] if len(values) > limit else items
    return value


def _render(result: TypedResult) -> str:
    # Enumerate the public value fields, never serialize the private result object.
    kind = result["kind"]
    display = {"kind": kind}
    if kind == "value":
        display["value"] = result["value"]
    elif kind == "raised":
        display["class"], display["message"] = result["class"], result["message"]
    rendered = json.dumps(display, ensure_ascii=True, separators=(",", ":"))
    limit = MAX_RENDERED
    while len(rendered) > MAX_RENDERED:
        limit //= 2
        clipped = {
            key: value if key == "kind" else _shorten(value, limit)
            for key, value in display.items()
        }
        rendered = json.dumps(clipped, ensure_ascii=True, separators=(",", ":"))
    return rendered


def _category(reason: str | None) -> str | None:
    if reason is None:
        return None
    if reason == "method or class (not probed yet)":
        return "unsupported node kind"
    if reason in ("added (no base version)", "removed (no proposed version)"):
        return "version unavailable"
    return "probe unavailable"


def capture_behavior(measured: BehaviorDiff | None) -> BehaviorEvidence | None:
    """Detach all public evidence from mutable nested worker results after the probe."""
    if measured is None:
        return None
    return BehaviorEvidence(
        measured.revision,
        tuple(
            CapturedFunction(
                item.node_id,
                item.compared,
                item.differing,
                tuple(
                    CapturedRow(
                        row.call[:MAX_RENDERED],
                        _render(row.before),
                        _render(row.after),
                        row.unstable,
                    )
                    for row in item.rows
                ),
                _category(item.reason),
            )
            for item in measured.functions
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
