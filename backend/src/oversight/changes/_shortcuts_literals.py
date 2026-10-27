"""Find explicit test inputs and new production branches on those literals.

Fixture return values are deliberately not tracked as inputs.
"""

import ast

from oversight.changes._shortcuts_asserts import is_assert_call
from oversight.changes._shortcuts_common import (
    Finding,
    ShortcutKind,
    Source,
)
from oversight.changes._shortcuts_skips import CALL_SKIP, DECORATOR_SKIP
from oversight.changes._shortcuts_tests import parametrize_parts


def _literal_key(value: object) -> tuple[str, object] | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (str, int, float)):
        return (type(value).__name__, value)
    return None


def _literal_value_of(node: ast.expr) -> tuple[str, object] | None:
    if isinstance(node, ast.Constant):
        return _literal_key(node.value)
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, (ast.USub, ast.UAdd))
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, (int, float))
    ):
        if isinstance(node.operand.value, bool):
            return None
        value = node.operand.value
        signed = -value if isinstance(node.op, ast.USub) else value
        return (type(signed).__name__, signed)
    return None


def collect_test_literals(source: Source) -> set[tuple[str, object]]:
    literals: set[tuple[str, object]] = set()
    # Traverse calls on either side of comparisons. Literal expected values are
    # not call arguments, so they never need to hide a whole comparison side.
    excluded: set[ast.AST] = set()
    for node in ast.walk(source.tree):
        if not isinstance(node, ast.Call) or node in excluded:
            continue
        resolved = source.resolve(node.func)
        if resolved == "pytest.mark.parametrize":
            parts = parametrize_parts(node, source)
            if parts:
                for case in parts.cases.elts:
                    excluded.update(ast.walk(case))
                    for idx, element in enumerate(parts.elements(case, source)):
                        if idx not in parts.expected:
                            literals.update(_literals_in_expr(element))
            continue
        if (
            resolved in DECORATOR_SKIP
            or resolved in CALL_SKIP
            or resolved in {"pytest.approx", "pytest.raises", "pytest.param"}
        ):
            excluded.update(ast.walk(node))
            continue
        if is_assert_call(node, source):
            # First argument to unittest assertions is the actual expression;
            # nested calls there are handled by this same traversal.
            for arg in node.args[1:]:
                excluded.update(ast.walk(arg))
            continue
        for arg in [*node.args, *[kw.value for kw in node.keywords]]:
            literals.update(_literals_in_expr(arg))
    return literals


def _literals_in_expr(node: ast.AST) -> list[tuple[str, object]]:
    found: list[tuple[str, object]] = []
    seen: set[tuple[str, object]] = set()

    def visit(current: ast.AST) -> None:
        if isinstance(current, ast.expr):
            value = _literal_value_of(current)
            if value is not None:
                if value not in seen:
                    seen.add(value)
                    found.append(value)
                return
        for child in ast.iter_child_nodes(current):
            visit(child)

    visit(node)
    return found


def detect_literal_branches(
    path: str,
    old_tree: ast.Module | None,
    new_tree: ast.Module,
    test_literals: set[tuple[str, object]],
) -> list[Finding]:
    if not test_literals:
        return []
    old_dumps: set[str] = set()
    if old_tree is not None:
        old_dumps = {ast.dump(n) for n in ast.walk(old_tree) if isinstance(n, ast.Compare)}
    findings: list[Finding] = []
    for node in ast.walk(new_tree):
        if not isinstance(node, (ast.If, ast.While, ast.IfExp)):
            continue
        for sub in ast.walk(node.test):
            if not isinstance(sub, ast.Compare) or ast.dump(sub) in old_dumps:
                continue
            matched: tuple[str, object] | None = None
            for key in _literals_in_expr(sub):
                if key in test_literals:
                    matched = key
                    break
            if matched is not None:
                findings.append(
                    Finding(
                        path=path,
                        line=node.lineno,
                        kind=ShortcutKind.LITERAL_BRANCH,
                        message=(
                            f"Please review: this code branches on {matched[1]!r}, "
                            "a literal also used as an input in the tests; "
                            "check it is not special-casing the test."
                        ),
                    )
                )
                break
    return findings
