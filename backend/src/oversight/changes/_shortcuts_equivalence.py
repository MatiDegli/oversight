"""Narrow syntax equivalences; no execution or general semantic matching."""

import ast
import copy
import string

from oversight.changes._shortcuts_common import FuncNode, Source, is_literal_like
from oversight.changes._shortcuts_tests import parametrize_parts

ALIASES = {
    "assertEquals": "assertEqual",
    "assertNotEquals": "assertNotEqual",
    "assert_": "assertTrue",
}


class Canonical(ast.NodeTransformer):
    """Canonicalize literal expectations, equality orientation and unittest aliases."""

    def __init__(self, source: Source) -> None:
        self.source = source

    def visit_Call(self, node: ast.Call) -> ast.expr:
        self.generic_visit(node)
        if (
            self.source.resolve(node.func) == "set"
            and len(node.args) == 1
            and not node.keywords
            and isinstance(node.args[0], (ast.List, ast.Tuple))
            and all(is_literal_like(e) for e in node.args[0].elts)
            and node.args[0].elts
        ):
            return ast.Set(elts=node.args[0].elts)
        if (
            not isinstance(node.func, ast.Attribute)
            or node.func.attr != "format"
            or not isinstance(node.func.value, ast.Constant)
            or not isinstance(node.func.value.value, str)
        ):
            return node
        values: list[ast.expr] = []
        keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg is not None}
        position = 0
        try:
            for text, field, spec, conversion in string.Formatter().parse(node.func.value.value):
                if text:
                    values.append(ast.Constant(text))
                if field is None:
                    continue
                if field == "":
                    field, position = str(position), position + 1
                value = node.args[int(field)] if field.isdecimal() else keywords[field]
                if spec and ("{" in spec or "}" in spec):
                    return node
                values.append(
                    ast.FormattedValue(
                        value=value,
                        conversion=ord(conversion) if conversion else -1,
                        format_spec=ast.JoinedStr(values=[ast.Constant(spec)]) if spec else None,
                    )
                )
        except (ValueError, KeyError, IndexError):
            return node
        return ast.JoinedStr(values=values)

    def visit_Attribute(self, node: ast.Attribute) -> ast.Attribute:
        self.generic_visit(node)
        node.attr = ALIASES.get(node.attr, node.attr)
        return node

    def visit_Compare(self, node: ast.Compare) -> ast.Compare:
        self.generic_visit(node)
        if len(node.ops) != 1 or not isinstance(
            node.ops[0], (ast.Eq, ast.NotEq, ast.Is, ast.IsNot)
        ):
            return node
        right = node.comparators[0]
        # Literal expectations belong on the right. For two expressions use a
        # stable syntax order, solely to cancel otherwise identical checks.
        left_literal, right_literal = is_literal_like(node.left), is_literal_like(right)
        if (left_literal and not right_literal) or (
            left_literal == right_literal and ast.dump(node.left) > ast.dump(right)
        ):
            node.left, node.comparators[0] = right, node.left
        return node


class _LocalNames(ast.NodeTransformer):
    def __init__(self, names: dict[str, str]) -> None:
        self.names = names

    def visit_Name(self, node: ast.Name) -> ast.Name:
        node.id = self.names.get(node.id, node.id)
        return node


def body_key(func: FuncNode) -> tuple[str, ...]:
    """Alpha-equivalent local assignments with unchanged fixture arguments.

    Nested scopes and parameters stay exact. Only skip the checks when the
    complete setup and assertion bodies agree, not merely their local names.
    """
    body = func.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    names: dict[str, str] = {}
    if not any(
        isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda))
        for stmt in body
        for n in ast.walk(stmt)
    ):
        for stmt in body:
            for node in ast.walk(stmt):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                    names.setdefault(node.id, f"@local{len(names)}")
    normalizer = _LocalNames(names)
    return (ast.dump(func.args), *(ast.dump(normalizer.visit(copy.deepcopy(s))) for s in body))


def canonicalize(source: Source) -> None:
    Canonical(source).visit(source.tree)


class _UnittestChecks(ast.NodeTransformer):
    """Represent standard TestCase checks as ordinary asserts for comparison."""

    def visit_Expr(self, node: ast.Expr) -> ast.AST:
        call = node.value
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
            return self.generic_visit(node)
        if not isinstance(call.func.value, ast.Name) or call.func.value.id != "self":
            return self.generic_visit(node)
        name = call.func.attr
        expr: ast.expr | None = None
        ops: dict[str, ast.cmpop] = {
            "assertEqual": ast.Eq(),
            "assertNotEqual": ast.NotEq(),
            "assertIn": ast.In(),
            "assertNotIn": ast.NotIn(),
            "assertIs": ast.Is(),
            "assertIsNot": ast.IsNot(),
        }
        if name in ops and len(call.args) == 2 and not call.keywords:
            expr = ast.Compare(left=call.args[0], ops=[ops[name]], comparators=[call.args[1]])
        elif name in {"assertTrue", "assertFalse"} and len(call.args) == 1 and not call.keywords:
            expr = (
                call.args[0]
                if name == "assertTrue"
                else ast.UnaryOp(op=ast.Not(), operand=call.args[0])
            )
        elif (
            name in {"assertIsNone", "assertIsNotNone"}
            and len(call.args) == 1
            and not call.keywords
        ):
            expr = ast.Compare(
                left=call.args[0],
                ops=[ast.Is() if name == "assertIsNone" else ast.IsNot()],
                comparators=[ast.Constant(None)],
            )
        if expr is None:
            return self.generic_visit(node)
        return ast.copy_location(ast.Assert(test=expr), node)


def unittest_checks(func: FuncNode) -> None:
    _UnittestChecks().visit(func)


def align_parameters(old: Source, new: Source, old_func: FuncNode, new_func: FuncNode) -> None:
    """Rename only columns whose explicit parameter tables are identical."""
    for before, after in zip(old_func.decorator_list, new_func.decorator_list, strict=False):
        left = parametrize_parts(before, old) if isinstance(before, ast.Call) else None
        right = parametrize_parts(after, new) if isinstance(after, ast.Call) else None
        if (
            left is None
            or right is None
            or len(left.names) != len(right.names)
            or ast.dump(left.cases) != ast.dump(right.cases)
        ):
            continue
        mapping = {a: b for a, b in zip(left.names, right.names, strict=True) if a != b}
        if not mapping:
            continue
        # Shadowing within a comprehension or nested scope stays conservative.
        if any(
            isinstance(
                n,
                (
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                    ast.ClassDef,
                    ast.Lambda,
                    ast.comprehension,
                ),
            )
            for stmt in old_func.body
            for n in ast.walk(stmt)
        ):
            continue
        _LocalNames(mapping).visit(old_func)
        for arg in [*old_func.args.posonlyargs, *old_func.args.args, *old_func.args.kwonlyargs]:
            arg.arg = mapping.get(arg.arg, arg.arg)


def singleton_tightening(before: ast.AST, after: ast.AST) -> bool:
    """Truthiness/equality narrowed to an identity with a boolean or None."""
    if not isinstance(before, ast.Assert) or not isinstance(after, ast.Assert):
        return False
    target = after.test
    if (
        not isinstance(target, ast.Compare)
        or len(target.ops) != 1
        or not isinstance(target.ops[0], ast.Is)
        or not isinstance(target.comparators[0], ast.Constant)
    ):
        return False
    value = target.comparators[0].value
    previous = before.test
    if (
        isinstance(previous, ast.Compare)
        and len(previous.ops) == 1
        and isinstance(previous.ops[0], ast.Eq)
    ):
        return (
            (value is None or type(value) is bool)
            and ast.dump(previous.left) == ast.dump(target.left)
            and ast.dump(previous.comparators[0]) == ast.dump(target.comparators[0])
        )
    if isinstance(previous, ast.UnaryOp) and isinstance(previous.op, ast.Not):
        return (value is None or value is False) and ast.dump(previous.operand) == ast.dump(
            target.left
        )
    return value is True and ast.dump(previous) == ast.dump(target.left)
