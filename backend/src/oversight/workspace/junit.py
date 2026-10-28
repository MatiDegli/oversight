"""Bounded pure JUnit parser shared by suite execution and before/after evidence."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal
from xml.etree import ElementTree as ET

MAX_XML_BYTES = 2_000_000
MAX_TESTS = 10_000
Outcome = Literal["passed", "failed", "error", "skipped"]


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
