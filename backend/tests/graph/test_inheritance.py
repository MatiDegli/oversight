"""Class bases use call name resolution, but link only to repository class nodes."""

from pathlib import Path

import pytest
from graph_helpers import COMMIT, scan, summary, version, write

from oversight.graph import WorkspaceGraph, graph_delta, impact_cone, possible_callers
from oversight_contracts.graph import GraphEdge, GraphSnapshot


def inherits(snapshot: GraphSnapshot) -> dict[tuple[str, str], GraphEdge]:
    return {(e.src, e.dst): e for e in snapshot.edges if e.kind == "inherits"}


def test_direct_and_multiple_bases_are_exact_and_owned_by_the_class() -> None:
    snap = version(
        {"m.py": "class Base: ...\nclass Other: ...\nclass Child(Base, Other): ...\n"}
    ).graph
    edges = inherits(snap)
    assert set(edges) == {("m:Child", "m:Base"), ("m:Child", "m:Other")}
    for (src, dst), edge in edges.items():
        assert (edge.id, edge.confidence, edge.reason) == (f"inherits:{src}->{dst}", "exact", None)
    assert not any(e.kind == "calls" for e in snap.edges)


@pytest.mark.parametrize(
    ("statement", "base"),
    [
        ("from pkg.base import Base as Parent", "Parent"),
        ("import pkg.base", "pkg.base.Base"),
        ("import pkg.base as mod", "mod.Base"),
        ("from pkg import base as mod", "mod.Base"),
        ("from pkg import Base", "Base"),
    ],
)
def test_imported_bases_follow_aliases_and_reexports(statement: str, base: str) -> None:
    snap = version(
        {
            "pkg/__init__.py": "from .base import Base\n",
            "pkg/base.py": "class Base: ...\n",
            "child.py": f"{statement}\nclass Child({base}): ...\n",
        }
    ).graph
    edges = inherits(snap)
    assert set(edges) == {("child:Child", "pkg.base:Base")}
    assert edges[("child:Child", "pkg.base:Base")].confidence == "exact"


def test_external_bases_nonclasses_and_metaclass_keywords_add_no_edges() -> None:
    snap = version(
        {
            "lib.py": "class Meta: ...\ndef function(): ...\n",
            "m.py": """
                import lib
                from lib import function, Meta
                from third_party import External
                class Builtin(Exception): ...
                class ThirdParty(External): ...
                class Module(lib): ...
                class Function(function): ...
                class Keyword(metaclass=Meta): ...
            """,
        }
    ).graph
    assert inherits(snap) == {}


def test_several_definitions_are_possible_with_stable_suffixed_ids() -> None:
    snap = version(
        {
            "m.py": """
                if flag:
                    class Base: ...
                else:
                    class Base: ...
                class Child(Base): ...
            """
        }
    ).graph
    edges = inherits(snap)
    assert set(edges) == {("m:Child", "m:Base"), ("m:Child", "m:Base#2")}
    assert {(e.confidence, e.reason) for e in edges.values()} == {
        ("possible", "several_candidates")
    }


@pytest.mark.parametrize(
    ("statement", "reason"),
    [
        ("from lib import *", "star_import"),
        ("from lib import Base\nBase = replacement", "rebound_name"),
        ("from lib import Base\nfrom lib import *", "star_import"),
    ],
)
def test_star_imports_and_rebound_names_keep_their_reason(statement: str, reason: str) -> None:
    snap = version(
        {"lib.py": "class Base: ...\n", "m.py": f"{statement}\nclass Child(Base): ...\n"}
    ).graph
    edges = inherits(snap)
    assert set(edges) == {("m:Child", "lib:Base")}
    edge = edges[("m:Child", "lib:Base")]
    assert (edge.confidence, edge.reason) == ("possible", reason)


def test_nonclass_candidates_still_make_the_class_candidate_possible() -> None:
    snap = version(
        {
            "m.py": """
                if flag:
                    class Base: ...
                else:
                    def Base(): ...
                class Child(Base): ...
            """
        }
    ).graph
    edges = inherits(snap)
    assert set(edges) == {("m:Child", "m:Base")}
    assert edges[("m:Child", "m:Base")].reason == "several_candidates"


@pytest.mark.parametrize("base", ["Base[T]", "lib.Base[list[T]]"])
def test_generic_bases_resolve_the_unsubscripted_class(base: str) -> None:
    snap = version(
        {
            "lib.py": "class Base: ...\n",
            "m.py": f"import lib\nfrom lib import Base\nclass Child({base}): ...\n",
        }
    ).graph
    edges = inherits(snap)
    assert set(edges) == {("m:Child", "lib:Base")}
    assert edges[("m:Child", "lib:Base")].confidence == "exact"


def test_nested_classes_resolve_bases_in_the_enclosing_scope() -> None:
    snap = version(
        {
            "m.py": """
                class Base: ...
                class Outer:
                    class Base: ...
                    class Child(Base):
                        class Inner(Base): ...
                class Dotted(Outer.Base): ...
            """
        }
    ).graph
    edges = inherits(snap)
    assert set(edges) == {
        ("m:Outer.Child", "m:Outer.Base"),
        ("m:Outer.Child.Inner", "m:Base"),
        ("m:Dotted", "m:Outer.Base"),
    }
    assert all(e.confidence == "exact" for e in edges.values())


def test_base_factory_calls_remain_calls_in_the_enclosing_scope() -> None:
    snap = version({"m.py": "def factory(): ...\nclass Child(factory()): ...\n"}).graph
    assert inherits(snap) == {}
    assert summary(snap, "m") == {"m:factory": "exact"}


def test_reparse_updates_bases_and_relinks_unchanged_importers(tmp_path: Path) -> None:
    files = {
        "lib.py": "class Base: ...\nclass Other: ...\n",
        "m.py": "from lib import Base, Other\nclass Child(Base): ...\n",
    }
    graph, original = scan(tmp_path, files)
    assert set(inherits(original)) == {("m:Child", "lib:Base")}
    write(
        tmp_path,
        {"m.py": "# moved down\n\nfrom lib import Base, Other\nclass Child(Base): ...\n"},
    )
    moved = graph.scan(COMMIT)
    assert inherits(moved) == inherits(original)
    assert graph.last_parsed == ("m.py",)

    write(tmp_path, {"m.py": "from lib import Base, Other\nclass Child(Other): ...\n"})
    edited = graph.scan(COMMIT)
    assert set(inherits(edited)) == {("m:Child", "lib:Other")}
    delta = graph_delta(original, edited, "user_edit", COMMIT)
    assert delta is not None
    assert [e.id for e in delta.edges_added] == ["inherits:m:Child->lib:Other"]
    assert delta.edges_removed == ["inherits:m:Child->lib:Base"]

    write(tmp_path, {"lib.py": "class Base: ...\ndef Other(): ...\n"})
    assert inherits(graph.scan(COMMIT)) == {}
    assert graph.last_parsed == ("lib.py",)
    write(tmp_path, files)
    assert graph.scan(COMMIT) == original


def test_impact_cone_and_possible_callers_ignore_inheritance() -> None:
    snap = version(
        {
            "lib.py": "class Base: ...\nclass Child(Base): ...\n",
            "m.py": "from lib import *\nclass MaybeChild(Base): ...\n",
        }
    ).graph
    assert len(inherits(snap)) == 2
    cone = impact_cone(snap, ["lib:Base"], max_depth=10, max_nodes=100)
    assert cone.entries == []
    assert (cone.omitted_by_depth, cone.omitted_by_limit, cone.tests_reaching) == (0, 0, 0)
    assert possible_callers(snap, cone) == []


def test_demo_repo_has_only_the_premium_customer_inheritance() -> None:
    root = Path(__file__).resolve().parents[3] / "demo-repo"
    edges = inherits(WorkspaceGraph(root).scan(COMMIT))
    assert set(edges) == {("shop.models:PremiumCustomer", "shop.models:Customer")}
    assert edges[("shop.models:PremiumCustomer", "shop.models:Customer")].confidence == "exact"
