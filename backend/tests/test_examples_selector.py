"""Selection boundaries on captured graphs, with actual static cone/test reach."""

import re
from typing import Any
from unittest.mock import Mock

import pytest

from oversight.examples_selector import select_examples
from oversight.graph import node_cone
from oversight_contracts.examples import TaskExample
from oversight_contracts.graph import GraphEdge, GraphNode, GraphSnapshot, ParseError


def node(name: str, *, ident: str | None = None, is_test: bool = False) -> GraphNode:
    return GraphNode(
        id=ident or f"pkg::{name}",
        name=name,
        qualname=f"pkg.{name}",
        kind="function",
        path="pkg.py",
        start_line=1,
        end_line=2,
        parent_id="pkg",
        is_test=is_test,
    )


def graph(*nodes: GraphNode, tested: bool = False, callers: int = 0) -> GraphSnapshot:
    entries = list(nodes)
    edges = []
    if tested:
        entries.append(node("test_f", is_test=True))
    entries.extend(node(f"caller{i}") for i in range(callers))
    for source in entries[len(nodes) :]:
        edges.append(
            GraphEdge(
                id=f"{source.id}-call",
                kind="calls",
                src=source.id,
                dst=nodes[0].id,
                confidence="exact",
                reason=None,
            )
        )
    return GraphSnapshot(
        version="v", commit="main", nodes=entries, edges=edges, parse_errors=[], positions={}
    )


def select(task: str, snapshot: GraphSnapshot, **kwargs: Any):
    return select_examples(
        task,
        snapshot,
        **({"examples_only": None, "supplied": [], "cone_depth": 4, "cone_limit": 40} | kwargs),
    )


@pytest.mark.parametrize("size", [79, 80, 81, 1599, 1600, 1601])
def test_trimmed_task_boundaries(size: int) -> None:
    result = select("  " + "x" * size + "\n", graph())
    assert result.offer == (size < 80 or size > 1600)
    assert ("short_task_no_target" in result.reasons) == (size < 80)
    assert ("long_task" in result.reasons) == (size > 1600)


@pytest.mark.parametrize("flag", [True, False, None])
def test_examples_only_never_vetoes(flag: bool | None) -> None:
    assert select("unknown", graph(), examples_only=flag).offer
    result = select("f", graph(node("f"), tested=True), examples_only=flag)
    assert result.offer == (flag is True)
    assert ("examples_only" in result.reasons) == (flag is True)


def test_supplied_examples_are_unconfirmed_even_with_pins() -> None:
    from oversight_contracts.examples import ExamplePin

    example = TaskExample(
        example_id="e",
        inputs="text",
        expected_output="null",
        pin=ExamplePin(source_task="old", confirmed_by="person"),
    )
    result = select("f", graph(node("f"), tested=True), supplied=[example], examples_only=False)
    assert result.offer and result.reasons == ("supplied_unconfirmed",)


@pytest.mark.parametrize("count", [2, 3])
def test_named_targets_boundary(count: int) -> None:
    nodes = [node(f"f{i}") for i in range(count)]
    result = select(" ".join(n.name for n in nodes) + " " + "x" * 80, graph(*nodes))
    assert result.offer == (count == 3)
    assert len(result.targets) == count


@pytest.mark.parametrize("count", [20, 21])
def test_named_candidates_includes_ambiguity(count: int) -> None:
    nodes = [
        node("shared", ident=f"pkg{i}::shared").model_copy(update={"qualname": f"pkg{i}.shared"})
        for i in range(count)
    ]
    result = select("shared " + "x" * 80, graph(*nodes))
    assert result.named_candidates == count and not result.targets
    assert result.ambiguous == ("shared",)
    assert result.offer == (count == 21)
    assert ("too_many_named_candidates" in result.reasons) == (count == 21)
    assert result.reasons[-1] == "ambiguous_name"


@pytest.mark.parametrize("entries", [14, 15])
def test_cone_size_boundary(entries: int) -> None:
    result = select("f " + "x" * 80, graph(node("f"), callers=entries))
    assert result.offer == (entries == 15)
    assert ("large_cone" in result.reasons) == (entries == 15)
    assert result.cones[0].cone and len(result.cones[0].cone.entries) == entries


@pytest.mark.parametrize("bounds", [{"cone_depth": 0}, {"cone_limit": 0}])
def test_nonzero_omitted(bounds: dict[str, int]) -> None:
    result = select("f " + "x" * 80, graph(node("f"), callers=1), **bounds)
    assert result.offer and result.reasons == ("cone_omitted",)
    cone = result.cones[0].cone
    assert cone and cone.omitted_by_depth + cone.omitted_by_limit == 1


@pytest.mark.parametrize("tested", [False, True])
def test_short_task_test_call_reach(tested: bool) -> None:
    result = select("f", graph(node("f"), tested=tested))
    assert result.offer != tested
    assert ("short_task_no_test_calls" in result.reasons) != tested
    assert result.cones[0].cone and result.cones[0].cone.tests_reaching == int(tested)


@pytest.mark.parametrize("path", ["pkg.py", "other.py"])
def test_parse_errors_report_unknown_not_empty_cone(path: str) -> None:
    snapshot = graph(node("f"), tested=True)
    snapshot = snapshot.model_copy(
        update={"parse_errors": [ParseError(path=path, line=1, message="syntax error")]}
    )
    result = select("f", snapshot)
    assert result.offer and "snapshot_parse_errors" in result.reasons
    assert ("target_parse_error" in result.reasons) == (path == "pkg.py")
    expected = (
        None if path == "pkg.py" else node_cone(snapshot, "pkg::f", max_depth=4, max_nodes=40)
    )
    assert result.cones[0].cone == expected
    assert "short_task_no_test_calls" not in result.reasons


@pytest.mark.parametrize("depth,limit", [(4, 40), (0, 40), (4, 0)])
def test_unrelated_parse_error_preserves_cone_evidence(depth: int, limit: int) -> None:
    snapshot = graph(node("f"), callers=16).model_copy(
        update={"parse_errors": [ParseError(path="broken.py", line=1, message="syntax error")]}
    )
    result = select("f", snapshot, cone_depth=depth, cone_limit=limit)
    expected = node_cone(snapshot, "pkg::f", max_depth=depth, max_nodes=limit)
    assert result.cones[0].cone == expected
    assert ("large_cone" in result.reasons) == (depth > 0 and limit > 0)
    assert ("cone_omitted" in result.reasons) == (depth == 0 or limit == 0)
    assert "snapshot_parse_errors" in result.reasons
    # Missing callers in the broken file still make negative test reach unknowable.
    assert "short_task_no_test_calls" not in result.reasons


@pytest.mark.parametrize("reference", ["pkg::f", "pkg.f", "f"])
def test_resolution_and_identifier_boundaries(reference: str) -> None:
    snapshot = graph(node("f"), node("other"))
    assert select(reference, snapshot).targets == ("pkg::f",)
    assert not select("prefixf f_suffix object.f pkg.foo", snapshot).targets


@pytest.mark.parametrize(
    "reference", ["pkg::apply_discount", "pkg.apply_discount", "apply_discount"]
)
@pytest.mark.parametrize("punctuation", [".", ",", ")"])
def test_resolution_with_trailing_punctuation(reference: str, punctuation: str) -> None:
    result = select(f"Fix {reference}{punctuation}", graph(node("apply_discount"), tested=True))
    assert result.targets == ("pkg::apply_discount",)
    assert result.named_candidates == 1 and len(result.cones) == 1
    assert not result.offer and not result.reasons


def test_absent_labels_skip_regex(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = graph(*(node(f"f{i:03}") for i in range(180)))
    finditer = Mock(wraps=re.finditer)
    monkeypatch.setattr(re, "finditer", finditer)
    assert not select("unknown", snapshot).targets
    finditer.assert_not_called()
    assert select("Fix f179", snapshot).targets == ("pkg::f179",)
    assert finditer.call_count == 1


def test_qualified_reference_does_not_resolve_ambiguous_suffix() -> None:
    snapshot = graph(
        node("f"), node("f", ident="other::f").model_copy(update={"qualname": "other.f"})
    )
    result = select("pkg.f", snapshot)
    assert result.targets == ("pkg::f",) and not result.ambiguous


def test_deterministic_selection_and_reason_order() -> None:
    snapshot = graph(node("f0"), node("f1"), node("f2"), callers=16)
    task = "f0 f1 f2 " + "x" * 1600
    first = select(task, snapshot, examples_only=True, cone_limit=15)
    snapshot.nodes.reverse()
    snapshot.edges.reverse()
    assert first == select(task, snapshot, examples_only=True, cone_limit=15)
    assert first.reasons == (
        "examples_only",
        "long_task",
        "many_named_targets",
        "large_cone",
        "cone_omitted",
    )


@pytest.mark.parametrize("size", [79, 80, 81])
def test_short_named_target_boundary(size: int) -> None:
    result = select(" f " + "x" * (size - 2) + " ", graph(node("f")))
    assert result.offer == (size < 80)
    assert result.targets == ("pkg::f",)


def test_one_tested_target_prevents_all_zero_test_reach_rule() -> None:
    result = select("f other", graph(node("f"), node("other"), tested=True))
    assert not result.offer and len(result.targets) == 2
