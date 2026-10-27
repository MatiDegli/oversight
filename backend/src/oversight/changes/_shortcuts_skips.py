"""Recognize skip markers using the shared lexical name resolver."""

import ast
from collections import Counter

from oversight.changes._shortcuts_common import Finding, ShortcutKind, Source, walk_local
from oversight.changes._shortcuts_tests import TestRef

DECORATOR_SKIP = frozenset(
    {
        "pytest.mark.skip",
        "pytest.mark.skipif",
        "pytest.mark.xfail",
        "unittest.skip",
        "unittest.skipIf",
        "unittest.skipUnless",
        "unittest.expectedFailure",
    }
)
CALL_SKIP = frozenset({"pytest.skip", "pytest.xfail"})


def _decorators(source: Source, decorators: list[ast.expr]) -> list[tuple[str, int]]:
    return [
        (name, dec.lineno) for dec in decorators if (name := source.resolve(dec)) in DECORATOR_SKIP
    ]


def _parameter_markers(source: Source, node: ast.AST) -> list[tuple[str, int]]:
    markers: list[tuple[str, int]] = []
    for call in ast.walk(node):
        if not isinstance(call, ast.Call) or source.resolve(call.func) != "pytest.param":
            continue
        for kw in call.keywords:
            if kw.arg == "marks":
                values = (
                    list(kw.value.elts)
                    if isinstance(kw.value, (ast.List, ast.Tuple))
                    else [kw.value]
                )
                markers.extend(_decorators(source, values))
    return markers


def _definition_markers(source: Source, qual: str) -> list[tuple[str, int]]:
    node = source.definitions[qual]
    markers = _decorators(source, node.decorator_list)
    for dec in node.decorator_list:
        markers.extend(_parameter_markers(source, dec))
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for child in walk_local(node):
            if isinstance(child, ast.Call) and (name := source.resolve(child.func)) in CALL_SKIP:
                markers.append((name, child.lineno))
            if isinstance(child, ast.Call) and source.resolve(child.func) == "pytest.param":
                markers.extend(_parameter_markers(source, child))
    return sorted(set(markers), key=lambda marker: (marker[1], marker[0]))


def _module_markers(source: Source) -> list[tuple[str, int]]:
    markers: set[tuple[str, int]] = set()
    stack: list[ast.AST] = list(source.tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Call) and source.resolve(node.func) == "pytest.param":
            markers.update(_parameter_markers(source, node))
        if (
            isinstance(node, ast.Call)
            and (name := source.resolve(node.func)) in CALL_SKIP
            and any(
                kw.arg == "allow_module_level"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is True
                for kw in node.keywords
            )
        ):
            markers.add((name, node.lineno))
        value = None
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets)
        ) or (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "pytestmark"
        ):
            value = node.value
        if value is not None:
            # Count outer markers only: a Call and its Attribute are one marker.
            values = list(value.elts) if isinstance(value, (ast.List, ast.Tuple)) else [value]
            markers.update(_decorators(source, values))
        stack.extend(ast.iter_child_nodes(node))
    return sorted(markers, key=lambda marker: (marker[1], marker[0]))


def detect_skips(
    path: str,
    old: Source | None,
    new: Source,
    old_sources: dict[str, Source],
    matches: dict[TestRef, TestRef],
) -> list[Finding]:
    pairs: list[tuple[list[tuple[str, int]], list[tuple[str, int]]]] = []
    for qual in new.definitions:
        previous = old
        old_qual = qual
        if (path, qual) in matches:
            old_path, old_qual = matches[path, qual]
            previous = old_sources[old_path]
        before = (
            _definition_markers(previous, old_qual)
            if previous and old_qual in previous.definitions
            else []
        )
        pairs.append((before, _definition_markers(new, qual)))
    pairs.append((_module_markers(old) if old else [], _module_markers(new)))
    findings: list[Finding] = []
    for before, after in pairs:
        counts = Counter(name for name, _line in before)
        for name, line in after:
            if counts[name]:
                counts[name] -= 1
                continue
            findings.append(
                Finding(
                    path,
                    line,
                    ShortcutKind.SKIP_ADDED,
                    f"Please review: a skip or xfail ('{name}') was added, "
                    "so this test may no longer run.",
                )
            )
    return findings
