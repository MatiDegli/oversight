"""Explicit uncertainty for collection changes and unsupported syntax comparisons."""

import ast
from collections import Counter
from pathlib import PurePosixPath

from oversight.changes._shortcuts_common import Finding, ShortcutKind, Source, is_test_file
from oversight.changes._shortcuts_tests import parametrize_parts

COLLECTION_CONFIG = frozenset({"pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini"})


def unknown(path: str, reason: str, line: int = 1) -> Finding:
    return Finding(path, line, ShortcutKind.UNKNOWN, f"Unknown: {reason}; please review.")


def _unsupported(source: Source, conftest: bool) -> Counter[tuple[str, str]]:
    reasons: Counter[tuple[str, str]] = Counter()
    for node in ast.walk(source.tree):
        reason = ""
        if isinstance(node, ast.Call):
            resolved = source.resolve(node.func)
            if resolved == "pytest.mark.parametrize" and parametrize_parts(node, source) is None:
                reason = "non-literal parameter table or names cannot be compared"
            elif (
                conftest and isinstance(node.func, ast.Attribute) and node.func.attr == "add_marker"
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


def detect_unknown(path: str, old: Source | None, new: Source | None) -> list[Finding]:
    if not is_test_file(path):
        return []
    conftest = PurePosixPath(path).name == "conftest.py"
    before = _unsupported(old, conftest) if old else Counter[tuple[str, str]]()
    after = _unsupported(new, conftest) if new else Counter[tuple[str, str]]()
    # Even removal of an opaque construct can change collection. Stable opaque
    # setup is a documented limit, rather than a new fact on every unrelated edit.
    return [
        unknown(path, reason)
        for reason in sorted({r for r, _ in (before - after) + (after - before)})
    ]
