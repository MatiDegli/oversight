"""Find explicit test inputs and new production branches on those literals.

Fixture return values are deliberately not tracked as inputs.
"""

import ast

from oversight.changes._shortcuts_asserts import is_assert_call
from oversight.changes._shortcuts_common import (
    Finding,
    ShortcutKind,
    Source,
    is_literal_like,
)
from oversight.changes._shortcuts_skips import CALL_SKIP, DECORATOR_SKIP
from oversight.changes._shortcuts_tests import parametrize_parts


def _literal_key(value: object) -> tuple[str, object] | None:
    if isinstance(value, bool) or value is None or value in (0, 1, -1, ""):
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
        return _literal_key(signed)
    return None


def collect_test_literals(source: Source, root: ast.AST | None = None) -> set[tuple[str, object]]:
    literals: set[tuple[str, object]] = set()
    # Traverse calls on either side of comparisons. Literal expected values are
    # not call arguments, so they never need to hide a whole comparison side.
    excluded: set[ast.AST] = set()
    for node in ast.walk(root or source.tree):
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


type TestCase = tuple[set[tuple[str, object]], set[str]]


def collect_test_cases(source: Source) -> list[TestCase]:
    """Keep each test's inputs beside its literal expectations, never file-wide."""
    cases: list[TestCase] = []
    for func in source.tests.values():
        expected: set[str] = set()
        for node in ast.walk(func):
            if isinstance(node, ast.Assert) and isinstance(node.test, ast.Compare):
                check = node.test
                if len(check.ops) == 1 and isinstance(check.ops[0], ast.Eq):
                    expected.update(
                        ast.dump(value)
                        for value in [check.left, *check.comparators]
                        if is_literal_like(value)
                    )
            elif isinstance(node, ast.Call) and is_assert_call(node, source):
                name = source.resolve(node.func)
                if name and name.endswith((".assertEqual", ".assertEquals")):
                    expected.update(ast.dump(a) for a in node.args[:2] if is_literal_like(a))
        cases.append((collect_test_literals(source, func), expected))
        for dec in func.decorator_list:
            parts = parametrize_parts(dec, source) if isinstance(dec, ast.Call) else None
            if parts is None or not parts.expected:
                continue
            for case in parts.cases.elts:
                values = parts.elements(case, source)
                inputs = {
                    key
                    for i, value in enumerate(values)
                    if i not in parts.expected
                    for key in _literals_in_expr(value)
                }
                outputs = {ast.dump(v) for i, v in enumerate(values) if i in parts.expected}
                cases.append((inputs, outputs))
    return cases


def _branch_values(node: ast.If | ast.While | ast.IfExp) -> set[str]:
    roots = [node.body] if isinstance(node, ast.IfExp) else node.body
    values: set[str] = set()
    for root in roots:
        stack: list[ast.AST] = [root]
        while stack:
            child = stack.pop()
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            stack.extend(ast.iter_child_nodes(child))
            if (
                isinstance(child, (ast.Return, ast.Assign, ast.AnnAssign))
                and child.value is not None
                and is_literal_like(child.value)
            ):
                values.add(ast.dump(child.value))
        if isinstance(root, ast.expr) and is_literal_like(root):
            values.add(ast.dump(root))
    return values


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
    old_compares: set[str],
    new_tree: ast.Module,
    test_cases: list[TestCase],
) -> list[Finding]:
    if not test_cases:
        return []
    findings: list[Finding] = []
    for node in ast.walk(new_tree):
        if not isinstance(node, (ast.If, ast.While, ast.IfExp)):
            continue
        for sub in ast.walk(node.test):
            if not isinstance(sub, ast.Compare) or ast.dump(sub) in old_compares:
                continue
            matched: tuple[str, object] | None = None
            for key in _literals_in_expr(sub):
                if any(
                    key in inputs and _branch_values(node) & expected
                    for inputs, expected in test_cases
                ):
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
                            "a test input, and returns or assigns that test's expected value; "
                            "check it is not special-casing the test."
                        ),
                    )
                )
                break
    return findings
