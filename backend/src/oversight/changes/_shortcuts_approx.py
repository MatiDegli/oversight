"""Compare effective numeric approx tolerances; never execute expressions."""

import ast
import copy
import math
from typing import cast

from oversight.changes._shortcuts_common import Source
from oversight.changes._shortcuts_numeric import numeric_domain


def _number(node: ast.expr) -> float | None:
    try:
        value = ast.literal_eval(node)
        if isinstance(value, (float, int)) and not isinstance(value, bool):
            result = float(value)
            return result if math.isfinite(result) else None
    except (ValueError, TypeError, SyntaxError, OverflowError):
        pass
    return None


def _expected_numbers(node: ast.expr) -> tuple[float, ...] | None:
    number = _number(node)
    if number is not None:
        return (number,)
    if isinstance(node, (ast.List, ast.Tuple, ast.Dict)):
        values = node.values if isinstance(node, ast.Dict) else node.elts
        numbers = [_expected_numbers(value) for value in values]
        if all(value is not None for value in numbers):
            return tuple(number for value in numbers if value is not None for number in value)
    return None


def _coefficients(call: ast.Call) -> tuple[float, float] | None:
    options = dict(
        [
            *zip(("rel", "abs"), call.args[1:3], strict=False),
            *((kw.arg, kw.value) for kw in call.keywords if kw.arg in {"rel", "abs"}),
        ]
    )
    # Explicit None has the same meaning as an omitted tolerance in pytest.
    options = {
        key: value
        for key, value in options.items()
        if not (isinstance(value, ast.Constant) and value.value is None)
    }
    # pytest uses only the absolute tolerance when abs is explicitly supplied
    # and rel is omitted. Its default relative tolerance no longer applies.
    rel, absolute = (0.0 if "abs" in options and "rel" not in options else 1e-6), 1e-12
    for key, value in options.items():
        number = _number(value)
        if number is None or number < 0:
            return None
        if key == "rel":
            rel = number
        else:
            absolute = number
    return rel, absolute


def _effective(call: ast.Call, source: Source) -> tuple[float, ...] | None:
    if not call.args:
        return None
    expected = _expected_numbers(call.args[0]) or numeric_domain(call, source)
    coefficients = _coefficients(call)
    if expected is None or coefficients is None:
        return None
    rel, absolute = coefficients
    return tuple(max(rel * abs(number), absolute) for number in expected)


def symbolic_relation(before: ast.AST, after: ast.AST, old: Source, new: Source) -> str:
    """Compare coefficients monotonically when the expected value is opaque."""

    def calls(node: ast.AST, source: Source) -> list[ast.Call]:
        expression = node.test if isinstance(node, ast.Assert) else node
        return [
            n
            for n in ast.walk(expression)
            if isinstance(n, ast.Call) and source.resolve(n.func) == "pytest.approx"
        ]

    relations: list[str] = []
    for left, right in zip(calls(before, old), calls(after, new), strict=True):
        if _effective(left, old) is not None and _effective(right, new) is not None:
            continue
        a, b = _coefficients(left), _coefficients(right)
        if a is None or b is None:
            if ast.dump(left) != ast.dump(right):
                relations.append("unknown")
        elif all(y >= x for x, y in zip(a, b, strict=True)) and a != b:
            relations.append("wider")
        elif not all(y <= x for x, y in zip(a, b, strict=True)):
            relations.append("unknown")
    return "wider" if "wider" in relations else "unknown" if "unknown" in relations else "preserved"


def approx_signature(node: ast.AST, source: Source) -> tuple[str, list[tuple[float, ...] | None]]:
    """Shape excluding tolerances, and effective tolerance for each approx call.

    Unknown runtime expectations/tolerances are deliberately not declared wider.
    Their unchanged check shape still prevents a spurious assertion removal.
    """
    tolerances: list[tuple[float, ...] | None] = []

    def clone(current: ast.AST) -> ast.AST:
        copied = copy.copy(current)
        for field, value in ast.iter_fields(current):
            if isinstance(value, ast.AST):
                setattr(copied, field, clone(value))
            elif isinstance(value, list):
                setattr(
                    copied,
                    field,
                    [clone(v) if isinstance(v, ast.AST) else v for v in cast(list[object], value)],
                )
        if isinstance(current, ast.Call) and source.resolve(current.func) == "pytest.approx":
            tolerances.append(_effective(current, source))
            assert isinstance(copied, ast.Call)
            copied.func = ast.Name(id="@pytest.approx", ctx=ast.Load())
            copied.args = copied.args[:1] + copied.args[3:]
            copied.keywords = [kw for kw in copied.keywords if kw.arg not in {"rel", "abs"}]
        return copied

    # Assert diagnostic messages have already been removed from the comparison.
    expression = node.test if isinstance(node, ast.Assert) else node
    return ast.dump(clone(expression)), tolerances
