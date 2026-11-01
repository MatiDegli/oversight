"""Structural duplicate evidence: exact matching, new pairs and bounded coverage."""

import ast
import random
from collections.abc import Collection
from pathlib import Path

import pytest

from oversight.changes import _shape_duplicates as duplicates
from oversight.graph import parse_version
from oversight_contracts.graph import GraphSnapshot, ParseError
from oversight_contracts.review import ShapeFact

BODY = (
    "    result = value + 7\n"
    "    cleaned = clean(result)\n"
    "    saved = cleaned.total\n"
    "    return saved\n"
)


def function(name: str, body: str = BODY, signature: str = "value=3") -> str:
    return f"def {name}({signature}):\n{body}"


def graph(files: dict[str, str]) -> GraphSnapshot:
    return parse_version({path: text.encode() for path, text in files.items()}, "c1").graph


def compare(
    head_files: dict[str, str],
    base_files: dict[str, str] | None = None,
    changed: Collection[str] | None = None,
) -> duplicates.DuplicateEvidence:
    head = graph(head_files)
    return duplicates.compare_duplicates(
        graph(base_files or {}),
        head,
        base_files or {},
        head_files,
        changed if changed is not None else [node.id for node in head.nodes],
        head_tree="head-tree",
    )


def test_renamed_locals_match_with_exact_message_and_evidence() -> None:
    base = {"a.py": function("original")}
    renamed = BODY.replace("value", "item").replace("result", "first")
    renamed = renamed.replace("cleaned", "second").replace("saved", "third")
    files = {**base, "b.py": function("copy", renamed, "item=3")}
    result = compare(files, base, ["b:copy"])
    assert result.complete and result.problems == ()
    assert len(result.facts) == 1
    fact = result.facts[0]
    assert (fact.path, fact.node_id, fact.kind) == ("b.py", "b:copy", "duplicate_logic")
    assert fact.message == "`copy` has the same logic as `original` (a.py)."
    assert ShapeFact.model_validate_json(fact.model_dump_json()) == fact
    assert [(ref.git_object, ref.path) for ref in fact.evidence] == [("head-tree", "b.py")]


def test_method_matches_function() -> None:
    source = function("original", signature="subject, value=3")
    method = function("copy", signature="self, value=3")
    source += "class Example:\n" + "\n".join("    " + line for line in method.splitlines())
    result = compare({"app.py": source}, changed=["app:Example.copy"])
    assert result.facts[0].message == "`Example.copy` has the same logic as `original` (app.py)."


def test_async_pair_matches() -> None:
    source = "async " + function("a") + "async " + function("b")
    assert len(compare({"app.py": source}).facts) == 1


@pytest.mark.parametrize("parameters", ["T", "**T", "*T"])
def test_generic_type_parameter_names_are_bindings(parameters: str) -> None:
    source = function(f"a[{parameters}]") + function(f"b[{parameters.replace('T', 'U')}]")
    assert len(compare({"app.py": source}).facts) == 1


def test_generic_body_type_parameter_references_are_renamed() -> None:
    body = BODY.replace("clean(result)", "cast(T, result)")
    source = function("a[T]", body) + function("b[U]", body.replace("cast(T", "cast(U"))
    assert len(compare({"app.py": source}).facts) == 1


def test_generic_bounds_are_annotations() -> None:
    source = function("a[T: int]") + function("b[T: str]")
    assert len(compare({"app.py": source}).facts) == 1


def test_generic_and_non_generic_signatures_stay_distinct() -> None:
    assert compare({"app.py": function("a[T]") + function("b")}).facts == ()


def test_nested_class_type_parameters_stay_unrenamed() -> None:
    body = "    class Box[T]:\n        pass\n" + BODY
    source = function("a", body) + function("b", body.replace("Box[T]", "Box[U]"))
    assert compare({"app.py": source}).facts == ()


@pytest.mark.parametrize(
    ("body", "signature"),
    [
        (BODY.replace("+ 7", "+ 8"), "value=3"),
        (BODY.replace("clean(", "wash("), "value=3"),
        (BODY.replace(".total", ".amount"), "value=3"),
        (BODY.replace("+ 7", "- 7"), "value=3"),
        (BODY, "value=4"),
        (BODY.replace("    return saved", "    if saved:\n        return saved"), "value=3"),
    ],
    ids=["literal", "called-name", "attribute", "operator", "default", "branch"],
)
def test_structural_differences_do_not_match(body: str, signature: str) -> None:
    result = compare({"app.py": function("a") + function("b", body, signature)})
    assert result == duplicates.DuplicateEvidence((), True, ())


def test_short_functions_are_ignored() -> None:
    body = '    "docstring"\n    first = value\n    second = first\n    return second\n'
    assert compare({"app.py": function("a", body) + function("b", body)}).facts == ()


@pytest.mark.parametrize("test_is_changed", [False, True])
def test_test_modules_are_ignored_on_either_side(test_is_changed: bool) -> None:
    base = {"app.py": function("a")} if test_is_changed else {"test_app.py": function("a")}
    added = {"test_app.py": function("b")} if test_is_changed else {"app.py": function("b")}
    assert compare({**base, **added}, base).facts == ()


def test_nested_duplicate_is_not_a_graph_node() -> None:
    nested = "    " + function("hidden").replace("\n", "\n    ").rstrip() + "\n"
    source = function("original") + "def outer():\n" + nested + "    return hidden\n"
    assert compare({"app.py": source}).facts == ()


def test_decorators_annotations_and_docstrings_are_removed() -> None:
    left = '@first\ndef a(value: int = 3) -> int:\n    "old docs"\n' + BODY
    right = '@second(9)\ndef b(value: str = 3) -> str:\n    "new docs"\n' + BODY
    right = right.replace("result =", "result: float =")
    assert len(compare({"app.py": left + right}).facts) == 1


def test_unchanged_existing_pair_gives_nothing() -> None:
    files = {"app.py": function("a") + function("b")}
    assert compare(files, files, []).facts == ()


@pytest.mark.parametrize("metadata_only", [False, True])
def test_same_old_fingerprint_is_not_changed_logic(metadata_only: bool) -> None:
    base = {"app.py": function("a") + function("b")}
    replacement = (
        '@decorator\ndef b(value: int = 3) -> int:\n    "docs"\n' + BODY
        if metadata_only
        else function("b", BODY.replace(" + 7", "+7  # formatting"))
    )
    head = {"app.py": function("a") + replacement}
    assert compare(head, base, ["app:b"]).facts == ()


def test_edit_into_existing_copy_is_changed_logic() -> None:
    base = {"app.py": function("a") + function("b", BODY.replace("+ 7", "+ 9"))}
    head = {"app.py": function("a") + function("b")}
    result = compare(head, base, ["app:b"])
    assert result.facts[0].node_id == "app:b"
    assert result.facts[0].message == "`b` has the same logic as `a` (app.py)."


def test_two_new_functions_anchor_on_least_id() -> None:
    result = compare({"app.py": function("z") + function("a")})
    assert len(result.facts) == 1
    assert result.facts[0].node_id == "app:a"
    assert result.facts[0].message == "`a` has the same logic as `z` (app.py)."


def test_five_members_show_three_names_then_remaining_count() -> None:
    files = {"app.py": "".join(function(name) for name in "edcba")}
    assert compare(files).facts[0].message == (
        "`a` has the same logic as `b` (app.py), `c` (app.py), `d` (app.py), +1 more."
    )
    files["app.py"] += function("f")
    assert compare(files).facts[0].message.endswith("`d` (app.py), +2 more.")


def test_unparsable_head_text_is_incomplete() -> None:
    files = {"app.py": function("a") + function("b")}
    result = duplicates.compare_duplicates(
        graph({}), graph(files), {}, {"app.py": "def broken("}, ["app:a"], head_tree="tree"
    )
    assert result == duplicates.DuplicateEvidence(
        (), False, ("shape: 1 files were not compared for duplicate logic",)
    )


def test_graph_parse_error_excludes_stale_nodes() -> None:
    files = {"app.py": function("a") + function("b")}
    head = graph(files).model_copy(
        update={"parse_errors": [ParseError(path="app.py", line=1, message="stale")]}
    )
    result = duplicates.compare_duplicates(graph({}), head, {}, files, ["app:a"], head_tree="tree")
    assert result == duplicates.DuplicateEvidence(
        (), False, ("shape: 1 files were not compared for duplicate logic",)
    )


@pytest.mark.parametrize("failure", [ValueError, RecursionError, MemoryError])
def test_parser_resource_failures_are_incomplete(
    monkeypatch: pytest.MonkeyPatch, failure: type[Exception]
) -> None:
    files = {"app.py": function("a")}
    base, head = graph({}), graph(files)

    def fail(*args: object, **kwargs: object) -> ast.Module:
        raise failure("cannot parse")

    monkeypatch.setattr(ast, "parse", fail)
    result = duplicates.compare_duplicates(base, head, {}, files, ["app:a"], head_tree="tree")
    assert result.problems == ("shape: 1 files were not compared for duplicate logic",)
    assert not result.complete


def test_bad_base_has_no_fingerprint_and_marks_incomplete() -> None:
    files = {"app.py": function("a") + function("b")}
    result = duplicates.compare_duplicates(
        graph(files), graph(files), {"app.py": "def broken("}, files, ["app:b"], head_tree="tree"
    )
    assert result.facts[0].node_id == "app:b"
    assert not result.complete
    assert result.problems == ("shape: 1 files were not compared for duplicate logic",)


def test_function_limit_selects_in_id_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(duplicates, "MAX_FUNCTIONS", 2)
    result = compare({"app.py": "".join(function(name) for name in "cba")})
    assert result.facts[0].node_id == "app:a"
    assert result.facts[0].message == "`a` has the same logic as `b` (app.py)."
    assert not result.complete
    assert result.problems == ("shape: duplicate check covered 2 of 3 functions",)


def test_fact_limit_keeps_path_then_id_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(duplicates, "MAX_FACTS", 1)
    files = {"z.py": function("a") + function("b")}
    files["a.py"] = function("z", BODY.replace("+ 7", "+ 9")) + function(
        "y", BODY.replace("+ 7", "+ 9")
    )
    result = compare(files)
    assert [fact.node_id for fact in result.facts] == ["a:y"]
    assert not result.complete
    assert result.problems == ("shape: 1 more duplicate observations omitted",)


def test_shuffled_inputs_are_deterministic_and_unmodified() -> None:
    files = {"a.py": function("a") + function("b"), "z.py": function("z")}
    files["a.py"] += "def clean(item):\n    return item\n"
    head = graph(files)
    assert head.edges
    before = head.model_dump_json()
    expected = duplicates.compare_duplicates(
        graph({}), head, {}, files, ["a:b", "z:z"], head_tree="tree"
    )
    rng = random.Random(17)
    for _ in range(5):
        nodes, edges, paths = list(head.nodes), list(head.edges), list(files)
        rng.shuffle(nodes)
        rng.shuffle(edges)
        rng.shuffle(paths)
        shuffled = head.model_copy(update={"nodes": nodes, "edges": edges})
        actual = duplicates.compare_duplicates(
            graph({}), shuffled, {}, {p: files[p] for p in paths}, ["z:z", "a:b"], head_tree="tree"
        )
        assert actual == expected
    assert head.model_dump_json() == before


def test_nested_bindings_and_signature_order_are_normalized() -> None:
    body = (
        "    def inner(item):\n        local = item + value\n        return local\n"
        "    result = [inner(entry) for entry in rest]\n    return result\n"
    )
    renamed = body.replace("inner", "helper").replace("item", "arg").replace("local", "temp")
    renamed = renamed.replace("entry", "element").replace("result", "answer")
    signature = "value, /, regular=3, *rest, flag=True, **options"
    source = function("a", body, signature) + function("b", renamed, signature)
    assert len(compare({"app.py": source}).facts) == 1


def test_nested_statements_count_toward_minimum() -> None:
    body = "    if value:\n        one = value\n        return one\n    return None\n"
    assert len(compare({"app.py": function("a", body) + function("b", body)}).facts) == 1


def test_all_parameter_kinds_are_renamed_in_signature_order() -> None:
    body = (
        "    first = value + regular\n    second = rest[0] + flag\n"
        "    third = options.get('key')\n    return first, second, third\n"
    )
    renamed = body
    for old, new in [
        ("value", "v"),
        ("regular", "r"),
        ("rest", "args"),
        ("flag", "enabled"),
        ("options", "kwargs"),
    ]:
        renamed = renamed.replace(old, new)
    source = function("a", body, "value, /, regular=3, *rest, flag=True, **options")
    source += function("b", renamed, "v, /, r=3, *args, enabled=True, **kwargs")
    assert len(compare({"app.py": source}).facts) == 1


def test_missing_head_source_is_incomplete() -> None:
    files = {"app.py": function("a")}
    result = duplicates.compare_duplicates(
        graph({}), graph(files), {}, {}, ["app:a"], head_tree="tree"
    )
    assert result == duplicates.DuplicateEvidence(
        (), False, ("shape: 1 files were not compared for duplicate logic",)
    )


def test_base_files_are_parsed_only_for_changed_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    files = {"a.py": function("a"), "b.py": function("b")}
    base, head = graph(files), graph(files)
    calls: list[str] = []
    parse = ast.parse

    def track(source: str) -> ast.Module:
        calls.append(source)
        return parse(source)

    monkeypatch.setattr(ast, "parse", track)
    result = duplicates.compare_duplicates(base, head, files, files, ["b:b"], head_tree="tree")
    assert result == duplicates.DuplicateEvidence((), True, ())
    assert calls.count(files["a.py"]) == 1
    assert calls.count(files["b.py"]) == 2


def test_module_has_no_oversight_imports() -> None:
    tree = ast.parse(Path(duplicates.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0
            assert node.module is not None and node.module.split(".")[0] != "oversight"
        elif isinstance(node, ast.Import):
            assert all(alias.name.split(".")[0] != "oversight" for alias in node.names)
