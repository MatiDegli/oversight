"""Canonicalize narrow assertion rewrites without executing test code."""

import ast
import copy
from collections import Counter

from oversight.changes._shortcuts_common import FuncNode, Source, walk_local

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


def normalize_assertions(source: Source) -> Source:
    """Keep original locations and scoped bindings on the copied syntax nodes."""
    source = copy.deepcopy(source)
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
        _SplitAnd().visit(func)
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
