"""The impact cone: a bounded reverse search over exact call edges."""

import pytest
from graph_helpers import version

from oversight.graph import NodeNotFoundError, change_cone, change_graph, impact_cone, node_cone
from oversight_contracts.graph import GraphSnapshot, ImpactCone


def graph(files: dict[str, str]) -> GraphSnapshot:
    return version(files).graph


def cone(
    files: dict[str, str], seeds: list[str], max_depth: int = 10, max_nodes: int = 100
) -> ImpactCone:
    return impact_cone(graph(files), seeds, max_depth=max_depth, max_nodes=max_nodes)


def entries(found: ImpactCone) -> list[tuple[str, int]]:
    return [(e.node_id, e.distance) for e in found.entries]


CHAIN = {
    "m.py": """
        def a():
            b()
        def b():
            c()
        def c():
            d()
        def d():
            e()
        def e(): ...
    """
}


def test_the_depth_limit_cuts_the_cone_and_counts_what_lies_beyond() -> None:
    found = cone(CHAIN, ["m:e"], max_depth=2)
    assert entries(found) == [("m:d", 1), ("m:c", 2)]
    assert (found.omitted_by_depth, found.omitted_by_limit) == (2, 0)
    assert (found.max_depth, found.max_nodes, found.edges_followed) == (2, 100, "exact")


def test_the_node_limit_keeps_the_nearest_entries_by_id_and_counts_the_rest() -> None:
    files = {
        "m.py": """
            def seed(): ...
            def c3(): seed()
            def c1(): seed()
            def c4(): seed()
            def c2(): seed()
            def far(): c1()
            def beyond(): far()
        """
    }
    found = cone(files, ["m:seed"], max_depth=2, max_nodes=3)
    assert entries(found) == [("m:c1", 1), ("m:c2", 1), ("m:c3", 1)]
    # c4 and far are within the depth but over the limit; beyond is past the depth.
    assert (found.omitted_by_limit, found.omitted_by_depth) == (2, 1)


def test_seeds_and_tests_stay_out_of_the_entries_and_tests_reaching_counts_them() -> None:
    files = {
        "lib.py": """
            def core(): ...
            def helper():
                core()
            def wrapper():
                helper()
                core()
            def unrelated(): ...
        """,
        "tests/conftest.py": """
            import pytest
            from lib import helper

            @pytest.fixture
            def ready():
                return helper()
        """,
        "tests/test_lib.py": """
            from lib import core, helper, unrelated
            def test_direct():
                core()
            def test_through_a_helper():
                helper()
            def test_through_a_fixture(ready): ...
            def call_helper():
                helper()
            def test_through_a_test_helper():
                call_helper()
            def test_other():
                unrelated()
            def check_core():
                core()
        """,
    }
    found = cone(files, ["lib:core", "lib:helper"])
    assert found.seeds == ["lib:core", "lib:helper"]
    assert entries(found) == [("lib:wrapper", 1)]
    # test_direct, test_through_a_helper and test_through_a_test_helper. pytest calls fixtures by
    # name, a possible call, so test_through_a_fixture does not count; check_core and call_helper
    # are no test functions.
    assert found.tests_reaching == 3
    assert (found.omitted_by_depth, found.omitted_by_limit) == (0, 0)


@pytest.mark.parametrize("constructor", ["__init__", "__new__", "__post_init__"])
def test_a_changed_constructor_also_seeds_its_class(constructor: str) -> None:
    files = {
        "m.py": f"""
            class Cart:
                def {constructor}(self): ...
                def total(self): ...
            def make():
                return Cart()
            def checkout():
                make()
        """
    }
    found = cone(files, [f"m:Cart.{constructor}"])
    assert found.seeds == ["m:Cart", f"m:Cart.{constructor}"]
    assert entries(found) == [("m:make", 1), ("m:checkout", 2)]


def test_a_constructor_in_the_cone_brings_its_class_and_its_callers() -> None:
    files = {
        "m.py": """
            def core(): ...
            class Order:
                def __init__(self):
                    core()
                def other(self): ...
            def place():
                return Order()
        """
    }
    found = cone(files, ["m:core"])
    assert entries(found) == [("m:Order", 1), ("m:Order.__init__", 1), ("m:place", 2)]


def test_module_and_class_bodies_that_call_into_the_cone_are_entries() -> None:
    files = {
        "m.py": """
            def core(): ...
            DEFAULT = core()
            class Prices:
                RATE = core()
            def build():
                return Prices()
        """
    }
    found = cone(files, ["m:core"])
    assert entries(found) == [("m", 1), ("m:Prices", 1), ("m:build", 2)]


def test_a_cycle_ends_the_search() -> None:
    files = {
        "m.py": """
            def seed():
                a()
            def a():
                seed()
                b()
            def b():
                a()
        """
    }
    found = cone(files, ["m:seed"])
    assert entries(found) == [("m:a", 1), ("m:b", 2)]


def test_possible_calls_are_not_followed() -> None:
    files = {
        "m.py": """
            class Cart:
                def total(self): ...
            def use(cart):
                return cart.total()
        """
    }
    assert entries(cone(files, ["m:Cart.total"])) == []


def test_a_change_cone_is_seeded_by_added_and_modified_nodes() -> None:
    base = version({"m.py": "def f():\n    return 1\ndef g():\n    f()\ndef h(): ...\n"})
    head = version(
        {"m.py": "def f():\n    return 2\ndef g():\n    f()\ndef n():\n    h()\ndef h(): ...\n"}
    )
    found = change_cone(change_graph(base, head), head.graph, max_depth=3, max_nodes=40)
    assert found.seeds == ["m:f", "m:n"]
    assert entries(found) == [("m:g", 1)]


def test_a_node_cone_starts_from_one_node_and_refuses_an_unknown_one() -> None:
    snapshot = graph(CHAIN)
    found = node_cone(snapshot, "m:c", max_depth=1, max_nodes=40)
    assert found.seeds == ["m:c"]
    assert entries(found) == [("m:b", 1)]
    assert found.omitted_by_depth == 1
    with pytest.raises(NodeNotFoundError):
        node_cone(snapshot, "m:nothing", max_depth=1, max_nodes=40)
    with pytest.raises(ValueError):
        node_cone(snapshot, "m:c", max_depth=-1, max_nodes=40)
