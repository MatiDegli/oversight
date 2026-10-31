"""Single-binding aliases for AST-only behavior seed discovery."""

import ast

# Both helpers belong to changes; reuse the detector's conservative binding semantics.
from oversight.changes._shortcuts_common import (
    Source,
    _AliasBindings,  # pyright: ignore[reportPrivateUsage]
    _Bindings,  # pyright: ignore[reportPrivateUsage]
    check_deadline,
)


def module_aliases(source: Source) -> dict[str, str]:
    bindings = _AliasBindings(source.deadline)
    bindings.visit(source.tree)
    if bindings.star_import:
        return {}
    global_names: set[str] = set()
    for node in ast.walk(source.tree):
        check_deadline(source.deadline)
        if isinstance(node, ast.Global):
            global_names.update(node.names)
    imported: set[str] = set()
    aliases: dict[str, str] = {}
    for stmt in source.tree.body:
        check_deadline(source.deadline)
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            imported.update(alias.asname or alias.name.split(".")[0] for alias in stmt.names)
        if not isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            continue
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        if len(targets) != 1 or not isinstance(targets[0], ast.Name):
            continue
        name = targets[0].id
        root = stmt.value
        while isinstance(root, ast.Attribute):
            root = root.value
        if not isinstance(root, ast.Name) or root.id not in imported:
            continue
        if any(bindings.counts[key] != 1 or key in global_names for key in (name, root.id)):
            continue
        if stmt.value is not None and (resolved := source.resolve(stmt.value)):
            aliases[name] = resolved
    return aliases


def shadowed(source: Source, call: ast.Call, name: str) -> bool:
    # Reuse the definition binding inventory; local assignments hide a name throughout a scope.
    for definition, bindings in source.definition_bindings.items():
        if definition.lineno <= call.lineno <= (definition.end_lineno or definition.lineno):
            if name in bindings:
                return True
            extra = _AliasBindings(source.deadline)
            for stmt in definition.body:
                check_deadline(source.deadline)
                extra.visit(stmt)
            if extra.star_import or name in extra.names:
                return True
    for parent in ast.walk(source.tree):
        check_deadline(source.deadline)
        if (
            isinstance(parent, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp))
            and any(
                isinstance(target, ast.Name) and target.id == name
                for generator in parent.generators
                for target in ast.walk(generator.target)
            )
            and call in ast.walk(parent)
        ):
            return True
        if isinstance(parent, ast.Lambda) and call in ast.walk(parent.body):
            bindings = _Bindings()
            bindings.visit(parent.args)
            bindings.visit(parent.body)
            if name in bindings.names:
                return True
    return False


def test_scope(source: Source, call: ast.Call) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Only calls in a collected test's own scope, never a nested execution scope."""
    parents = source.parents
    node: ast.AST = call
    while node in parents:
        check_deadline(source.deadline)
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node if node in source.tests.values() else None
        if isinstance(node, (ast.ClassDef, ast.Lambda)):
            return None
    return None


def local_alias(source: Source, call: ast.Call) -> str | None:
    if not isinstance(call.func, ast.Name) or (scope := test_scope(source, call)) is None:
        return None
    name = call.func.id
    bindings = _AliasBindings(source.deadline)
    bindings.visit(scope.args)
    for stmt in scope.body:
        bindings.visit(stmt)
    if bindings.counts[name] != 1 or bindings.star_import:
        return None
    module, declared = source.alias_inventory
    if module.star_import:
        return None
    imports = {
        alias.asname or alias.name.split(".")[0]
        for stmt in source.tree.body
        if isinstance(stmt, (ast.Import, ast.ImportFrom))
        for alias in stmt.names
    }
    for stmt in scope.body:
        check_deadline(source.deadline)
        if not isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            continue
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        if len(targets) != 1 or not isinstance(targets[0], ast.Name) or targets[0].id != name:
            continue
        if (stmt.end_lineno or stmt.lineno, stmt.end_col_offset or 0) >= (
            call.lineno,
            call.col_offset,
        ):
            return None
        root = stmt.value
        while isinstance(root, ast.Attribute):
            root = root.value
        if not isinstance(root, ast.Name) or root.id not in imports:
            return None
        if module.counts[root.id] != 1 or root.id in bindings.names:
            return None
        if declared.intersection((name, root.id)):
            return None
        # A comprehension target can hide the otherwise unique test-local alias.
        if any(
            isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp))
            and call in ast.walk(node)
            and any(
                isinstance(target, ast.Name) and target.id == name
                for generator in node.generators
                for target in ast.walk(generator.target)
            )
            for node in ast.walk(scope)
        ):
            return None
        return source.resolve(stmt.value) if stmt.value is not None else None
    return None
