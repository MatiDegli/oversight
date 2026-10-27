"""Explicit uncertainty for collection changes and unsupported syntax comparisons."""

import ast
from collections import Counter
from pathlib import PurePosixPath

from oversight.changes._shortcuts_common import Finding, ShortcutKind, Source, is_test_file
from oversight.changes._shortcuts_tests import parametrize_parts

COLLECTION_CONFIG = frozenset(
    {
        "pytest.ini",
        ".pytest.ini",
        "pytest.toml",
        ".pytest.toml",
        "pyproject.toml",
        "setup.cfg",
        "tox.ini",
    }
)


def unknown(path: str, reason: str, line: int = 1) -> Finding:
    return Finding(path, line, ShortcutKind.UNKNOWN, f"Unknown: {reason}; please review.")


def _unsupported(source: Source, conftest: bool) -> Counter[tuple[str, str]]:
    reasons: Counter[tuple[str, str]] = Counter()
    decorators = {
        dec for definition in source.definitions.values() for dec in definition.decorator_list
    }
    for node in ast.walk(source.tree):
        reason = ""
        if (
            isinstance(node, ast.Call)
            and source.resolve(node.func) == "pytest.mark.parametrize"
            and node not in decorators
            and parametrize_parts(node, source) is None
        ):
            reason = "non-literal parameter table or names cannot be compared"
        if (
            isinstance(node, ast.Call)
            and conftest
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_marker"
        ):
            reason = "conftest collection markers cannot be compared"
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Call)) and any(
            isinstance(n, ast.Name) and n.id in {"collect_ignore", "collect_ignore_glob"}
            for n in ast.walk(node)
        ):
            reason = "collection exclusions cannot be compared"
        if (
            conftest
            and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name
            in {
                "pytest_ignore_collect",
                "pytest_collect_file",
                "pytest_pycollect_makeitem",
                "pytest_collection_modifyitems",
                "pytest_collection",
                "pytest_collect_directory",
            }
        ):
            reason = "conftest collection hook cannot be compared"
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if node in source.tree.body and any(
                isinstance(t, ast.Name) and t.id in source.tests for t in targets
            ):
                reason = "test rebinding cannot be compared"
            if node.value is not None and source.resolve(node.value) in {"pytest.mark", "pytest"}:
                reason = "dynamic pytest alias cannot be compared"
        if reason:
            reasons[reason, ast.dump(node)] += 1
    duplicates = [qual for qual in source.tests if "#" in qual]
    if duplicates:
        reasons["duplicate test definitions cannot be compared", repr(duplicates)] += 1
    return reasons


def _parameter_unknown(source: Source, previous: Source | None, matched: set[str]) -> set[str]:
    reasons: set[str] = set()
    for owner, definition in source.definitions.items():
        for call in definition.decorator_list:
            if (
                not isinstance(call, ast.Call)
                or source.resolve(call.func) != "pytest.mark.parametrize"
                or parametrize_parts(call, source) is not None
            ):
                continue
            cases = call.args[1] if len(call.args) >= 2 else None
            added = owner not in matched and (previous is None or owner not in previous.definitions)
            names = (
                {n.id for n in ast.walk(cases) if isinstance(n, ast.Name)} if cases else set[str]()
            )
            bound = source.scopes[source.tree].keys() | {"range", "list", "tuple", "sorted", "len"}
            if added and names and names <= bound:
                continue  # Pure additions using bound tables have no rows to compare.
            if isinstance(cases, ast.Name):
                # A new test has no earlier rows to lose. Its table can remain
                # opaque without making a claim about a comparison we never made.
                if added:
                    continue
                reason = source.tables.reason(cases.id)
                if cases.id in source.tables.values:
                    reason = (
                        f"named parameter table '{cases.id}' has a shadowed binding"
                        if source.table_value(cases) is None
                        else "non-literal parameter names cannot be compared"
                    )
                reasons.add(reason + "; rows cannot be compared")
            else:
                reasons.add("non-literal parameter table or names cannot be compared")
    return reasons


def detect_unknown(
    path: str, old: Source | None, new: Source | None, matched: set[str]
) -> list[Finding]:
    if not is_test_file(path):
        return []
    conftest = PurePosixPath(path).name == "conftest.py"
    before = _unsupported(old, conftest) if old else Counter[tuple[str, str]]()
    after = _unsupported(new, conftest) if new else Counter[tuple[str, str]]()
    parameters = (_parameter_unknown(old, new, matched) if old else set[str]()) | (
        _parameter_unknown(new, old, matched) if new else set[str]()
    )
    # Removing opaque collection constructs can matter. Parameter comparisons
    # remain unknown even for stable opaque tables on an unrelated edit: their
    # rows were not compared, so the gate cannot report a complete analysis.
    return [
        unknown(path, reason)
        for reason in sorted({r for r, _ in (before - after) + (after - before)} | parameters)
    ]
