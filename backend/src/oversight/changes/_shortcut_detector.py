"""A pure, deterministic shortcut detector for agent diffs; stdlib only.

Each change is ``(path, old, new)``; None denotes a missing version. Findings
ask for review: legitimate changes can trigger them, and a human decides.

Policy must read [] as "no facts", never as "safe". DetectionResult.status is
"unknown" whenever a parse_error or unknown fact prevents a complete comparison;
reasons are explicit and unknown never has an empty finding list.

Known limits: syntax review is not semantic equivalence or pytest execution.
Plain binding value changes under an intact assertion are not generally tracked:
only binding capture and direct literal tautologies are recognized. The broad
rule flagged four legitimate setup changes in the repository history. Fixture
return values, added mocks, stable opaque framework aliases, nested-scope
parameter renames and arbitrary helper extraction/inlining remain untracked.
Imported parameter tables changed only in their own module, fixture params and
metafunc.parametrize are untracked. Dead-branch checks beyond literal False ifs,
class-qualified helper calls and aliased skip marks can escape comparison.
Expected columns are inferred only from a direct call == name assertion (or a
column name containing "expected"); more complex expected expressions are a limit.
Custom collection/inheritance and API rewrites can require review even when
behavior is preserved. Callable/with raises checks compare the exception and
match pattern, not callable arguments or pytest check= predicates. Non-test
function moves/renames can re-report old placeholders. Numeric domains for approx
are limited to literal rows and single-bound straight-line arithmetic; otherwise
coefficient dominance proves widening/tightening, and crossed/dynamic tolerances
are unknown. Removed parameter rows and helpers are facts, even for legitimate
coverage reorganizations. Historical Python 2 syntax is a parse error. This
module is not wired into the manager. Helper checks recognize assertions,
assert-prefixed calls and model_validate/model_validate_json results used in an
expression or returned, not bare validation statements, raise AssertionError,
pytest.fail or delegation. Validation use is syntactic: assignment counts even
if the assigned value is never read; schema types and dataflow are not verified.
"""

import ast
import time
from collections.abc import Iterable
from pathlib import PurePosixPath

from oversight.changes._shortcuts_asserts import detect_assertions
from oversight.changes._shortcuts_common import Change, Source, is_test_file
from oversight.changes._shortcuts_common import DetectionResult as DetectionResult
from oversight.changes._shortcuts_common import Finding as Finding
from oversight.changes._shortcuts_common import ShortcutKind as ShortcutKind
from oversight.changes._shortcuts_helpers import detect_helpers
from oversight.changes._shortcuts_literals import (
    TestCase,
    collect_test_cases,
    detect_literal_branches,
)
from oversight.changes._shortcuts_skips import detect_skips
from oversight.changes._shortcuts_stubs import detect_stubs
from oversight.changes._shortcuts_tests import TestRef, detect_parametrize_expected, match_tests
from oversight.changes._shortcuts_unknown import COLLECTION_CONFIG, detect_unknown, unknown


def _parse_sources(
    change_list: list[Change],
    deadline: float,
) -> tuple[dict[str, Source], dict[str, Source], set[str], list[Finding]]:
    findings: list[Finding] = []
    old_sources: dict[str, Source] = {}
    new_sources: dict[str, Source] = {}
    failed: set[str] = set()
    for path, old, new in change_list:
        if not path.endswith(".py"):
            if old != new and PurePosixPath(path).name in COLLECTION_CONFIG:
                findings.append(unknown(path, "pytest collection configuration changed"))
            continue
        for text, sources in ((old, old_sources), (new, new_sources)):
            if text is None:
                continue
            try:
                sources[path] = Source(text, deadline=deadline)
            except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
                if path not in failed:
                    findings.append(
                        Finding(
                            path,
                            (exc.lineno or 1) if isinstance(exc, SyntaxError) else 1,
                            ShortcutKind.PARSE_ERROR
                            if isinstance(exc, (SyntaxError, ValueError))
                            else ShortcutKind.UNKNOWN,
                            f"Unknown: {type(exc).__name__} while parsing this file; "
                            "the other checks could not run on it. Please review.",
                        )
                    )
                    failed.add(path)
        if path in failed:
            old_sources.pop(path, None)
            new_sources.pop(path, None)

    return old_sources, new_sources, failed, findings


def _exact_moves(
    change_list: list[Change], old_sources: dict[str, Source], new_sources: dict[str, Source]
) -> dict[str, str]:
    # Preserve class/module markers and comments, consuming each origin once.
    origins = [
        path
        for path, old, new in change_list
        if old is not None and new is None and path in old_sources
    ]
    moved: dict[str, str] = {}
    for path, old, new in change_list:
        if old is not None or new is None or path not in new_sources:
            continue
        origin = next(
            (
                p
                for p in origins
                if old_sources[p].text == new and is_test_file(p) == is_test_file(path)
            ),
            None,
        )
        if origin is not None:
            moved[path] = origin
            origins.remove(origin)

    return moved


def _compare_file(
    path: str,
    old: Source | None,
    new: Source,
    old_sources: dict[str, Source],
    matches: dict[TestRef, TestRef],
    inputs: list[TestCase],
    old_compares: set[str],
    deadline: float,
) -> list[Finding]:
    findings: list[Finding] = []
    if is_test_file(path):
        if old:
            findings.extend(detect_assertions(path, old, new, deadline=deadline))
            findings.extend(detect_parametrize_expected(path, old, new, deadline=deadline))
        grouped: dict[str, list[tuple[str, str]]] = {}
        for (new_path, new_qual), (old_path, old_qual) in matches.items():
            if new_path == path and (old_path, old_qual) != (path, new_qual):
                grouped.setdefault(old_path, []).append((old_qual, new_qual))
        for old_path, pairs in grouped.items():
            findings.extend(
                detect_assertions(path, old_sources[old_path], new, pairs, deadline=deadline)
            )
            findings.extend(
                detect_parametrize_expected(
                    path, old_sources[old_path], new, pairs, deadline=deadline
                )
            )
        findings.extend(detect_skips(path, old, new, old_sources, matches))
    else:
        findings.extend(
            detect_literal_branches(
                path,
                old_compares,
                new.tree,
                inputs,
            )
        )
    findings.extend(detect_stubs(path, old, new, old_sources, matches))
    return findings


def _combine_findings(findings: list[Finding]) -> list[Finding]:
    groups: dict[tuple[str, ShortcutKind], list[Finding]] = {}
    for finding in findings:
        groups.setdefault((finding.path, finding.kind), []).append(finding)
    combined: list[Finding] = []
    for (path, kind), group in groups.items():
        lines = sorted({f.line for f in group})
        messages = dict.fromkeys(f.message for f in sorted(group, key=lambda f: f.line))
        combined.append(
            Finding(
                path,
                lines[0],
                kind,
                " ".join(messages) + " Lines: " + ", ".join(map(str, lines)) + ".",
            )
        )
    return sorted(combined, key=lambda f: (f.path, f.line, f.kind, f.message))


def _detect(changes: Iterable[Change], deadline: float) -> list[Finding]:
    change_list = sorted(changes, key=lambda change: change[0])
    if time.monotonic() >= deadline:
        raise TimeoutError("analysis timeout")
    if sum(len(text) for _, a, b in change_list for text in (a, b) if text) > 2_000_000:
        raise TimeoutError("analysis source size cap (2 MB)")
    old_sources, new_sources, failed, findings = _parse_sources(change_list, deadline)
    moved = _exact_moves(change_list, old_sources, new_sources)
    removed, matches = match_tests(old_sources, new_sources, deadline=deadline)
    compared: dict[str, set[str]] = {}
    for refs in matches.items():
        for path, qual in refs:
            while qual:
                compared.setdefault(path, set()).add(qual)
                qual = qual.rpartition(".")[0]
    for path, before, after in change_list:
        if time.monotonic() >= deadline:
            raise TimeoutError("analysis timeout")
        if before != after:
            findings.extend(
                detect_unknown(
                    path, old_sources.get(path), new_sources.get(path), compared.get(path, set())
                )
            )
    findings.extend(removed)
    findings.extend(detect_helpers(old_sources, new_sources, matches))
    for path, old, new in change_list:
        if (
            new is None
            and old is not None
            and is_test_file(path)
            and path not in moved.values()
            and path not in failed
            and path in old_sources
            and not old_sources[path].tests
        ):
            findings.append(
                Finding(
                    path,
                    1,
                    ShortcutKind.TEST_REMOVED,
                    "Please review: a test file was deleted.",
                )
            )

    inputs = [
        case
        for path, source in new_sources.items()
        if is_test_file(path)
        for case in collect_test_cases(source)
    ]
    # A helper can move into an existing file without moving its whole source.
    # Literal comparisons already present anywhere in the baseline stay old.
    old_compares = {
        ast.dump(node)
        for source in old_sources.values()
        for node in ast.walk(source.tree)
        if isinstance(node, ast.Compare)
    }
    for path, new in new_sources.items():
        if time.monotonic() >= deadline:
            raise TimeoutError("analysis timeout")
        old = old_sources.get(moved.get(path, path))
        if old is not None and old.text == new.text:
            continue
        findings.extend(
            _compare_file(path, old, new, old_sources, matches, inputs, old_compares, deadline)
        )
        if time.monotonic() >= deadline:
            raise TimeoutError("analysis timeout")
    return _combine_findings(findings)


def detect_shortcuts(changes: Iterable[Change], *, timeout: float = 2.0) -> DetectionResult:
    """Return sorted facts and an explicit status, without running test code.

    ``[]`` means "no facts", NEVER "safe": policy must not treat it as permission
    to merge. ``status == "unknown"`` and ``reasons`` signal incomplete analysis;
    unknowns always contain a finding (parse_error or unknown). Collection config,
    opaque changed constructs, resource exhaustion and timeouts require a person.
    This synchronous function is unwired; an async caller must offload it and map
    its own timeout/failure to unknown as well. The deadline is cooperative; size
    and matching caps bound expensive work, rather than promising preemption.
    """
    try:
        return DetectionResult(_detect(changes, time.monotonic() + timeout))
    except Exception as exc:
        return DetectionResult([unknown("<analysis>", f"{type(exc).__name__}: {exc}")])
