"""Compare effective numeric approx tolerances; never execute expressions."""

import ast
import copy
import math
from typing import cast

from oversight.changes._shortcuts_common import Source


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


def _effective(call: ast.Call) -> tuple[float, ...] | None:
    if not call.args:
        return None
    expected = _expected_numbers(call.args[0])
    options = dict(
        [
            *zip(("rel", "abs"), call.args[1:3], strict=False),
            *((kw.arg, kw.value) for kw in call.keywords if kw.arg in {"rel", "abs"}),
        ]
    )
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
    if expected is None:
        return None
    return tuple(max(rel * abs(number), absolute) for number in expected)


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
            tolerances.append(_effective(current))
            assert isinstance(copied, ast.Call)
            copied.func = ast.Name(id="@pytest.approx", ctx=ast.Load())
            copied.args = copied.args[:1] + copied.args[3:]
            copied.keywords = [kw for kw in copied.keywords if kw.arg not in {"rel", "abs"}]
        return copied

    # Assert diagnostic messages have already been removed from the comparison.
    expression = node.test if isinstance(node, ast.Assert) else node
    return ast.dump(clone(expression)), tolerances
