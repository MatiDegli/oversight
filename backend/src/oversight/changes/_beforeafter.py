"""Pure per-test evidence, independent of the runner, contracts and merge policy.

JUnit xunit1 preserves exact file paths. Default xunit2 loses that information and
necessarily assumes conventional, dot-free pytest paths (test_*.py or *_test.py)
and Test* classes, without --junitprefix. An unresolvable split is reported.
Exact recovery for custom collectors or dotted paths requires xunit1.
"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from oversight.workspace import MAX_TESTS as MAX_TESTS
from oversight.workspace import MAX_XML_BYTES as MAX_XML_BYTES
from oversight.workspace import JUnitOutcome as Outcome
from oversight.workspace import JUnitRun as TestRun
from oversight.workspace import RunProblem as RunProblem
from oversight.workspace import parse_junit as parse_junit
from oversight_contracts.common import CaseOutcome

__all__ = [
    "MAX_TESTS",
    "MAX_XML_BYTES",
    "Outcome",
    "RunProblem",
    "TestFact",
    "TestRun",
    "affected_tests",
    "facts_line",
    "parse_junit",
]

FactKind = Literal[
    "fails_before_passes_after",
    "passes_before_and_after",
    "fails_after",
    "not_collected_after",
    "skipped",
    "unknown",
]
UnknownReason = Literal[
    "before_run_missing",
    "before_test_missing",
    "before_report_problem",
    "after_report_problem",
    "before_setup_or_import_error",
]
_AFTER_OUTCOMES: dict[CaseOutcome, FactKind | None] = {
    "passed": None,  # Only a pass may proceed to the before evidence.
    "failed": "fails_after",
    "error": "fails_after",
    "skipped": "skipped",
}
_BEFORE_OUTCOMES: dict[CaseOutcome, tuple[FactKind, UnknownReason | None]] = {
    "passed": ("passes_before_and_after", None),
    "failed": ("fails_before_passes_after", None),
    "error": ("unknown", "before_setup_or_import_error"),
    "skipped": ("skipped", None),
}


@dataclass(frozen=True)
class TestFact:
    """Base setup/import errors are unknown; only assertions prove a base failure.

    before_test_missing can mean a new file, a renamed test or a deselection;
    JUnit alone cannot establish which. It never proves failure on main.
    """

    test_id: str
    kind: FactKind
    before: Outcome | None
    after: Outcome | None
    reason: UnknownReason | None = None


def affected_tests(
    added_or_modified_test_ids: Iterable[str], before: TestRun | None, after: TestRun
) -> tuple[TestFact, ...]:
    """One fact per distinct id, sorted for stable output; no inference from suite totals.

    After-report problems take precedence. Otherwise an absent/failed/skipped after
    outcome is decisive without before evidence. For an after pass, before problems,
    absent evidence and skips cannot establish a before failure or pass.
    """
    return tuple(
        _fact(test_id, before, after) for test_id in sorted(set(added_or_modified_test_ids))
    )


def _fact(test_id: str, before: TestRun | None, after: TestRun) -> TestFact:
    old = before.outcomes.get(test_id) if before is not None else None
    new = after.outcomes.get(test_id)
    reason: UnknownReason | None = None
    if after.problems:
        kind, reason = "unknown", "after_report_problem"
    elif new is None:
        kind = "not_collected_after"
    elif (after_kind := _AFTER_OUTCOMES.get(new, "unknown")) is not None:
        kind = after_kind
    elif before is None:
        kind, reason = "unknown", "before_run_missing"
    elif before.problems:
        kind, reason = "unknown", "before_report_problem"
    elif old is None:
        kind, reason = "unknown", "before_test_missing"
    else:
        kind, reason = _BEFORE_OUTCOMES.get(old, ("unknown", None))
    return TestFact(test_id, kind, old, new, reason)


def facts_line(facts: Iterable[TestFact]) -> str:
    """Neutral counts, in fixed order; errors are named separately from assertions."""
    items = tuple(facts)
    counts = Counter(f.kind for f in items)
    phrases = (
        (
            "fails_before_passes_after",
            "fails before and passes after",
            "fail before and pass after",
        ),
        ("passes_before_and_after", "passes before too", "pass before too"),
        ("fails_after", "fails after", "fail after"),
        ("not_collected_after", "not collected after", "not collected after"),
        ("skipped", "skipped before or after", "skipped before or after"),
        ("unknown", "has unknown comparison", "have unknown comparisons"),
    )
    parts: list[str] = []
    for kind, singular, plural in phrases:
        count = counts[kind]
        if count:
            parts.append(
                f"{count} test{'' if count == 1 else 's'} {singular if count == 1 else plural}"
            )
    for side in ("before", "after"):
        count = sum(getattr(f, side) == "error" for f in items)
        if count:
            parts.append(f"{count} error{'' if count == 1 else 's'} {side}")
    return "; ".join(parts) if parts else "No added or modified tests"
