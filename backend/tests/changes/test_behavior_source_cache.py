"""Behavior discovery shares syntax inventories across call-site lookups."""

import ast

import pytest

from oversight.changes._behavior_aliases import local_alias
from oversight.changes._behavior_aliases import test_scope as find_test_scope
from oversight.changes._behavior_builtins import builtin_call
from oversight.changes._behavior_sources import Sources
from oversight.changes._shortcuts_common import Source, _AliasBindings


@pytest.mark.parametrize("cache_first", [False, True])
def test_scope_and_discovery_share_one_parent_walk(monkeypatch, cache_first):
    source = Source("def test_values():\n    first()\n    second()\n")
    calls = [node for node in ast.walk(source.tree) if isinstance(node, ast.Call)]
    cache = Sources({})
    original_walk = ast.walk
    module_walks = 0

    def walk(node):
        nonlocal module_walks
        if node is source.tree:
            module_walks += 1
        return original_walk(node)

    monkeypatch.setattr(ast, "walk", walk)
    if cache_first:
        cache.parents(source)
    for call in calls:
        assert find_test_scope(source, call) is source.tests["test_values"]
    parents = cache.parents(source)
    assert parents is cache.parents(source)
    assert all(call in parents for call in calls)
    assert module_walks == 1


def test_builtin_and_local_alias_lookups_share_one_module_binding_visit(monkeypatch):
    source = Source(
        "from sample import target\n"
        "def test_values():\n"
        "    alias = target\n"
        "    alias(range(2))\n"
        "    list(target())\n"
    )
    calls = {
        node.func.id: node
        for node in ast.walk(source.tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    original_visit = _AliasBindings.visit
    module_visits = 0

    def visit(self, node):
        nonlocal module_visits
        if node is source.tree:
            module_visits += 1
        original_visit(self, node)

    monkeypatch.setattr(_AliasBindings, "visit", visit)
    for _ in range(2):
        assert builtin_call(source, calls["range"], "range")
        assert builtin_call(source, calls["list"], "list")
        assert local_alias(source, calls["alias"]) == "sample.target"
    assert module_visits == 1


def test_global_and_nonlocal_inventory_is_walked_once(monkeypatch):
    source = Source(
        "def outer():\n"
        "    value = 0\n"
        "    def inner():\n"
        "        global range\n"
        "        nonlocal value\n"
    )
    original_walk = ast.walk
    module_walks = 0

    def walk(node):
        nonlocal module_walks
        if node is source.tree:
            module_walks += 1
        return original_walk(node)

    monkeypatch.setattr(ast, "walk", walk)
    inventory = source.alias_inventory
    assert inventory is source.alias_inventory
    assert inventory[1] == frozenset({"range", "value"})
    assert module_walks == 1
