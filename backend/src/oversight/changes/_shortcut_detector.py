"""A pure, deterministic shortcut detector for agent diffs; stdlib only.

Each change is ``(path, old, new)``; None denotes a missing version. Findings
ask for review: legitimate changes can trigger them, and a human decides.

Known limits: this is syntax review, not semantic equivalence or pytest execution.
Fixture return values, dynamically constructed parameter tables and nested-scope
parameter renames are not tracked. Only explicit call inputs and non-expected
parametrize columns count as test inputs. Custom collection/inheritance, dynamic
framework aliases, overlapping test renames, API/helper rewrites and complex
expected expressions can need review even when behavior is preserved. Callable/with raises
checks compare the exception and match pattern, not callable arguments or pytest
check= predicates. Non-test function moves/renames can re-report old placeholders.
Unknown runtime approx values cannot prove a tolerance wider. Historical Python 2
syntax is reported as a parse error. This module is not wired into the manager.
"""

import ast
from collections.abc import Iterable

from oversight.changes._shortcuts_asserts import detect_assertions
from oversight.changes._shortcuts_common import Change, Source, is_test_file
from oversight.changes._shortcuts_common import Finding as Finding
from oversight.changes._shortcuts_common import ShortcutKind as ShortcutKind
from oversight.changes._shortcuts_literals import (
    TestCase,
    collect_test_cases,
    detect_literal_branches,
)
from oversight.changes._shortcuts_skips import detect_skips
from oversight.changes._shortcuts_stubs import detect_stubs
from oversight.changes._shortcuts_tests import TestRef, detect_parametrize_expected, match_tests


def _parse_sources(
    change_list: list[Change],
) -> tuple[dict[str, Source], dict[str, Source], set[str], list[Finding]]:
    findings: list[Finding] = []
    old_sources: dict[str, Source] = {}
    new_sources: dict[str, Source] = {}
    failed: set[str] = set()
    for path, old, new in change_list:
        for text, sources in ((old, old_sources), (new, new_sources)):
            if text is None:
                continue
            try:
                sources[path] = Source(text)
            except (SyntaxError, ValueError) as exc:
                if path not in failed:
                    findings.append(
                        Finding(
                            path,
                            (exc.lineno or 1) if isinstance(exc, SyntaxError) else 1,
                            ShortcutKind.PARSE_ERROR,
                            "Please review: this file does not parse, "
                            "so the other checks could not run on it.",
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
) -> list[Finding]:
    findings: list[Finding] = []
    if is_test_file(path):
        if old:
            findings.extend(detect_assertions(path, old, new))
            findings.extend(detect_parametrize_expected(path, old, new))
        for (new_path, new_qual), (old_path, old_qual) in matches.items():
            if new_path == path and (old_path, old_qual) != (path, new_qual):
                findings.extend(
                    detect_assertions(path, old_sources[old_path], new, [(old_qual, new_qual)])
                )
                findings.extend(
                    detect_parametrize_expected(
                        path, old_sources[old_path], new, [(old_qual, new_qual)]
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


def detect_shortcuts(changes: Iterable[Change]) -> list[Finding]:
    """Return findings sorted by path, line and kind, without side effects."""
    change_list = sorted(changes, key=lambda change: change[0])
    old_sources, new_sources, failed, findings = _parse_sources(change_list)
    moved = _exact_moves(change_list, old_sources, new_sources)
    removed, matches = match_tests(old_sources, new_sources)
    findings.extend(removed)
    for path, old, new in change_list:
        if (
            new is None
            and old is not None
            and is_test_file(path)
            and path not in moved.values()
            and path not in failed
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
        old = old_sources.get(moved.get(path, path))
        if old is not None and old.text == new.text:
            continue
        findings.extend(_compare_file(path, old, new, old_sources, matches, inputs, old_compares))
    return _combine_findings(findings)
