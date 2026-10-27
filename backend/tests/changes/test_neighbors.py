"""Bounded, deterministic review context uses graph relations, never source heuristics."""

from textwrap import dedent

import pytest

from oversight.changes._neighbors import HEADING, graph_context
from oversight.graph import parse_version
from oversight_contracts.graph import EdgeKind, GraphEdge, GraphNode, GraphSnapshot, NodeKind


def node(name: str, kind: NodeKind = "function", parent: str | None = None) -> GraphNode:
    return GraphNode(
        id=f"mod:{name}",
        kind=kind,
        name=name,
        qualname=name,
        path="mod.py",
        start_line=1,
        end_line=2,
        parent_id=parent,
        is_test=False,
    )


def call(src: str, dst: str, possible: bool = False, kind: EdgeKind = "calls") -> GraphEdge:
    return GraphEdge(
        id=f"{src}-{dst}-{possible}-{kind}",
        kind=kind,
        src=f"mod:{src}",
        dst=f"mod:{dst}",
        confidence="possible" if possible else "exact",
        reason="unknown_receiver" if possible else None,
    )


def graph(nodes: list[GraphNode], edges: list[GraphEdge]) -> GraphSnapshot:
    return GraphSnapshot(
        version="v", commit="base", nodes=nodes, edges=edges, parse_errors=[], positions={}
    )


def test_callers_and_siblings_rank_both_edges_and_deduplicate() -> None:
    snapshot = graph(
        [node(n) for n in ("edited", "a_possible", "z_exact", "a_sibling", "z_sibling")],
        [
            call("a_possible", "edited", True),
            call("z_exact", "edited"),
            call("z_exact", "edited", True),  # keep the strongest relationship
            call("a_possible", "a_sibling"),
            call("z_exact", "z_sibling"),
            call("z_exact", "a_sibling", True),
            call("edited", "z_exact"),  # outgoing calls alone are not siblings
        ],
    )
    before = snapshot.model_dump_json()
    block = graph_context(snapshot, ["mod:edited", "mod:edited"])
    assert block == (
        f"{HEADING}\nedited (mod.py)\n"
        "Callers: z_exact (mod.py; exact calls), a_possible (mod.py; possible calls)\n"
        "Siblings: z_sibling (mod.py; exact calls), a_sibling (mod.py; possible calls)"
    )
    assert snapshot.model_dump_json() == before
    assert (
        graph_context(snapshot.model_copy(update={"edges": snapshot.edges[::-1]}), ["mod:edited"])
        == block
    )


def test_same_class_and_module_callers_without_listing_containers() -> None:
    snapshot = graph(
        [
            node("module", "module"),
            node("C", "class"),
            node("D", "class"),
            node("C.edited", "method", "mod:C"),
            node("C.peer", "method", "mod:C"),
            node("D.peer", "method", "mod:D"),
            node("shared"),
            node("ignored"),
        ],
        [
            call("module", "C.edited"),
            call("module", "shared"),
            call("C", "C.edited"),
            call("C", "shared"),
            call("C.edited", "ignored", kind="imports"),
        ],
    )
    assert graph_context(snapshot, ["mod:C.edited", "mod:module", "mod:C", "missing"]) == (
        f"{HEADING}\nC.edited (mod.py)\nCallers: none\n"
        "Siblings: shared (mod.py; exact calls), C.peer (mod.py; same class)"
    )
    assert graph_context(snapshot, ["mod:C", "missing"]) == ""


@pytest.mark.parametrize("relation", ["callers", "siblings"])
def test_each_list_caps_unique_nodes_at_eight_exact_first(relation: str) -> None:
    names = [f"a_possible_{i}" for i in range(9)] + ["z_exact"]
    edges = [call(n, "edited", n != "z_exact") for n in names]
    if relation == "siblings":
        edges = [call("caller", "edited")] + [call("caller", n, n != "z_exact") for n in names]
    snapshot = graph([node(n) for n in ["edited", "caller", *names]], edges + edges)
    block = graph_context(snapshot, ["mod:edited"])
    line = next(line for line in block.splitlines() if line.startswith(relation.title()))
    assert line.startswith(f"{relation.title()}: z_exact (mod.py; exact calls), a_possible_0")
    assert "a_possible_6" in line and "a_possible_7" not in line
    assert line.endswith("+2 more")


def test_qualified_names_and_paths_disambiguate_and_seeds_sort() -> None:
    other = node("peer").model_copy(update={"id": "other:peer", "path": "other.py"})
    snapshot = graph(
        [node("edited"), node("caller"), node("peer"), other],
        [
            call("caller", "edited"),
            call("caller", "peer"),
            call("caller", "peer").model_copy(update={"id": "other-call", "dst": other.id}),
        ],
    )
    block = graph_context(snapshot, ["mod:peer", "mod:edited"])
    assert block.index("edited (mod.py)\n") < block.index("peer (mod.py)\n")
    assert "peer (mod.py; exact calls), peer (other.py; exact calls)" in block


def test_nested_scope_calls_belong_to_enclosing_function_as_graph_decides() -> None:
    snapshot = parse_version(
        {
            "mod.py": dedent("""
        def edited(): pass
        def sibling(): pass
        def caller():
            def nested():
                edited()
                sibling()
            return nested
        def shadowed(edited):
            edited()
    """).encode()
        },
        "base",
    ).graph
    assert graph_context(snapshot, ["mod:edited"]) == (
        f"{HEADING}\nedited (mod.py)\nCallers: caller (mod.py; exact calls)\n"
        "Siblings: sibling (mod.py; exact calls)"
    )
