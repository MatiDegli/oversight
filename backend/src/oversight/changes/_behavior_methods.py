"""AST-only method targets and literal receivers; never import project code here."""

import ast
import inspect
from dataclasses import replace

from oversight.changes._behavior_generation import generate
from oversight.changes._behavior_inputs import (
    MAX_INPUTS,
    BehaviorInput,
    BehaviorInputs,
    _default,  # pyright: ignore[reportPrivateUsage]
    _effect_reason,  # pyright: ignore[reportPrivateUsage]
    _literals,  # pyright: ignore[reportPrivateUsage]
    _signature,  # pyright: ignore[reportPrivateUsage]
    calls,
    literal_text,
    module_name,
)
from oversight.changes._behavior_objects import display, invocation_values, method_arguments
from oversight.changes._behavior_reasons import NOT_PROBED_REASONS
from oversight.changes._behavior_sources import Sources
from oversight.changes._review_identity import bound_inputs, canonical
from oversight.changes._shortcuts_common import Source, is_test_file, walk_local

type Function = ast.FunctionDef | ast.AsyncFunctionDef
HOOKS = {
    "__getattr__",
    "__getattribute__",
    "__setattr__",
    "__delattr__",
    "__slots__",
    "__del__",
    "__new__",
}


def _decorator(node: Function) -> str:
    decorators = node.decorator_list
    if not decorators:
        return "method"
    if len(decorators) == 1:
        value = decorators[0]
        if isinstance(value, ast.Name) and value.id in {"property", "classmethod", "staticmethod"}:
            return value.id
        if (
            isinstance(value, ast.Attribute)
            and isinstance(value.value, ast.Name)
            and value.value.id == node.name
            and value.attr in {"setter", "deleter"}
        ):
            return value.attr
    return "other decorator"


def _definition(
    cache: Sources, module: str, qualified: str
) -> tuple[Source, ast.ClassDef, Function]:
    name, marker, suffix = qualified.partition("#")
    ordinal = int(suffix) if marker else 1
    parts = name.split(".")
    if len(parts) != 2:
        raise ValueError("nested class")
    found: list[tuple[Source, ast.ClassDef, Function]] = []
    for path in cache.files:
        if not path.endswith(".py") or module_name(path) != module:
            continue
        if is_test_file(path):
            raise ValueError("test module")
        source = cache.get(path)
        classes = [
            n for n in source.tree.body if isinstance(n, ast.ClassDef) and n.name == parts[0]
        ]
        if len(classes) != 1:
            continue
        cls = classes[0]
        methods = [
            n
            for n in cls.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == parts[1]
        ]
        if 0 < ordinal <= len(methods):
            selected = methods[ordinal - 1]
            if any(_decorator(other) == _decorator(selected) for other in methods[ordinal:]):
                raise ValueError("method target mismatch")
            found.append((source, cls, selected))
    if len(found) != 1:
        raise ValueError("method target mismatch")
    return found[0]


def _dataclass(source: Source, cls: ast.ClassDef) -> bool:
    if len(cls.decorator_list) != 1:
        return False
    value = cls.decorator_list[0]
    func = value.func if isinstance(value, ast.Call) else value
    if source.resolve(func) != "dataclasses.dataclass":
        return False
    if isinstance(value, ast.Call):
        if value.args:
            return False
        for keyword in value.keywords:
            if keyword.arg not in {"frozen", "eq", "order"}:
                return False
            if not isinstance(keyword.value, ast.Constant) or type(keyword.value.value) is not bool:
                return False
    return True


def _class_reason(source: Source, cls: ast.ClassDef) -> str | None:
    if cls.bases or cls.keywords:
        return "class bases or metaclass"
    if cls.decorator_list and not _dataclass(source, cls):
        return "class decorator"
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in HOOKS:
            return "custom object hooks"
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id in HOOKS for target in targets):
                return "custom object hooks"
    return None


def _function_reason(source: Source, node: Function) -> str | None:
    if isinstance(node, ast.AsyncFunctionDef):
        return "async method"
    if any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in walk_local(node)):
        return "generator method"
    return _effect_reason(source, node)


def _bound_signature(
    source: Source, node: Function, *, allow_kwargs: bool = False
) -> inspect.Signature:
    signature = _signature(source, node, allow_kwargs=allow_kwargs)
    params = list(signature.parameters.values())
    if not params or params[0].kind not in (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    ):
        raise ValueError("unsupported signature")
    return signature.replace(parameters=params[1:])


def _property_signature(source: Source, node: Function, kind: str) -> None:
    params = list(_bound_signature(source, node).parameters.values())
    if kind == "property" and params:
        raise ValueError("unsupported getter signature")
    if kind == "setter" and (
        len(params) != 1
        or params[0].kind
        not in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ):
        raise ValueError("unsupported setter signature")


def _field_annotation(source: Source, annotation: ast.expr) -> None:
    parents = {
        child: parent for parent in ast.walk(annotation) for child in ast.iter_child_nodes(parent)
    }
    for node in ast.walk(annotation):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            # Dataclass recognizes some string annotations using runtime module globals;
            # do not guess about aliases or forward references without importing the class.
            raise ValueError("unsupported dataclass constructor")
        if not isinstance(node, (ast.Name, ast.Attribute)) or isinstance(
            parents.get(node), ast.Attribute
        ):
            continue
        resolved = source.resolve(node)
        if not resolved or resolved.rsplit(".", 1)[-1] in {"InitVar", "ClassVar", "KW_ONLY"}:
            raise ValueError("unsupported dataclass constructor")
        if not resolved.startswith("typing.") and resolved not in {
            "int",
            "str",
            "float",
            "bool",
            "bytes",
            "list",
            "tuple",
            "dict",
            "object",
        }:
            raise ValueError("unsupported dataclass constructor")


def _constructor(source: Source, cls: ast.ClassDef) -> inspect.Signature:
    initializers = [
        n
        for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "__init__"
    ]
    if initializers:
        if len(initializers) != 1 or initializers[0].decorator_list:
            raise ValueError("unsupported constructor")
        if reason := _function_reason(source, initializers[0]):
            raise ValueError(reason)
        return _bound_signature(source, initializers[0])
    if not cls.decorator_list:
        return inspect.Signature()
    if any(
        isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "__post_init__"
        for n in cls.body
    ):
        raise ValueError("unsupported dataclass constructor")
    params: list[inspect.Parameter] = []
    for node in cls.body:
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        _field_annotation(source, node.annotation)
        if isinstance(node.value, ast.Call):
            raise ValueError("unsupported dataclass constructor")
        default = inspect.Parameter.empty if node.value is None else _default(node.value, {})
        params.append(
            inspect.Parameter(
                node.target.id, inspect.Parameter.POSITIONAL_OR_KEYWORD, default=default
            )
        )
    return inspect.Signature(params)


def _properties(definitions: list[tuple[Source, ast.ClassDef, Function]]) -> tuple[str, ...]:
    getters: list[dict[str, Function]] = []
    for _, cls, _ in definitions:
        getters.append(
            {
                n.name: n
                for n in cls.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and _decorator(n) == "property"
                and not n.name.startswith("_")
            }
        )
    names: list[str] = []
    for name in sorted(getters[0].keys() & getters[1].keys()):
        nodes = [side[name] for side in getters]
        if ast.dump(nodes[0], include_attributes=False) != ast.dump(
            nodes[1], include_attributes=False
        ):
            continue
        for (source, _, _), node in zip(definitions, nodes, strict=True):
            _property_signature(source, node, "property")
        if any(
            _function_reason(source, node)
            for (source, _, _), node in zip(definitions, nodes, strict=True)
        ):
            raise ValueError("property getter effects")
        names.append(name)
    return tuple(names)


def _receivers(module: str, cls: str, cache: Sources):
    eligible = {
        path: text
        for path, text in cache.files.items()
        if is_test_file(path) or (path.endswith(".py") and module_name(path) == module)
    }
    return calls(eligible, module, cls, cache.deadline, cache=cache)


def _receiver_inputs(
    module: str,
    cls: str,
    caches: list[Sources],
    signatures: list[inspect.Signature],
):
    found: dict[str, tuple[str, str | None]] = {}
    for cache in caches:
        for args, kwargs in _receivers(module, cls, cache):
            literal = literal_text((args, kwargs))
            if literal in found:
                continue
            try:
                identities: list[str | None] = []
                for sig in signatures:
                    bound = sig.bind(*args, **kwargs)
                    bound.apply_defaults()
                    identities.append(bound_inputs(dict(bound.arguments)))
            except TypeError:
                continue
            found[literal] = (literal, identities[0] if identities[0] == identities[1] else None)
            if len(found) == 4:
                return list(found.values())
    return list(found.values())


def method_inputs(
    module: str,
    qualified: str,
    base: dict[str, str],
    head: dict[str, str],
    *,
    deadline: float = float("inf"),
) -> BehaviorInputs:
    caches = [Sources(files, deadline) for files in (base, head)]
    try:
        definitions = [_definition(cache, module, qualified) for cache in caches]
        kinds = [_decorator(node) for _, _, node in definitions]
        if kinds[0] != kinds[1]:
            raise ValueError("method target mismatch")
        kind = kinds[0]
        if kind in {"classmethod", "staticmethod", "deleter", "other decorator"}:
            raise ValueError({"deleter": "property deleter"}.get(kind, kind))
        for source, cls, node in definitions:
            if node.name.startswith("__") and node.name.endswith("__"):
                raise ValueError("dunder method")
            if reason := _class_reason(source, cls) or _function_reason(source, node):
                raise ValueError(reason)
            if kind in ("property", "setter"):
                _property_signature(source, node, kind)
            # Setters can run through ordinary attribute access, including construction/getters.
            for setter in cls.body:
                if (
                    isinstance(setter, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and _decorator(setter) == "setter"
                    and (reason := _function_reason(source, setter))
                ):
                    raise ValueError(reason)
        constructors = [_constructor(source, cls) for source, cls, _ in definitions]
        properties = _properties(definitions)
        signatures = [_bound_signature(source, node) for source, _, node in definitions]
        nodes = [node for _, _, node in definitions]
        integers = [
            v for source, _, _ in definitions for v in _literals(source.tree) if type(v) is int
        ]
        cls, name = definitions[0][1].name, nodes[0].name
        receivers = _receiver_inputs(module, cls, caches, constructors)
        if not receivers:
            raise ValueError("no literal constructor receivers")
        observed = [method_arguments(module, cls, name, cache) for cache in caches]
        generated = generate(
            module, name, base, head, nodes, signatures, integers, deadline, observed, caches
        )
        return replace(
            generated,
            inputs=_combine_receivers(cls, kind, generated, receivers),
            receiver_class=cls,
            method_kind=kind,
            properties=properties,
        )
    except (ValueError, TypeError, SyntaxError, RecursionError) as error:
        return BehaviorInputs(
            reason=text
            if isinstance(error, ValueError) and (text := str(error)) in NOT_PROBED_REASONS
            else "unsupported signature"
        )


def _combine_receivers(
    cls: str, kind: str, generated: BehaviorInputs, receivers: list[tuple[str, str | None]]
) -> tuple[BehaviorInput, ...]:
    samples: list[BehaviorInput] = []
    # Round-robin receivers keep the shared MAX_INPUTS cap from starving later receivers.
    for sample in generated.inputs:
        for receiver, identity in receivers:
            args, kwargs = ast.literal_eval(receiver)
            arguments = ", ".join(
                [
                    *(literal_text(v) for v in args),
                    *(f"{k}={literal_text(v)}" for k, v in kwargs.items()),
                ]
            )
            receiver_text = f"{cls}({arguments})"
            name = sample.call.split("(", 1)[0]
            if kind == "property":
                call = f"{receiver_text}.{name}"
            elif kind == "setter":
                args, kwargs = invocation_values(sample.before)
                values = [*args, *kwargs.values()]
                if len(values) != 1:
                    continue
                call = f"{receiver_text}.{name} = {display(values[0])}"
            else:
                call = f"{receiver_text}.{sample.call}"
            samples.append(
                BehaviorInput(
                    call,
                    literal_text((receiver, sample.before)),
                    literal_text((receiver, sample.after)),
                    canonical([identity, sample.input_identity])
                    if identity is not None and sample.input_identity is not None
                    else None,
                )
            )
            if len(samples) == MAX_INPUTS:
                break
        if len(samples) == MAX_INPUTS:
            break
    return tuple(samples)
