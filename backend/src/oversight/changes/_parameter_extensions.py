"""Independent observations for supplied calls that use proposed-only parameters."""

import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from oversight.changes._behavior import BehaviorRow, FunctionBehavior
from oversight.changes._behavior_inputs import module_name
from oversight.changes._parameter_inputs import ExtensionInputs, extension_inputs
from oversight.workspace import BehaviorObservation, BehaviorTarget, Tree, Workspace

if TYPE_CHECKING:
    from oversight.changes._state import ChangeRecord


@dataclass(frozen=True)
class ParameterExtension:
    node_id: str
    parameters: tuple[str, ...]
    run_count: int = 0
    rejected_before: int = 0
    rows: tuple[BehaviorRow, ...] = ()
    reason: str | None = None


def _remaining(deadline: float) -> float:
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise TimeoutError
    return seconds


def _record(
    result: ParameterExtension,
    generated: ExtensionInputs,
    before: BehaviorObservation,
    after: BehaviorObservation,
) -> ParameterExtension:
    if before.problem or after.problem:
        return replace(result, reason=_join_reason(generated.reason, "worker failed"))
    rows: list[BehaviorRow] = []
    runs = rejected = 0
    reason = generated.reason
    if len(before.results) != len(generated.samples) or len(after.results) != len(
        generated.samples
    ):
        reason = _join_reason(reason, "worker failed")
    for sample, old, new in zip(generated.samples, before.results, after.results, strict=False):
        if sample.input_identity is None or (
            generated.receiver_class is not None
            and (
                old.get("pre_fingerprint") is None
                or old.get("pre_fingerprint") != new.get("pre_fingerprint")
            )
        ):
            reason = _join_reason(reason, "unrepresentable input or unequal receiver state")
            continue
        if any(
            value.get("kind") not in ("value", "raised")
            or not isinstance(value.get("fingerprint"), str)
            for value in (old, new)
        ):
            reason = _join_reason(reason, "unrepresentable input")
            continue
        runs += 1
        rejected += int(old.get("binding_rejected") is True)
        if len(rows) < 8:
            # Single-run evidence: no repeated execution has disagreed.
            rows.append(BehaviorRow(sample.call, old, new, unstable=False))
    return replace(
        result, run_count=runs, rejected_before=rejected, rows=tuple(rows), reason=reason
    )


def _join_reason(reason: str | None, added: str) -> str:
    return "; ".join(dict.fromkeys([*(reason.split("; ") if reason else []), added]))


class ExtensionProbe:
    def __init__(self) -> None:
        self.inputs: dict[str, ExtensionInputs] = {}
        self.targets: dict[str, BehaviorTarget] = {}
        self._results: dict[str, ParameterExtension] = {}
        self.completed: set[str] = set()

    @property
    def results(self) -> tuple[ParameterExtension, ...]:
        return tuple(self._results.values())

    def prepare(self, rec: "ChangeRecord", found: list[FunctionBehavior], deadline: float) -> None:
        nodes = {node.id: node for node in rec.head.graph.nodes}
        for result in found:
            if result.reason is not None:
                continue
            _remaining(deadline)
            node = nodes[result.node_id]
            name = node.id.split(":", 1)[1] if node.kind == "method" else node.name
            generated = extension_inputs(
                module_name(node.path),
                name,
                rec.base_files,
                rec.head_files,
                method=node.kind == "method",
                deadline=deadline,
            )
            if generated is None:
                continue
            self.inputs[node.id] = generated
            self.targets[node.id] = BehaviorTarget(
                node.id,
                module_name(node.path),
                node.name,
                tuple(sample.before for sample in generated.samples),
                receiver_class=generated.receiver_class,
                method_kind="method" if generated.receiver_class else None,
                properties=generated.properties,
                observe_binding=True,
            )
            self._results[node.id] = ParameterExtension(
                node.id, generated.parameters, reason="budget exhausted"
            )

    async def measure(self, workspace: Workspace, trees: list[Tree], deadline: float) -> None:
        for node_id, generated in self.inputs.items():
            # Exactly the same target and encoded invocation go to both checkouts.
            sides = [
                (
                    await workspace.run_behavior(
                        tree, (self.targets[node_id],), timeout_s=_remaining(deadline)
                    )
                ).get(node_id, BehaviorObservation(problem="unavailable"))
                for tree in trees
            ]
            self._results[node_id] = _record(self._results[node_id], generated, sides[0], sides[1])
            self.completed.add(node_id)

    def unavailable(self, reason: str) -> None:
        self._results = {
            node_id: result
            if node_id in self.completed
            else replace(result, reason=result.reason or reason)
            for node_id, result in self._results.items()
        }
