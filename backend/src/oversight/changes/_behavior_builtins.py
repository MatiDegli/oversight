"""Unshadowed builtin call sites and bounded range input data; no evaluation."""

import ast

from oversight.changes._behavior_aliases import shadowed
from oversight.changes._shortcuts_common import Source, is_test_file

MAX_RANGE_ITEMS = 10_000


def builtin_call(source: Source, call: ast.Call, name: str) -> bool:
    if not isinstance(call.func, ast.Name) or call.func.id != name:
        return False
    bindings, declared = source.alias_inventory
    if bindings.star_import or name in bindings.names or shadowed(source, call, name):
        return False
    if name in declared:
        return False
    return source.resolve(call.func) == name


def range_argument(source: Source, call: ast.Call) -> range:
    if not builtin_call(source, call, "range"):
        raise ValueError("shadowed range builtin")
    if call.keywords or not 1 <= len(call.args) <= 3:
        raise ValueError("range requires one to three int literals")
    values: list[int] = []
    for node in call.args:
        # literal_eval admits signed integers, but never expressions or booleans here.
        try:
            value = ast.literal_eval(node)
        except (ValueError, TypeError):
            raise ValueError("range requires one to three int literals") from None
        if type(value) is not int:
            raise ValueError("range requires one to three int literals")
        values.append(value)
    if len(values) == 3 and values[2] == 0:
        raise ValueError("range step is zero")
    result = range(*values)
    try:
        if len(result) <= MAX_RANGE_ITEMS:
            return result
    except OverflowError:
        pass
    raise ValueError("range length cap")


def consumed_call(
    source: Source, path: str, call: ast.Call, parents: dict[ast.AST, ast.AST]
) -> bool:
    wrapper = parents.get(call)
    return (
        is_test_file(path)
        and isinstance(wrapper, ast.Call)
        and wrapper.args == [call]
        and not wrapper.keywords
        and builtin_call(source, wrapper, "list")
    )
