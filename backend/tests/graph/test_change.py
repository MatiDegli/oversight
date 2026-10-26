"""Change graphs: a change's version against its base, both parsed from file sets."""

from graph_helpers import COMMIT, version

from oversight.graph import change_graph, parse_version
from oversight_contracts.graph import ChangeGraph


def change(base: dict[str, str], head: dict[str, str]) -> ChangeGraph:
    return change_graph(version(base), version(head))


def test_a_change_graph_lists_added_modified_and_removed_nodes() -> None:
    base = {"m.py": "def f():\n    return 1\ndef g(): ...\ndef old():\n    g()\n"}
    head = {"m.py": "def new():\n    f()\ndef f():\n    return 2\ndef g(): ...\n"}
    found = change(base, head)
    assert [n.id for n in found.nodes_added] == ["m:new"]
    # g moved down two lines, but its own source is the same.
    assert found.nodes_modified == ["m:f"]
    assert found.nodes_removed == ["m:old"]
    assert [e.id for e in found.edges_added] == ["calls:m:new->m:f"]
    assert found.edges_removed == ["calls:m:old->m:g"]


def test_new_calls_in_modified_code_are_added_edges() -> None:
    base = {"m.py": "def f(): ...\ndef g(): ...\n"}
    head = {"m.py": "def f():\n    g()\ndef g(): ...\n"}
    found = change(base, head)
    assert found.nodes_modified == ["m:f"]
    assert [e.id for e in found.edges_added] == ["calls:m:f->m:g"]


def test_a_change_inside_a_nested_function_marks_only_the_enclosing_function() -> None:
    source = """
        class Cart:
            def total(self):
                def line(item):
                    return item * 1
                return sum(line(i) for i in self.items)

            def size(self):
                return 0
    """
    found = change({"m.py": source}, {"m.py": source.replace("item * 1", "item * 2")})
    assert found.nodes_modified == ["m:Cart.total"]
    assert found.nodes_added == [] and found.nodes_removed == []


def test_a_new_module_level_constant_marks_the_module() -> None:
    base = {"m.py": "RATE = 1\n\n\ndef f():\n    return RATE\n"}
    head = {"m.py": "RATE = 1\nLIMIT = 2\n\n\ndef f():\n    return RATE\n"}
    assert change(base, head).nodes_modified == ["m"]


def test_adding_or_removing_a_definition_does_not_mark_its_parent() -> None:
    base = {"m.py": "def f(): ...\n\n\nclass Cart:\n    size = 0\n\n    def total(self): ...\n"}
    head = {
        "m.py": "def f(): ...\n\n\ndef g(): ...\n\n\nclass Cart:\n    size = 0\n\n"
        "    def count(self): ...\n"
    }
    found = change(base, head)
    assert [n.id for n in found.nodes_added] == ["m:Cart.count", "m:g"]
    assert found.nodes_removed == ["m:Cart.total"]
    assert found.nodes_modified == []


def test_a_blank_line_inside_a_multi_line_string_counts_as_a_change() -> None:
    base = {"m.py": 'def f():\n    return """first\n\nlast"""\n\n\ndef g(): ...\n'}
    head = {"m.py": 'def f():\n    return """first\nlast"""\n\n\ndef g(): ...\n'}
    assert change(base, head).nodes_modified == ["m:f"]


def test_a_nested_definition_beside_a_multi_line_string_marks_no_parent() -> None:
    text = 'class A:\n    DOC = """a\n\nb"""\n\n    def one(self): ...\n'
    nested = text + '\n    def two(self):\n        return """x\n\ny"""\n'
    found = change({"m.py": text}, {"m.py": nested})
    assert [n.id for n in found.nodes_added] == ["m:A.two"]
    assert found.nodes_modified == []
    assert change({"m.py": nested}, {"m.py": text}).nodes_modified == []


def test_a_version_ignores_files_that_are_not_importable_modules() -> None:
    files = {
        "pkg/__init__.py": b"",
        "pkg.py": b"def shadowed(): ...\n",
        ".venv/lib.py": b"def hidden(): ...\n",
        "pkg/__pycache__/x.py": b"",
        "my-scripts/run.py": b"",
        "README.md": b"# readme\n",
    }
    graph = parse_version(files, COMMIT).graph
    assert [n.id for n in graph.nodes] == ["pkg"]


def test_a_file_that_fails_to_parse_keeps_its_previous_parse() -> None:
    base = version({"m.py": "def f(): ...\n", "n.py": "def g(): ...\n"})
    head = version({"m.py": "def f(:\n", "n.py": "def g():\n    return 1\n"}, previous=base)
    assert [e.path for e in head.graph.parse_errors] == ["m.py"]
    found = change_graph(base, head)
    assert found.nodes_removed == [] and found.nodes_modified == ["n:g"]
    fresh = version({"m.py": "def f(:\n"})
    assert [n.id for n in fresh.graph.nodes] == []
