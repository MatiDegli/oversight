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


def _binding_values(
    func: FuncNode, *, include_plain: bool = True
) -> dict[str, list[tuple[str, str, str]]]:
    """Track each name's sources and unpacking positions, independent of lines.

    A type annotation does not change an assigned value. Iteration and context
    manager targets receive different values from a plain assignment, even
    when their source expressions agree.
    """
    result: dict[str, list[tuple[str, str, str]]] = {}

    def bind(target: ast.expr, value: ast.expr, kind: str, position: str = "") -> None:
        if isinstance(target, ast.Name):
            result.setdefault(target.id, []).append((kind, ast.dump(value), position))
        elif isinstance(target, (ast.Tuple, ast.List)):
            for index, item in enumerate(target.elts):
                bind(item, value, kind, f"{position}/{index}")
        elif isinstance(target, ast.Starred):
            bind(target.value, value, kind, f"{position}/*")

    for node in walk_local(func):
        if isinstance(node, ast.Assign):
            if include_plain:
                for target in node.targets:
                    bind(target, node.value, "value")
        elif isinstance(node, ast.AugAssign):
            bind(node.target, node.value, type(node.op).__name__)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                target = ast.Name(id=alias.asname or alias.name.split(".")[0])
                bind(target, ast.Constant(value=ast.dump(node)), "import")
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
            bind(node.target, node.value, "value")
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            bind(node.target, node.iter, type(node).__name__)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    bind(item.optional_vars, item.context_expr, type(node).__name__)
    return result


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
    # Nested scope bindings can shadow a local; don't rewrite across them.
    if any(
        isinstance(
            n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef, ast.comprehension)
        )
        for stmt in before.body
        for n in ast.walk(stmt)
    ):
        return
    # Apply safe renames to dependent binding values before comparing them.
    for node in walk_local(before):
        if isinstance(node, ast.Name):
            node.id = mapping.get(node.id, node.id)
    # Equal store counts can still replace a checked binding. Compare sources
    # per name, including annotated/walrus assignments and for/with targets.
    old_bindings, new_bindings = _binding_values(before), _binding_values(after)
    nonplain = _binding_values(after, include_plain=False)
    tautological = {
        node.test.left.id
        for node in walk_local(after)
        if isinstance(node, ast.Assert)
        and isinstance(node.test, ast.Compare)
        and len(node.test.ops) == 1
        and isinstance(node.test.ops[0], ast.Eq)
        and isinstance(node.test.left, ast.Name)
        and any(
            kind == "value" and value == ast.dump(node.test.comparators[0])
            for kind, value, _ in new_bindings.get(node.test.left.id, [])
        )
    }
    captured = {
        name
        for name in new_bindings
        if name in existing
        and new_bindings[name] != old_bindings.get(name)
        and (
            name in nonplain
            or name in tautological
            or len(new_bindings[name]) > len(old_bindings.get(name, []))
        )
    }
    for node in walk_local(before):
        if isinstance(node, ast.Name) and node.id in captured:
            # Keep a captured fixture/global distinct even if the assertion's
            # spelling is unchanged: it no longer checks the same binding.
            node.id = f"@before:{node.id}"


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
