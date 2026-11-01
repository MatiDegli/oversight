"""Structural observations from real parser graphs, without runtime or provider calls."""

import ast
from pathlib import Path
from random import Random

import pytest

from oversight.changes import _shape
from oversight.changes._shape import FUNCTION_LINES, MAX_FACTS, ShapeEvidence, compare_shape
from oversight.graph import parse_version
from oversight_contracts.graph import GraphSnapshot
from oversight_contracts.review import ShapeFact


def _graph(files: dict[str, str]) -> GraphSnapshot:
    return parse_version({path: source.encode() for path, source in files.items()}, "c1").graph


def _compare(base: GraphSnapshot, head: GraphSnapshot, **kwargs: int) -> ShapeEvidence:
    result = compare_shape(
        base, head, [node.id for node in head.nodes], base_tree="base", head_tree="head", **kwargs
    )
    for fact in result.facts:
        assert ShapeFact.model_validate(fact.model_dump()) == fact
        assert fact.evidence
        assert all(ref.git_object and ref.path for ref in fact.evidence)
    return result


def _function(lines: int, *, decorated: bool = False) -> str:
    prefix = "@decorate\n" if decorated else ""
    return prefix + "def work():\n" + "    pass\n" * (lines - 1 - int(decorated))


@pytest.mark.parametrize(
    ("before", "after", "decorated", "message"),
    [
        (None, 80, False, None),
        (None, 81, True, "`work` is new and 81 lines long (limit 80)."),
        (80, 81, False, "`work` grew from 80 to 81 lines, past 80."),
        (81, 82, False, "`work` was already over 80 lines and grew from 81 to 82."),
        (82, 81, False, None),
        (81, 81, False, None),
    ],
)
def test_function_length(
    before: int | None, after: int, decorated: bool, message: str | None
) -> None:
    base = _graph({"app.py": _function(before)} if before else {})
    head = _graph({"app.py": _function(after, decorated=decorated)})
    result = _compare(base, head)
    assert result.complete and result.problems == ()
    assert [fact.message for fact in result.facts] == ([message] if message else [])
    if message:
        fact = result.facts[0]
        assert (fact.kind, fact.node_id, fact.path) == ("function_length", "app:work", "app.py")
        assert [(ref.git_object, ref.path) for ref in fact.evidence] == [
            ("head", "app.py"),
            *([("base", "app.py")] if before else []),
        ]


def test_only_changed_functions_and_methods_are_checked() -> None:
    head = _graph({"app.py": _function(81)})
    assert compare_shape(_graph({}), head, [], base_tree="b", head_tree="h").facts == ()
    method = "class Service:\n" + "".join("    " + line for line in _function(81).splitlines(True))
    result = _compare(_graph({}), _graph({"app.py": method}))
    assert result.facts[0].node_id == "app:Service.work"
    assert result.facts[0].message == "`Service.work` is new and 81 lines long (limit 80)."
    # A long class/module without a long function is not a size observation.
    assert _compare(_graph({}), _graph({"app.py": "class C:\n" + "    x = 1\n" * 81})).facts == ()


def test_limit_can_be_overridden() -> None:
    assert FUNCTION_LINES == 80
    result = _compare(_graph({}), _graph({"app.py": _function(4)}), limit=3)
    assert result.facts[0].message == "`work` is new and 4 lines long (limit 3)."


@pytest.mark.parametrize(
    ("base_files", "head_files", "expected"),
    [
        ({"a.py": "", "b.py": ""}, {"a.py": "import b\n", "b.py": ""}, True),
        ({"b.py": ""}, {"a.py": "import b\n", "b.py": ""}, False),
        ({"a.py": ""}, {"a.py": "import b\n", "b.py": ""}, False),
        ({"a.py": "import b\n", "b.py": ""}, {"a.py": "import b\n", "b.py": ""}, False),
    ],
)
def test_import_added(
    base_files: dict[str, str], head_files: dict[str, str], expected: bool
) -> None:
    result = _compare(_graph(base_files), _graph(head_files))
    assert len(result.facts) == int(expected)
    if expected:
        fact = result.facts[0]
        assert (fact.kind, fact.path, fact.node_id) == ("import_added", "a.py", "a")
        assert fact.message == "`a` now imports `b`."
        assert [(ref.git_object, ref.path) for ref in fact.evidence] == [("head", "a.py")]


@pytest.mark.parametrize(
    "source", ["if TYPE_CHECKING:\n    import b\n", "def f():\n    import b\n"]
)
def test_any_scope_import_is_an_edge(source: str) -> None:
    base = _graph({"a.py": "", "b.py": ""})
    head = _graph({"a.py": source, "b.py": ""})
    assert [(edge.src, edge.dst) for edge in head.edges if edge.kind == "imports"] == [("a", "b")]
    assert [fact.message for fact in _compare(base, head).facts] == ["`a` now imports `b`."]


@pytest.mark.parametrize("names", ["ab", "abc", "abcdefgh"])
def test_new_cycle_and_truncated_names(names: str) -> None:
    files = {
        f"{name}.py": f"import {names[(i + 1) % len(names)]}\n" for i, name in enumerate(names)
    }
    result = _compare(_graph({}), _graph(files))
    assert len(result.facts) == 1  # New modules only contribute the cycle.
    fact = result.facts[0]
    shown = ", ".join(names[:6]) + (f", +{len(names) - 6} more" if len(names) > 6 else "")
    assert (fact.kind, fact.node_id, fact.path) == ("import_cycle", "a", "a.py")
    assert fact.message == (
        f"Modules {{{shown}}} import each other in a cycle (new: `a` imports `b`)."
    )
    assert [(ref.git_object, ref.path) for ref in fact.evidence] == [("head", "a.py")]


def test_existing_cycle_and_cycle_gaining_a_member() -> None:
    base = _graph({"a.py": "import b\n", "b.py": "import a\n"})
    assert _compare(base, base).facts == ()
    head = _graph({"a.py": "import b\n", "b.py": "import a\nimport c\n", "c.py": "import a\n"})
    result = _compare(base, head)
    assert len(result.facts) == 1
    assert result.facts[0].node_id == "b"
    assert result.facts[0].message == (
        "Modules {a, b, c} import each other in a cycle (new: `b` imports `c`)."
    )


def test_new_edge_inside_existing_cycle_is_reported() -> None:
    files = {"a.py": "import b\n", "b.py": "import c\n", "c.py": "import a\n"}
    result = _compare(_graph(files), _graph(files | {"c.py": "import a\nimport b\n"}))
    assert [fact.kind for fact in result.facts] == ["import_added", "import_cycle"]
    assert result.facts[1].message.endswith("(new: `c` imports `b`).")


def test_self_import_has_no_edge_or_fact() -> None:
    head = _graph({"a.py": "import a\n"})
    assert head.edges == []
    assert _compare(_graph({"a.py": ""}), head).facts == ()


def test_test_nodes_excluded_from_every_check() -> None:
    base = _graph({"app.py": "", "test_app.py": ""})
    head = _graph({"app.py": "import test_app\n", "test_app.py": "import app\n" + _function(81)})
    assert any(node.is_test for node in head.nodes)
    assert len([edge for edge in head.edges if edge.kind == "imports"]) == 2
    assert _compare(base, head).facts == ()


def test_order_and_shuffled_input_are_deterministic() -> None:
    base = _graph({"a.py": "", "b.py": "", "c.py": "", "z.py": ""})
    head = _graph(
        {"a.py": "import c\nimport b\n", "b.py": "import a\n", "c.py": "", "z.py": _function(81)}
    )
    expected = _compare(base, head)
    assert [(f.kind, f.path) for f in expected.facts] == [
        ("function_length", "z.py"),
        ("import_added", "a.py"),
        ("import_added", "a.py"),
        ("import_added", "b.py"),
        ("import_cycle", "a.py"),
    ]
    assert [fact.message for fact in expected.facts[1:3]] == [
        "`a` now imports `b`.",
        "`a` now imports `c`.",
    ]
    random = Random(73)
    for _ in range(5):
        for graph in (base, head):
            random.shuffle(graph.nodes)
            random.shuffle(graph.edges)
        assert _compare(base, head) == expected


@pytest.mark.parametrize("extra", [0, 2])
def test_fact_limit_without_parse_errors(extra: int) -> None:
    files = {f"m{i:02}.py": _function(81) for i in range(MAX_FACTS + extra)}
    result = _compare(_graph({}), _graph(files))
    assert len(result.facts) == MAX_FACTS
    assert result.complete == (extra == 0)
    assert result.problems == (("shape: 2 more observations omitted",) if extra else ())


def test_fact_limit_and_parse_errors_report_both_gaps() -> None:
    assert MAX_FACTS == 24
    files = {f"m{i:02}.py": _function(81) for i in range(MAX_FACTS + 2)}
    base = _graph({"bad.py": "def broken(\n"})
    head = _graph(files | {"bad.py": "def broken(\n", "other.py": "def broken(\n"})
    result = _compare(base, head)
    assert len(result.facts) == MAX_FACTS
    assert result.facts[-1].path == "m23.py"
    assert not result.complete
    assert result.problems == (
        "shape: 2 more observations omitted",
        "shape: 2 files kept their last good parse",
    )


@pytest.mark.parametrize("base_error", [True, False])
def test_parse_errors_without_findings_are_incomplete(base_error: bool) -> None:
    bad, clean = _graph({"bad.py": "def broken(\n"}), _graph({})
    result = _compare(bad, clean) if base_error else _compare(clean, bad)
    assert result == ShapeEvidence((), False, ("shape: 1 files kept their last good parse",))
    assert _compare(clean, clean) == ShapeEvidence((), True, ())


def test_deep_import_chain_and_multiple_components() -> None:
    count = 1100
    files = {f"m{i}.py": f"import m{i + 1}\n" for i in range(count)}
    files[f"m{count}.py"] = f"import m{count - 1}\n"
    files |= {"a.py": "import b\n", "b.py": "import a\n"}
    result = _compare(_graph({}), _graph(files))
    assert [fact.node_id for fact in result.facts] == ["a", "m1099"]


def test_shape_has_no_backend_imports() -> None:
    tree = ast.parse(Path(_shape.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name.split(".")[0] != "oversight" for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            assert (node.module or "").split(".")[0] != "oversight"
