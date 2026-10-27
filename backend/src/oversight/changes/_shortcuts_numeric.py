"""Bounded numeric domains from literal parameter rows and straight-line setup."""

import ast
import itertools
import math
from collections import Counter
from collections.abc import Callable

from oversight.changes._shortcuts_common import Source, walk_local
from oversight.changes._shortcuts_tests import parametrize_parts

_OPS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
}


def _values(expr: ast.expr, env: dict[str, tuple[float, ...]]) -> tuple[float, ...] | None:
    if (
        isinstance(expr, ast.Constant)
        and isinstance(expr.value, (int, float))
        and not isinstance(expr.value, bool)
    ):
        value = float(expr.value)
        return (value,) if math.isfinite(value) else None
    if isinstance(expr, ast.Name):
        return env.get(expr.id)
    if isinstance(expr, ast.BinOp) and type(expr.op) in _OPS:
        left, right = _values(expr.left, env), _values(expr.right, env)
        if left is not None and right is not None and len(left) * len(right) <= 100:
            try:
                result = tuple(_OPS[type(expr.op)](a, b) for a, b in itertools.product(left, right))
            except (ArithmeticError, ValueError):
                return None
            return result if all(math.isfinite(n) for n in result) else None
    return None


def numeric_domain(call: ast.Call, source: Source) -> tuple[float, ...] | None:
    owners = [
        f
        for f in source.functions.values()
        if f.lineno <= call.lineno <= (f.end_lineno or f.lineno)
    ]
    if not owners or not call.args:
        return None
    func = min(owners, key=lambda f: (f.end_lineno or f.lineno) - f.lineno)
    nodes = walk_local(func)
    stores = Counter(
        n.id for n in nodes if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
    )
    env: dict[str, tuple[float, ...]] = {}
    for dec in func.decorator_list:
        parts = parametrize_parts(dec, source) if isinstance(dec, ast.Call) else None
        if parts is None or len(parts.cases.elts) > 100:
            continue
        for i, name in enumerate(parts.names):
            values: list[float] = []
            for row in parts.cases.elts:
                elements = parts.elements(row, source)
                numbers = _values(elements[i], {}) if i < len(elements) else None
                if numbers is None:
                    break
                values.extend(numbers)
            else:
                if stores[name] == 0 and values:
                    env[name] = tuple(values)
    for stmt in func.body:
        if stmt.lineno >= call.lineno:
            break
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target = stmt.targets[0]
            if isinstance(target, ast.Name) and stores[target.id] == 1:
                value = _values(stmt.value, env)
                if value is not None:
                    env[target.id] = value
    return _values(call.args[0], env)
