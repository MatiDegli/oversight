"""Construction identities include statically reached members, with a conservative fallback."""

import ast

from oversight.changes._shortcuts_common import check_deadline

type Function = ast.FunctionDef | ast.AsyncFunctionDef


def construction_ast(cls: ast.ClassDef, deadline: float) -> tuple[str, ...]:
    methods: dict[str, list[Function]] = {}
    selected: set[ast.AST] = set(cls.decorator_list)
    for node in cls.body:
        check_deadline(deadline)
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            selected.add(node)
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == "__init__" for target in targets):
                return _whole_class(cls, deadline)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.setdefault(node.name, []).append(node)
        elif not isinstance(node, ast.Pass) and not (
            isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        ):
            # Conditional class definitions/bindings cannot be resolved here.
            return _whole_class(cls, deadline)
    pending = list(methods.get("__init__", []))
    if not pending and cls.decorator_list:
        # A generated dataclass initializer writes each annotated field on self.
        pending = [
            method
            for field in cls.body
            if isinstance(field, ast.AnnAssign) and isinstance(field.target, ast.Name)
            for method in methods.get(field.target.id, [])
        ]
    while pending:
        check_deadline(deadline)
        method = pending.pop()
        if method in selected:
            continue
        selected.add(method)
        reached = _reached(method, methods, deadline)
        if reached is None:
            return _whole_class(cls, deadline)
        pending.extend(reached)
    return tuple(
        ast.dump(node, include_attributes=False)
        for node in [*cls.decorator_list, *cls.body]
        if node in selected
    )


def _whole_class(cls: ast.ClassDef, deadline: float) -> tuple[str, ...]:
    check_deadline(deadline)
    return tuple(
        ast.dump(node, include_attributes=False) for node in [*cls.decorator_list, *cls.body]
    )


def _reached(
    method: Function, methods: dict[str, list[Function]], deadline: float
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
            if node.attr == "__class__":
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
