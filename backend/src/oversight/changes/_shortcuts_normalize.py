"""Canonicalize narrow assertion rewrites without executing test code."""

import ast
import copy
from collections import Counter

from oversight.changes._shortcuts_common import FuncNode, Source, walk_local
from oversight.changes._shortcuts_equivalence import canonicalize, unittest_checks

# Positional diagnostic-message slots in unittest.TestCase. Other arguments
# (places, delta, regex, callable arguments) must remain part of the check.
_MESSAGE_SLOT = {
    **dict.fromkeys(("assertTrue", "assertFalse", "assertIsNone", "assertIsNotNone"), 1),
    **dict.fromkeys(
        (
            "assertEqual",
            "assertNotEqual",
            "assertIs",
            "assertIsNot",
            "assertIn",
            "assertNotIn",
            "assertGreater",
            "assertGreaterEqual",
            "assertLess",
            "assertLessEqual",
            "assertSequenceEqual",
            "assertListEqual",
            "assertTupleEqual",
            "assertSetEqual",
            "assertDictEqual",
            "assertCountEqual",
            "assertRegex",
            "assertNotRegex",
        ),
        2,
    ),
    "assertAlmostEqual": 3,
    "assertNotAlmostEqual": 3,
}


class _ReplaceLocal(ast.NodeTransformer):
    def __init__(self, name: str, value: ast.Call) -> None:
        self.name, self.value = name, value

    def visit_Name(self, node: ast.Name) -> ast.expr:
        return self.value if node.id == self.name and isinstance(node.ctx, ast.Load) else node


class _SplitAnd(ast.NodeTransformer):
    def visit_Assert(self, node: ast.Assert) -> ast.Assert | list[ast.Assert]:
        def terms(expr: ast.expr) -> list[ast.expr]:
            if isinstance(expr, ast.BoolOp) and isinstance(expr.op, ast.And):
                return [term for value in expr.values for term in terms(value)]
            return [expr]

        return [ast.copy_location(ast.Assert(test=term), node) for term in terms(node.test)]


class _SplitTuple(ast.NodeTransformer):
    def visit_Assert(self, node: ast.Assert) -> ast.Assert | list[ast.Assert]:
        check = node.test
        if (
            isinstance(check, ast.Compare)
            and len(check.ops) == 1
            and isinstance(check.ops[0], ast.Eq)
            and isinstance(check.left, (ast.Tuple, ast.List))
            and isinstance(check.comparators[0], type(check.left))
        ):
            right = check.comparators[0]
            assert isinstance(right, (ast.Tuple, ast.List))
            if len(check.left.elts) == len(right.elts):
                return [
                    ast.copy_location(
                        ast.Assert(test=ast.Compare(left=a, ops=[ast.Eq()], comparators=[b])), node
                    )
                    for a, b in zip(check.left.elts, right.elts, strict=True)
                ]
        return node


def align_local_names(before: FuncNode, after: FuncNode) -> None:
    """Align single-bound locals with identical values, even when setup grew."""

    def assignments(func: FuncNode) -> dict[str, str]:
        nodes = walk_local(func)
        stores = Counter(
            n.id for n in nodes if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
        )
        values: dict[str, list[str]] = {}
        for node in nodes:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name) and stores[target.id] == 1:
                    values.setdefault(ast.dump(node.value), []).append(target.id)
        return {key: names[0] for key, names in values.items() if len(names) == 1}

    left, right = assignments(before), assignments(after)
    existing = {
        n.id if isinstance(n, ast.Name) else n.arg
        for n in walk_local(before)
        if isinstance(n, (ast.Name, ast.arg))
    }
    # Even a load of a global/fixture is significant: a local rename must not
    # capture it and make the old check look like a check of the new local.
    mapping = {
        name: right[key]
        for key, name in left.items()
        if key in right and (right[key] == name or right[key] not in existing)
    }
    captured = {
        right[key]
        for key, name in left.items()
        if key in right and right[key] != name and right[key] in existing
    }
    # Nested scope bindings can shadow a local; don't rewrite across them.
    if any(
        isinstance(
            n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef, ast.comprehension)
        )
        for stmt in before.body
        for n in ast.walk(stmt)
    ):
        return
    for node in walk_local(before):
        if isinstance(node, ast.Name):
            # Keep a captured fixture/global distinct even if the assertion's
            # spelling is unchanged: it no longer checks the same binding.
            node.id = f"@before:{node.id}" if node.id in captured else mapping.get(node.id, node.id)


def normalize_assertions(source: Source) -> Source:
    """Keep original locations and scoped bindings on the copied syntax nodes."""
    source = copy.deepcopy(source)
    canonicalize(source)
    for func in source.functions.values():
        local = walk_local(func)
        for node in local:
            if isinstance(node, ast.Call):
                name = source.resolve(node.func)
                last = name.rsplit(".", 1)[-1] if name else ""
                slot = _MESSAGE_SLOT.get(last)
                if slot is not None:
                    node.keywords = [kw for kw in node.keywords if kw.arg != "msg"]
                    if len(node.args) > slot:
                        node.args.pop(slot)
        unittest_checks(func)
        _SplitAnd().visit(func)
        _SplitTuple().visit(func)
    canonicalize(source)
    return source


def inline_assertion_calls(func: FuncNode) -> None:
    """Normalize extraction only after unchanged assertions have been matched."""
    local = walk_local(func)
    loads = Counter(n.id for n in local if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load))
    stores = Counter(
        n.id for n in local if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
    )
    # Only single-use calls assigned and asserted in the same straight-line
    # block. Rebinding, multiple uses and conditional assignments stay exact.
    for index, previous in enumerate(func.body):
        if (
            isinstance(previous, ast.Assign)
            and len(previous.targets) == 1
            and isinstance(target := previous.targets[0], ast.Name)
            and isinstance(previous.value, ast.Call)
            and loads[target.id] == stores[target.id] == 1
        ):
            for following in func.body[index + 1 :]:
                if isinstance(following, ast.Assert) or (
                    isinstance(following, ast.Expr) and isinstance(following.value, ast.Call)
                ):
                    _ReplaceLocal(target.id, previous.value).visit(following)
