"""Pure per-test evidence, independent of the runner, contracts and merge policy.

JUnit xunit1 preserves exact file paths. Default xunit2 loses that information and
necessarily assumes conventional, dot-free pytest paths (test_*.py or *_test.py)
and Test* classes, without --junitprefix. An unresolvable split is reported.
Exact recovery for custom collectors or dotted paths requires xunit1.
"""

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal
from xml.etree import ElementTree as ET

MAX_XML_BYTES = 2_000_000
MAX_TESTS = 10_000
Outcome = Literal["passed", "failed", "error", "skipped"]
FactKind = Literal[
    "fails_before_passes_after",
    "passes_before_and_after",
    "fails_after",
    "not_collected_after",
    "skipped",
    "unknown",
]
UnknownReason = Literal[
    "before_run_missing", "before_test_missing", "before_report_problem", "after_report_problem"
]


@dataclass(frozen=True)
class RunProblem:
    """Machine-readable report diagnostic, optionally tied to a test id."""

    code: str
    test_id: str | None = None


@dataclass(frozen=True)
class TestRun:
    """Immutable outcomes; any problem makes the report unsuitable for comparisons.

    A call failure and teardown error can produce two JUnit cases for one pytest
    node id. That pair becomes error; other duplicates are reported as ambiguous.
    Suite counters are ignored: pytest counts phases differently from node ids.
    """

    outcomes: Mapping[str, Outcome]
    problems: tuple[RunProblem, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcomes", MappingProxyType(dict(self.outcomes)))
        object.__setattr__(self, "problems", tuple(self.problems))


@dataclass(frozen=True)
class TestFact:
    """Errors count as failures, while before/after retain their distinct outcomes.

    before_test_missing can mean a new file, a renamed test or a deselection;
    JUnit alone cannot establish which. It never proves failure on main.
    """

    test_id: str
    kind: FactKind
    before: Outcome | None
    after: Outcome | None
    reason: UnknownReason | None = None


def _problem(code: str) -> TestRun:
    return TestRun({}, (RunProblem(code),))


def _path_ok(path: str) -> bool:
    return (
        path.endswith(".py")
        and not path.startswith("/")
        and not any(part in {"", ".", ".."} for part in path.split("/"))
        and "\\" not in path
        and ":" not in path
    )


def _test_id(case: ET.Element) -> str | None:
    name, classname = case.get("name", ""), case.get("classname", "")
    # Collection errors/skips are file node ids, as in the existing runner.
    if not classname and any(child.tag in {"error", "skipped"} for child in case):
        path = case.get("file")
        if path is not None:
            return path if _path_ok(path) and name == path[:-3].replace("/", ".") else None
        if _path_ok(name):
            return name
        parts = name.split(".")
        path = "/".join(parts) + ".py"
        if (parts[-1].startswith("test_") or parts[-1].endswith("_test")) and _path_ok(path):
            return path
        return None
    if not name or not classname:
        return None
    path = case.get("file")
    if path is not None:
        if not _path_ok(path):
            return None
        module = path[:-3].replace("/", ".")
        if classname == module:
            classes: list[str] = []
        elif classname.startswith(module + "."):
            classes = classname[len(module) + 1 :].split(".")
        else:
            return None
    else:
        parts = classname.split(".")
        # The last conventional module component must be followed only by classes.
        boundaries = [
            i
            for i, part in enumerate(parts)
            if (part.startswith("test_") or part.endswith("_test"))
            and all(c.startswith("Test") for c in parts[i + 1 :])
        ]
        if len(boundaries) != 1:
            return None
        end = boundaries[0] + 1
        path, classes = "/".join(parts[:end]) + ".py", parts[end:]
        if not _path_ok(path):
            return None
    if any(not c or "::" in c for c in classes):
        return None
    # Do not split the name: parameter ids can themselves contain dots or ::.
    return "::".join([path, *classes, name])


def _outcome(case: ET.Element) -> Outcome:
    tags = {child.tag for child in case}
    if "error" in tags:
        return "error"
    if "failure" in tags:
        return "failed"
    if "skipped" in tags:
        return "skipped"
    return "passed"


def _structure_ok(root: ET.Element) -> bool:
    allowed = {
        "testsuites": {"testsuite"},
        "testsuite": {"testsuite", "testcase", "properties", "system-out", "system-err"},
        "testcase": {"failure", "error", "skipped", "properties", "system-out", "system-err"},
        "properties": {"property"},
    }
    return root.tag in {"testsuites", "testsuite"} and all(
        child.tag in allowed.get(parent.tag, set()) for parent in root.iter() for child in parent
    )


def parse_junit(xml_text: str) -> TestRun:
    """Read pytest 8 JUnit without I/O; reject invalid input with problems, not raises.

    Bounds are inclusive, in UTF-8 bytes and testcase elements (including duplicates).
    Declaration-like DOCTYPE/ENTITY text is refused even inside comments or logs;
    predefined/numeric XML escapes remain supported. No partial oversized report.
    Xfail is skipped; non-strict xpass is passed (JUnit loses its xpass marker);
    strict xpass is failed. Setup/teardown/collection errors remain error.
    """
    if len(xml_text) > MAX_XML_BYTES:
        return _problem("input_too_large")
    try:
        if len(xml_text.encode("utf-8")) > MAX_XML_BYTES:
            return _problem("input_too_large")
        if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", xml_text, re.IGNORECASE):
            return _problem("forbidden_declaration")
        root = ET.fromstring(xml_text)
    except (ET.ParseError, ValueError):
        return _problem("invalid_xml")
    if sum(1 for _ in root.iter("testcase")) > MAX_TESTS:
        return _problem("too_many_tests")
    if not _structure_ok(root):
        return _problem("invalid_structure")
    outcomes: dict[str, Outcome] = {}
    seen: dict[str, list[Outcome]] = {}
    problems: list[RunProblem] = []
    for case in root.iter("testcase"):
        test_id = _test_id(case)
        if test_id is None:
            problems.append(RunProblem("ambiguous_test_id"))
            continue
        outcome = _outcome(case)
        previous = seen.setdefault(test_id, [])
        if previous:
            if len(previous) == 1 and {previous[0], outcome} == {"failed", "error"}:
                outcome = "error"
            else:
                problems.append(RunProblem("duplicate_test_id", test_id))
        previous.append(outcome)
        outcomes[test_id] = outcome
    return TestRun(outcomes, tuple(problems))


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
    elif new in {"failed", "error"}:
        kind = "fails_after"
    elif new == "skipped":
        kind = "skipped"
    elif before is None:
        kind, reason = "unknown", "before_run_missing"
    elif before.problems:
        kind, reason = "unknown", "before_report_problem"
    elif old is None:
        kind, reason = "unknown", "before_test_missing"
    elif old == "skipped":
        kind = "skipped"
    elif old in {"failed", "error"}:
        kind = "fails_before_passes_after"
    else:
        kind = "passes_before_and_after"
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
