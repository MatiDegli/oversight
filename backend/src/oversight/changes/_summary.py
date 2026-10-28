"""Pure, bounded summary of server evidence; author declarations are not an input."""

from collections.abc import Mapping
from dataclasses import dataclass
from math import fsum, isfinite

from oversight_contracts.events import SuiteExecution
from oversight_contracts.review import RoleUsage


@dataclass(frozen=True, kw_only=True)
class SummaryEvidence:
    task: str | None = None
    changed_paths: tuple[str, ...] | None = None
    changed_nodes: tuple[str, ...] | None = None
    added_test_ids: tuple[str, ...] | None = None
    usage: tuple[RoleUsage, ...] | None = None


def _line(text: str) -> str:
    normalized = " ".join(text.split())
    return normalized if len(normalized) <= 240 else normalized[:239] + "…"


def _count(items: tuple[str, ...] | None) -> str:
    return "unknown" if items is None else str(len(set(items)))


def _cost(usage: tuple[RoleUsage, ...] | None) -> str:
    if not usage or any(
        bucket.cost_usd is None
        or bucket.unknown_calls != 0
        or bucket.role is None
        or bucket.model is None
        or bucket.round is None
        for bucket in usage
    ):
        return "cost unknown"
    try:
        total = fsum(bucket.cost_usd for bucket in usage if bucket.cost_usd is not None)
    except OverflowError:
        return "cost unknown"
    return f"cost ${total:.6g}" if isfinite(total) else "cost unknown"


def build_summary(
    evidence: SummaryEvidence, executions: Mapping[int, SuiteExecution], *, revision: int
) -> list[str]:
    """Use the last proposal suite for this revision, not prior or probe outcomes.

    Other-result comparisons have no public producer yet. Even private behavior
    measurements must never become summary claims through this path.
    """
    current = [
        (seq, execution.suite)
        for seq, execution in executions.items()
        if execution.phase == "proposal" and execution.revision == revision
    ]
    tests = "tests run: unknown"
    if current:
        _, suite = max(current, key=lambda item: item[0])
        tests = (
            f"tests run: {suite.passed + suite.failed} ({suite.passed} passed, "
            f"{suite.failed} failed, {suite.skipped} skipped; {suite.outcome})"
        )
    return [
        _line(f"Task: {evidence.task or 'unknown'}"),
        _line(
            f"Changed: {_count(evidence.changed_paths)} files, "
            f"{_count(evidence.changed_nodes)} nodes; "
            f"tests added: {_count(evidence.added_test_ids)}; {tests}"
        ),
        _line(f"Other results: not compared; {_cost(evidence.usage)}"),
    ]
