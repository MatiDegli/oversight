"""Nodes, ids, ranges, own source and the span index (AGENTS.md, "Graph rules")."""

from pathlib import Path

from graph_helpers import COMMIT, node, scan

from oversight.graph import WorkspaceGraph


def test_modules_classes_functions_and_methods_are_nodes(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/mod.py": """
                class Box:
                    def open(self): ...

                def helper(): ...
            """,
        },
    )
    assert {n.id: (n.kind, n.parent_id) for n in snap.nodes} == {
        "pkg": ("module", None),
        "pkg.mod": ("module", None),
        "pkg.mod:Box": ("class", "pkg.mod"),
        "pkg.mod:Box.open": ("method", "pkg.mod:Box"),
        "pkg.mod:helper": ("function", "pkg.mod"),
    }
    method = node(snap, "pkg.mod:Box.open")
    assert (method.name, method.qualname, method.path) == ("open", "Box.open", "pkg/mod.py")
    module = node(snap, "pkg.mod")
    assert (module.name, module.qualname, module.start_line, module.end_line) == (
        "mod",
        "pkg.mod",
        1,
        4,
    )


def test_a_range_starts_at_the_first_decorator(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "m.py": """
                import functools

                @functools.cache
                @staticmethod
                def cached():
                    return 1
            """
        },
    )
    cached = node(snap, "m:cached")
    assert (cached.start_line, cached.end_line) == (3, 6)


def test_nested_functions_lambdas_and_comprehensions_are_not_nodes(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "m.py": """
                def outer():
                    def inner():
                        class Local: ...
                    square = lambda x: x * x
                    return [y for y in range(3)], (z for z in range(3)), inner, square
            """
        },
    )
    assert [n.id for n in snap.nodes] == ["m", "m:outer"]


def test_definitions_under_if_and_try_at_module_level_are_nodes(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "m.py": """
                try:
                    def fast(): ...
                except ImportError:
                    pass
                if True:
                    class Flag: ...
            """
        },
    )
    assert [n.id for n in snap.nodes] == ["m", "m:Flag", "m:fast"]


def test_ids_never_hold_line_numbers(tmp_path: Path) -> None:
    source = "class A:\n    def f(self): ...\n\ndef g(): ...\n"
    graph, before = scan(tmp_path, {"m.py": source})
    (tmp_path / "m.py").write_text("\n\n\n# moved down\n" + source)
    after = graph.scan("1" * 40)
    assert [n.id for n in after.nodes] == [n.id for n in before.nodes]
    assert node(after, "m:A.f").start_line == node(before, "m:A.f").start_line + 4


def test_a_second_definition_of_a_name_gets_a_suffix(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "m.py": """
                class Customer:
                    @property
                    def email(self): ...

                    @email.setter
                    def email(self, value): ...

                    @email.deleter
                    def email(self): ...

                def build(): ...
                def build(): ...
            """
        },
    )
    ids = [n.id for n in snap.nodes]
    assert ids == [
        "m",
        "m:Customer",
        "m:Customer.email",
        "m:Customer.email#2",
        "m:Customer.email#3",
        "m:build",
        "m:build#2",
    ]
    assert node(snap, "m:Customer.email#2").start_line == 5


def test_a_package_init_is_the_package_and_test_modules_are_marked(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "shop/__init__.py": "",
            "shop/cart.py": "def total(): ...\n",
            "tests/conftest.py": "",
            "tests/test_cart.py": "def test_total(): ...\n",
            "check_test.py": "",
            "test_top.py": "",
        },
    )
    marked = {n.id: n.is_test for n in snap.nodes}
    assert marked == {
        "shop": False,
        "shop.cart": False,
        "shop.cart:total": False,
        "tests.conftest": True,
        "tests.test_cart": True,
        "tests.test_cart:test_total": True,
        "check_test": True,
        "test_top": True,
    }


def test_hidden_and_cache_directories_and_bad_module_paths_are_skipped(tmp_path: Path) -> None:
    _, snap = scan(
        tmp_path,
        {
            "ok.py": "",
            ".venv/lib/site.py": "",
            "pkg/__pycache__/x.py": "",
            "my-scripts/run.py": "",
        },
    )
    assert [n.id for n in snap.nodes] == ["ok"]


def test_a_node_own_source_excludes_the_bodies_of_nested_nodes(tmp_path: Path) -> None:
    source = """
        RATE = 1

        class Cart:
            size = 0

            def total(self):
                return 1
    """
    graph, _ = scan(tmp_path, {"m.py": source})
    ids = ["m", "m:Cart", "m:Cart.total"]
    before = {i: graph.own_hash(i) for i in ids}

    def changed(new_source: str) -> set[str]:
        (tmp_path / "m.py").unlink()
        scan(tmp_path, {"m.py": new_source})
        graph.scan("1" * 40)
        return {i for i in ids if graph.own_hash(i) != before[i]}

    assert changed(source.replace("return 1", "return 2")) == {"m:Cart.total"}
    assert changed(source.replace("size = 0", "size = 9")) == {"m:Cart"}
    assert changed(source.replace("RATE = 1", "RATE = 1\n        LIMIT = 2")) == {"m"}
    assert changed(source.replace("    def total", "    @staticmethod\n            def total")) == {
        "m:Cart.total"
    }


def test_own_source_hashes_follow_the_ast_offsets_in_a_latin_1_file(tmp_path: Path) -> None:
    def latin_1(text: str) -> bytes:
        header = "# -*- coding: latin-1 -*-\n"
        body = f"class Cart:\n    def total(self):\n        return '{text}'\n    size = 0\n"
        return (header + body).encode("latin-1")

    (tmp_path / "m.py").write_bytes(latin_1("\u00e9"))
    graph = WorkspaceGraph(tmp_path)
    graph.scan(COMMIT)
    ids = ["m", "m:Cart", "m:Cart.total"]
    before = {i: graph.own_hash(i) for i in ids}
    (tmp_path / "m.py").write_bytes(latin_1("ee"))
    graph.scan(COMMIT)
    assert {i for i in ids if graph.own_hash(i) != before[i]} == {"m:Cart.total"}


def test_the_span_index_finds_the_innermost_node(tmp_path: Path) -> None:
    graph, _ = scan(
        tmp_path,
        {
            "m.py": """
                import os

                class A:
                    x = 1

                    @property
                    def f(self):
                        def g():
                            return os.sep
                        return g

                def h(): ...
            """
        },
    )
    at = {line: graph.node_at("m.py", line) for line in range(0, 14)}
    assert at == {
        0: None,
        1: "m",
        2: "m",
        3: "m:A",
        4: "m:A",
        5: "m:A",
        6: "m:A.f",
        7: "m:A.f",
        8: "m:A.f",
        9: "m:A.f",
        10: "m:A.f",
        11: "m",
        12: "m:h",
        13: None,
    }
    assert graph.node_at("other.py", 1) is None


def test_one_line_definitions_resolve_to_the_deepest_node(tmp_path: Path) -> None:
    graph, _ = scan(tmp_path, {"m.py": "class A:\n    def f(self): return 1\n"})
    assert graph.node_at("m.py", 2) == "m:A.f"
    assert graph.node_at("m.py", 1) == "m:A"
