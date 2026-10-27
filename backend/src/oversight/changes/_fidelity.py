"""Check the stage split against the frozen baseline, without importing the product.

    git show fa58329:backend/src/oversight/changes/_manager.py > /tmp/cm-base.py
    .venv/bin/python backend/src/oversight/changes/_fidelity.py /tmp/cm-base.py

The adjacent JSON manifest is the entire rename/move allowlist. Comparison retains signatures,
docstrings, callbacks, awaits, lock order and exception boundaries. Scaffold (owners, ports,
compatibility properties and public forwarding methods) is reviewed separately. The built-in
negative controls must reject all three mutations on every invocation. No code generator is used
by this check; a changed base requires redoing the move and rerunning comparison and review.
"""

import ast
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
MAP: dict[str, Any] = json.loads((HERE / "_split_map.json").read_text())
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def members(tree: ast.Module, name: str) -> dict[str, Any]:
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
    return {n.name: n for n in cls.body if isinstance(n, FUNCTIONS)}


class Canonical(ast.NodeTransformer):
    """Normalize only accesses and functions declared in the manifest."""

    def __init__(self, owner: str, methods: set[str]) -> None:
        self.owner = owner
        self.methods = methods
        self.inverse = {new: old for old, new in MAP["renames"].items()}
        self.class_names = {new: old for old, new in MAP["classes"].items()}

    def visit_Name(self, node: ast.Name) -> ast.AST:
        return ast.copy_location(
            ast.Name(id=self.class_names.get(node.id, node.id), ctx=node.ctx), node
        )

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        if self.owner == "record":
            return self.generic_visit(node)
        path = ast.unparse(node)
        dest: str | None = None
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)+", path):
            return self.generic_visit(node)
        if path.startswith("self._ctx."):
            path = "ctx." + path.removeprefix("self._ctx.")
        if path.startswith("self."):
            name = path.removeprefix("self.")
            if self.owner == "support":
                path = "ctx." + name
            elif self.owner == "state":
                path = "ctx.state." + name
            elif name in MAP["attributes"]:
                dest = "ctx." + MAP["attributes"][name]
            elif name in self.methods:
                dest = "operation_" + name
            elif "." not in name:
                raise AssertionError(f"Unmapped self access: {path}")
        if any(path.startswith("ctx." + value + ".") for value in MAP["attributes"].values()):
            return self.generic_visit(node)
        if path.startswith("ctx."):
            name = path.removeprefix("ctx.")
            if name in MAP["attributes"].values():
                dest = path
            elif name.startswith("ports."):
                old = self.inverse.get(name.removeprefix("ports."))
                assert old in self.methods, f"Unmapped port: {path}"
                dest = "operation_" + str(old)
            elif name.startswith("state."):
                old = self.inverse.get(name.removeprefix("state."))
                assert old in MAP["transitions"], f"Unmapped state access: {path}"
                dest = "operation_" + str(old)
            elif "." not in name:
                old = self.inverse.get(name)
                assert old in MAP["support"], f"Unmapped context access: {path}"
                dest = "operation_" + str(old)
            elif not name.startswith(("workspace.", "main_graph.", "publish.", "clock.")):
                raise AssertionError(f"Unmapped context access: {path}")
        # Module-qualified stage functions are allowed only by their explicit stage table.
        for stage, names in MAP["stages"].items():
            if path.startswith(stage + "."):
                old = self.inverse.get(path.removeprefix(stage + "."))
                if old in names:
                    dest = "operation_" + str(old)
        if dest is not None:
            replacement = ast.parse(dest, mode="eval").body
            replacement.ctx = node.ctx  # type: ignore[attr-defined]
            return replacement
        return self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> ast.AST:
        visited = self.generic_visit(node)
        assert isinstance(visited, ast.Call)
        node = visited
        if (
            isinstance(node.func, ast.Name)
            and node.func.id.startswith("operation_")
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "ctx"
        ):
            node.args.pop(0)
        if ast.unparse(node.func) == "functools.partial":
            # A moved bound callback carries ctx explicitly; the remaining arguments are exact.
            if len(node.args) >= 2 and ast.unparse(node.args[1]) == "ctx":
                node.args.pop(1)
            if node.args and isinstance(node.args[0], ast.Call):
                inner = node.args[0]
                if ast.unparse(inner.func) == "functools.partial" and len(inner.args) == 1:
                    node.args[0] = inner.args[0]
            if len(node.args) == 1 and not node.keywords:
                return node.args[0]
        return node


def normalized(node: Any, owner: str, methods: set[str]) -> str:
    node = copy.deepcopy(node)
    normalizer = Canonical(owner, methods)
    if isinstance(node, FUNCTIONS):
        old = normalizer.inverse.get(node.name, node.name) if owner != "manager" else node.name
        node.name = old
        if node.args.args and node.args.args[0].arg in ("self", "ctx"):
            node.args.args.pop(0)
        node.decorator_list = [d for d in node.decorator_list if ast.unparse(d) != "staticmethod"]
    node = normalizer.visit(node)
    # Local calls within a stage use its declared function names.
    if owner in MAP["stages"]:
        names = {MAP["renames"][old]: old for old in MAP["stages"][owner]}

        class LocalCalls(ast.NodeTransformer):
            def visit_Name(self, node: ast.Name) -> ast.AST:
                if node.id in names:
                    return ast.Name(id="operation_" + names[node.id], ctx=node.ctx)
                return node

        node = LocalCalls().visit(node)
        node = normalizer.visit(node)
    return ast.dump(node, include_attributes=False)


def verify(baseline: ast.Module, trees: dict[str, ast.Module]) -> int:
    originals = members(baseline, "ChangeManager")
    methods = set(originals)
    count = 0
    for name, original in originals.items():
        if name == "__init__":
            continue
        owner = "manager"
        target = name
        if name in MAP["support"]:
            owner, target = "support", MAP["renames"][name]
        elif name in MAP["transitions"]:
            owner, target = "state", MAP["renames"][name]
        else:
            for stage, names in MAP["stages"].items():
                if name in names and stage in trees:
                    owner, target = stage, MAP["renames"][name]
        candidates = (
            members(
                trees[owner],
                {"manager": "ChangeManager", "support": "ChangeContext", "state": "ChangeState"}[
                    owner
                ],
            )
            if owner in ("manager", "support", "state")
            else {n.name: n for n in trees[owner].body if isinstance(n, FUNCTIONS)}
        )
        assert target in candidates, f"Missing {owner}.{target}"
        before = normalized(original, "manager", methods)
        after = normalized(candidates[target], owner, methods)
        assert before == after, f"Body/signature/callback changed: {name} -> {owner}.{target}"
        count += 1
    for old, new in MAP["classes"].items() | {"Revision": "Revision"}.items():
        original = next(n for n in baseline.body if isinstance(n, ast.ClassDef) and n.name == old)
        current = next(
            n for n in trees["state"].body if isinstance(n, ast.ClassDef) and n.name == new
        )
        current = copy.deepcopy(current)
        current.name = old
        assert normalized(original, "record", methods) == normalized(current, "record", methods)
    return count


def negative_controls(baseline: ast.Module, trees: dict[str, ast.Module]) -> None:
    for mutation in ("await", "locks", "except"):
        altered = copy.deepcopy(trees)
        if mutation == "await":

            class RemoveAwait(ast.NodeTransformer):
                changed = False

                def visit_Await(self, node: ast.Await) -> ast.AST:
                    if not self.changed:
                        self.changed = True
                        return node.value
                    return node

            RemoveAwait().visit(altered["support"])
        elif mutation == "locks":
            # Swap the first two lock sites in the method that actually contains two.
            owner = "proposal" if "proposal" in altered else "manager"
            opens = next(
                n for n in ast.walk(altered[owner]) if isinstance(n, FUNCTIONS) and n.name == "open"
            )
            locks = [n for n in ast.walk(opens) if isinstance(n, ast.AsyncWith)]
            locks[0].items, locks[1].items = locks[1].items, locks[0].items
        else:
            handler = next(
                n
                for n in ast.walk(altered["support"])
                if isinstance(n, ast.ExceptHandler)
                and n.type is not None
                and ast.unparse(n.type) == "Exception"
            )
            handler.type = ast.Name(id="BaseException", ctx=ast.Load())
        try:
            verify(baseline, altered)
        except AssertionError:
            continue
        raise AssertionError(f"Seeded mutation escaped: {mutation}")


def main() -> None:
    baseline_path = Path(sys.argv[1])
    baseline = ast.parse(baseline_path.read_text())
    trees = {
        name: ast.parse((HERE / f"_{name}.py").read_text())
        for name in ("manager", "state", "support", *MAP["stages"])
        if (HERE / f"_{name}.py").exists()
    }
    count = verify(baseline, trees)
    negative_controls(baseline, trees)
    print(f"Fidelity PASS: {count} operations, 4 records; 3/3 seeded mutations rejected")


if __name__ == "__main__":
    main()
