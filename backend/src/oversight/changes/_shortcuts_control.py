"""Recognize syntax that prevents an assertion from failing its test."""

import ast
from collections import Counter
from typing import cast

from oversight.changes._shortcuts_common import Finding, FuncNode, ShortcutKind, Source


def _catches_assertion(node: ast.expr | None, source: Source) -> bool:
    if node is None:
        return True
    if isinstance(node, ast.Tuple):
        return any(_catches_assertion(item, source) for item in node.elts)
    return source.resolve(node) in {"AssertionError", "Exception", "BaseException"}


def _neutralized(func: FuncNode, source: Source) -> Counter[str]:
    result: Counter[str] = Counter()

    def block(statements: list[ast.stmt], disabled: bool) -> None:
        stopped = disabled
        for stmt in statements:
            visit(stmt, stopped)
            if isinstance(stmt, (ast.Return, ast.Raise)):
                stopped = True

    def visit(node: ast.AST, disabled: bool) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            return
        if isinstance(node, ast.Assert) and disabled:
            result[ast.dump(node.test)] += 1
        if isinstance(node, ast.Try):
            caught = any(_catches_assertion(h.type, source) for h in node.handlers)
            block(node.body, disabled or caught)
            for handler in node.handlers:
                block(handler.body, disabled)
            block(node.orelse, disabled)
            block(node.finalbody, disabled)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            suppressed = any(
                isinstance(item.context_expr, ast.Call)
                and source.resolve(item.context_expr.func) == "contextlib.suppress"
                and any(_catches_assertion(arg, source) for arg in item.context_expr.args)
                for item in node.items
            )
            block(node.body, disabled or suppressed)
        elif isinstance(node, ast.If):
            literal = node.test.value if isinstance(node.test, ast.Constant) else None
            block(node.body, disabled or literal is False)
            block(node.orelse, disabled or literal is True)
        else:
            # Keep statement order inside loops/branches, so a prior return
            # disables checks in that block, never in its sibling branch.
            for _field, value in ast.iter_fields(node):
                if (
                    isinstance(value, list)
                    and value
                    and all(isinstance(n, ast.stmt) for n in cast(list[object], value))
                ):
                    block(cast(list[ast.stmt], value), disabled)
                elif isinstance(value, ast.AST):
                    visit(value, disabled)

    block(func.body, False)
    return result


def detect_control(
    path: str, before: FuncNode, after: FuncNode, old: Source, new: Source
) -> list[Finding]:
    added = _neutralized(after, new) - _neutralized(before, old)
    if not added:
        return []
    return [
        Finding(
            path,
            after.lineno,
            ShortcutKind.ASSERTION_LOOSENED,
            "Please review: control flow now disables or suppresses an assertion.",
        )
    ]
