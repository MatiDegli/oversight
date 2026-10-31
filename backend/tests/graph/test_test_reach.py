"""Static exact-call reach is graph evidence, not measured test coverage."""

import pytest
from graph_helpers import version

from oversight.graph import call_graph_test_reach


def reach(files, changed=("app:target",), *, depth=8, nodes=100):
    return call_graph_test_reach(version(files).graph, changed, max_depth=depth, max_nodes=nodes)


@pytest.mark.parametrize("path", ["tests/check.py", "test/check.py", "test_app.py", "app_test.py"])
def test_direct_test_call_uses_existing_test_path_rule(path):
    result = reach(
        {
            "app.py": "def target(): pass",
            path: "from app import target\ndef test_target(): target()",
        }
    )
    assert result.complete and result.unreached_nodes == ()


def test_exact_chain_through_helpers_reaches_function_and_method():
    files = {
        "app.py": "class A:\n def target(self): pass\n def run(self): self.target()",
        "tests/test_app.py": (
            "from app import A\ndef helper(): A.run(None)\ndef test_run(): helper()"
        ),
    }
    result = reach(files, ["app:A.target", "app:A.run"])
    assert result.complete and result.unreached_nodes == ()


def test_test_method_reaches_function_through_exact_chain():
    result = reach(
        {
            "app.py": "def target(): pass\ndef helper(): target()",
            "tests/test_app.py": (
                "from app import helper\nclass TestApp:\n def test_target(self): helper()"
            ),
        }
    )
    assert result.complete and result.unreached_nodes == ()


def test_possible_call_does_not_demonstrate_reach():
    files = {
        "app.py": "def target(): pass",
        "tests/test_app.py": ("from app import target\ntarget = None\ndef test_target(): target()"),
    }
    graph = version(files).graph
    assert any(e.dst == "app:target" and e.confidence == "possible" for e in graph.edges)
    result = reach(files)
    assert result.complete and result.unreached_nodes == ("app:target",)


def test_test_helpers_and_module_calls_are_not_test_roots():
    result = reach(
        {
            "app.py": "def target(): pass",
            "tests/test_app.py": "from app import target\ntarget()\ndef helper(): target()",
        }
    )
    assert result.complete and result.unreached_nodes == ("app:target",)


@pytest.mark.parametrize(("depth", "nodes"), [(1, 100), (8, 2), (0, 100), (8, 0)])
def test_cut_walk_is_unclassified(depth, nodes):
    result = reach(
        {
            "app.py": "def target(): pass\ndef helper(): target()",
            "tests/test_app.py": "from app import helper\ndef test_target(): helper()",
        },
        depth=depth,
        nodes=nodes,
    )
    assert not result.complete and result.unreached_nodes == ()


def test_exact_bound_without_a_cut_is_complete_and_cycles_terminate():
    files = {"app.py": "def target(): helper()\ndef helper(): target()"}
    result = reach(files, depth=1, nodes=2)
    assert result.complete and result.unreached_nodes == ("app:target",)


def test_unknown_seed_is_unclassified_and_containers_are_ignored():
    files = {"app.py": "class A: pass\ndef target(): pass"}
    result = reach(files, ["app", "app:A", "app:gone", "app:target"])
    assert not result.complete and result.unreached_nodes == ("app:target",)


def test_parse_errors_prevent_negative_classification():
    result = reach({"app.py": "def target(): pass", "tests/test_app.py": "def broken("})
    assert not result.complete and result.unreached_nodes == ()


@pytest.mark.parametrize(("depth", "nodes"), [(-1, 10), (1, -1)])
def test_negative_bounds_are_rejected(depth, nodes):
    with pytest.raises(ValueError):
        reach({"app.py": "def target(): pass"}, depth=depth, nodes=nodes)


def test_a_cut_does_not_hide_other_exhaustively_unreached_functions():
    result = reach(
        {
            "app.py": "def target(): pass\ndef caller(): target()\ndef isolated(): pass",
        },
        ["app:target", "app:isolated"],
        depth=0,
    )
    assert not result.complete and result.unreached_nodes == ("app:isolated",)


def forward(
    files, targets=("app:target",), *, root="tests.test_app:test_target", depth=8, nodes=100
):
    from oversight.graph import test_reaches as reaches

    return reaches(version(files).graph, root, targets, max_depth=depth, max_nodes=nodes)


FORWARD = {
    "app.py": "def target(): pass\ndef helper(): target()\ndef isolated(): pass",
    "tests/test_app.py": "from app import helper\ndef test_target(): helper()",
}


def test_forward_reach_finds_target_through_helpers():
    assert forward(FORWARD) is True


def test_forward_reach_root_in_targets():
    assert forward(FORWARD, ["tests.test_app:test_target"], depth=0, nodes=1) is True


def test_forward_reach_exhaustive_miss_is_false():
    assert forward(FORWARD, ["app:isolated"]) is False
    assert forward({"tests/test_app.py": "def test_target(): test_target()"}, ["gone"]) is False


def test_forward_reach_possible_edge_is_unknown():
    files = FORWARD | {
        "tests/test_app.py": "from app import target\ntarget = None\ndef test_target(): target()"
    }
    assert forward(files) is None
    # A proven target on another branch still wins over an uncertain call.
    files["tests/test_app.py"] = (
        "from app import target, helper\ntarget = None\ndef test_target():\n target()\n helper()"
    )
    assert forward(files) is True


@pytest.mark.parametrize(("depth", "nodes"), [(0, 100), (8, 1), (8, 0)])
def test_forward_reach_cut_or_parse_error_is_unknown(depth, nodes):
    assert forward(FORWARD, depth=depth, nodes=nodes) is None
    assert forward(FORWARD | {"bad.py": "def broken("}) is None
    assert forward(FORWARD, root="missing") is None


@pytest.mark.parametrize(("depth", "nodes"), [(-1, 1), (1, -1)])
def test_forward_reach_negative_budgets_raise(depth, nodes):
    with pytest.raises(ValueError):
        forward(FORWARD, depth=depth, nodes=nodes)


def test_forward_reach_cut_does_not_hide_a_queued_target():
    assert forward(FORWARD, ["app:helper"], nodes=2) is True
    assert forward(FORWARD, ["app:target"], depth=2, nodes=3) is True
