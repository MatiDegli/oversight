"""Construction identities include statically reached members, with a conservative fallback."""

import ast

from oversight.changes._shortcuts_common import Source, check_deadline

type Function = ast.FunctionDef | ast.AsyncFunctionDef


def construction_ast(cls: ast.ClassDef, deadline: float, source: Source | None) -> tuple[str, ...]:
    """Build a construction identity; None selects the class-only identity."""
    members = _members(cls, deadline)
    helpers = _helpers(members, source, deadline) if source is not None else ()
    return tuple(ast.dump(node, include_attributes=False) for node in (*members, *helpers))


def _helpers(roots: tuple[ast.AST, ...], source: Source, deadline: float) -> tuple[Function, ...]:
    functions: dict[str, list[Function]] = {}
    for node in source.tree.body:
        check_deadline(deadline)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.setdefault(node.name, []).append(node)
    selected: set[Function] = set()
    pending = list(roots)
    while pending:
        check_deadline(deadline)
        for node in ast.walk(pending.pop()):
            check_deadline(deadline)
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            names = [node.func.id]
            if node.func.id == "property":
                names.extend(
                    arg.id
                    for arg in [*node.args, *[kw.value for kw in node.keywords]]
                    if isinstance(arg, ast.Name)
                )
            # Conservatively include every same-named module definition on ambiguity.
            for name in names:
                for helper in functions.get(name, []):
                    if helper not in selected:
                        selected.add(helper)
                        pending.append(helper)
    # Class-level walrus bindings, super().__setattr__, and descriptor classes
    # are outside this construction identity analysis.
    # Only top-level module defs are indexed: helpers under a module-level if or try
    # are not reached. Helpers keep module order, so reordering two reached helpers
    # conservatively makes the identity different.
    return tuple(node for group in functions.values() for node in group if node in selected)


def _members(cls: ast.ClassDef, deadline: float) -> tuple[ast.AST, ...]:
    methods: dict[str, list[Function]] = {}
    bindings: set[str] = set()
    assignments: set[str] = set()
    selected: set[ast.AST] = set(cls.decorator_list)
    for node in cls.body:
        check_deadline(deadline)
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            selected.add(node)
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = {
                name.id
                for target in targets
                for name in ast.walk(target)
                if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Store)
            }
            bindings.update(names)
            if isinstance(node, ast.Assign):
                assignments.update(names)
            if any(name.startswith("__") and name.endswith("__") for name in bindings):
                return _whole_class(cls, deadline)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(
                isinstance(dec, ast.Attribute)
                and dec.attr in {"setter", "deleter"}
                and (not isinstance(dec.value, ast.Name) or dec.value.id != node.name)
                for dec in node.decorator_list
            ):
                # A renamed accessor cannot be reached by the property's name here.
                return _whole_class(cls, deadline)
            methods.setdefault(node.name, []).append(node)
        elif not isinstance(node, ast.Pass) and not (
            isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        ):
            # Conditional class definitions/bindings cannot be resolved here.
            return _whole_class(cls, deadline)
    pending = list(methods.get("__init__", []))
    if not pending and cls.decorator_list:
        # A generated dataclass initializer writes each annotated field on self.
        for field in cls.body:
            check_deadline(deadline)
            if not isinstance(field, ast.AnnAssign) or not isinstance(field.target, ast.Name):
                continue
            if field.target.id in assignments:
                # Assigned descriptors can invoke accessors with unrelated names.
                return _whole_class(cls, deadline)
            if field.value is not None:
                try:
                    ast.literal_eval(field.value)
                except (ValueError, TypeError):
                    return _whole_class(cls, deadline)
            pending.extend(methods.get(field.target.id, []))
    while pending:
        check_deadline(deadline)
        method = pending.pop()
        if method in selected:
            continue
        selected.add(method)
        reached = _reached(method, methods, bindings, deadline)
        if reached is None:
            return _whole_class(cls, deadline)
        pending.extend(reached)
    return tuple(node for node in [*cls.decorator_list, *cls.body] if node in selected)


def _whole_class(cls: ast.ClassDef, deadline: float) -> tuple[ast.AST, ...]:
    check_deadline(deadline)
    return (*cls.decorator_list, *cls.body)


def _reached(
    method: Function, methods: dict[str, list[Function]], bindings: set[str], deadline: float
) -> list[Function] | None:
    positional = [*method.args.posonlyargs, *method.args.args]
    if not positional or (
        method.decorator_list
        and not all(
            (isinstance(dec, ast.Name) and dec.id == "property")
            or (isinstance(dec, ast.Attribute) and dec.attr == "setter")
            for dec in method.decorator_list
        )
    ):
        return None
    receiver = positional[0].arg
    reached: list[Function] = []
    for root in method.body:
        nodes = list(ast.walk(root))
        calls = {node.func for node in nodes if isinstance(node, ast.Call)}
        for node in nodes:
            check_deadline(deadline)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                return None
            for child in ast.iter_child_nodes(node):
                if (
                    isinstance(child, ast.Name)
                    and child.id == receiver
                    and not (isinstance(node, ast.Attribute) and node.value is child)
                ):
                    # Passing/aliasing/rebinding self makes member reach dynamic.
                    return None
            if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
                continue
            if node.value.id != receiver:
                continue
            if node.attr == "__class__" or node.attr in bindings:
                # Class assignments may install descriptors with differently named accessors.
                return None
            members = methods.get(node.attr, [])
            if isinstance(node.ctx, (ast.Store, ast.Del)) and any(
                not member.decorator_list for member in members
            ):
                return None
            reached.extend(members)
            if node in calls and not members:
                return None
    return reached
