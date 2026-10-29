"""Explicit immutable public projection; worker protocol and diagnostics stay private."""

import json
from dataclasses import dataclass

from oversight.changes._behavior import BehaviorDiff, BehaviorRow
from oversight.changes._review_samples import Observation
from oversight.workspace import TypedResult
from oversight_contracts.review import BehaviorDiffRow, NodeBehaviorDiff

MAX_RENDERED = 1024


@dataclass(frozen=True)
class CapturedRow:
    inputs: str
    before: str
    after: str
    unstable: bool
    observation: Observation | None = None


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


def _render(result: TypedResult) -> str:
    # Enumerate the public value fields, never serialize the private result object.
    kind = result["kind"]
    display = {"kind": kind}
    if kind == "value":
        display["value"] = result["value"]
    elif kind == "raised":
        display["class"], display["message"] = result["class"], result["message"]
    return json.dumps(display, ensure_ascii=True, separators=(",", ":"))


def _capture_row(node_id: str, row: BehaviorRow) -> CapturedRow:
    before, after = _render(row.before), _render(row.after)
    identity = tuple(
        f"{result['kind']}:{result.get('fingerprint', '')}" for result in (row.before, row.after)
    )
    reason = None
    if not row.repeat_complete or not row.inputs_equal:
        reason = "comparison or repetition incomplete"
    elif row.unstable or identity[0] == identity[1]:
        reason = "unstable or equal observation"
    elif any(not result.get("fingerprint") for result in (row.before, row.after)):
        reason = "typed identity unavailable"
    elif any(len(text) > MAX_RENDERED for text in (row.call, before, after)):
        reason = "display sample truncated"
    observation = Observation(
        node_id, (row.call, before, after), (row.call, identity[0], identity[1]), reason
    )
    return CapturedRow(
        row.call[:MAX_RENDERED],
        before[:MAX_RENDERED],
        after[:MAX_RENDERED],
        row.unstable,
        observation,
    )


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
                tuple(_capture_row(item.node_id, row) for row in item.rows),
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
