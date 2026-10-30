"""Own-scope AST changes decide whether modules and classes need an unprobed entry."""

from types import SimpleNamespace
from typing import cast

import pytest
from changes_helpers import EDIT, PATH, VALUE, Harness

from oversight.changes._behavior import FunctionBehavior, targets
from oversight.changes._state import ChangeRecord
from oversight.graph import change_graph, parse_version
from oversight_contracts.review import ChangeFacts


def record(before: str, after: str) -> ChangeRecord:
    base_files, head_files = {"sample.py": before}, {"sample.py": after}
    base = parse_version({"sample.py": before.encode()}, "base")
    head = parse_version({"sample.py": after.encode()}, "head", previous=base)
    return cast(
        ChangeRecord,
        SimpleNamespace(
            base=base,
            head=head,
            graph=change_graph(base, head),
            base_files=base_files,
            head_files=head_files,
        ),
    )


@pytest.mark.parametrize(
    "added",
    [
        "import pathlib\n",
        "from pathlib import Path\n",
        "from pathlib import Path as P\n",
        "import os.path\n",
        "import os.path as path\n",
        "import os, pathlib\n",
    ],
)
def test_added_fresh_import_has_no_module_entry(added):
    rec = record("LIMIT = 1\n", added + "LIMIT = 1\n")
    assert rec.graph.nodes_modified == ["sample"]
    assert targets(rec) == ()


@pytest.mark.parametrize(
    "before",
    [
        "Path = 1\n",
        "Path: int\n",
        "def Path(): pass\n",
        "async def Path(): pass\n",
        "class Path: pass\n",
        "import os as Path\n",
        "from other import Path\n",
        "if ready:\n    Path = 1\n",
        "for Path in items: pass\n",
        "with resource as Path: pass\n",
        "try: pass\nexcept Error as Path: pass\n",
        "match item:\n    case {'key': Path}: pass\n",
        "match item:\n    case {**Path}: pass\n",
        "[Path := item for item in items]\n",
        "def f():\n    global Path\n    Path = 1\n",
        "def f(value=(Path := 1)): pass\n",
        "@decorate(Path := 1)\nclass Box: pass\n",
        "from other import *\n",
    ],
)
def test_added_import_cannot_rebind_a_base_module_name(before):
    rec = record(before, before + "from pathlib import Path\n")
    assert targets(rec) == (FunctionBehavior("sample", reason="unsupported signature"),)


@pytest.mark.parametrize(
    "before",
    [
        "def f(Path): pass\n",
        "def f():\n    Path = 1\n",
        "class Box:\n    Path = 1\n",
        "[Path for Path in items]\n",
        "f = lambda Path: Path\n",
    ],
)
def test_nested_local_binding_does_not_block_a_fresh_import(before):
    assert targets(record(before, before + "from pathlib import Path\n")) == ()


@pytest.mark.parametrize(
    "before,after",
    [
        ("import pathlib\nLIMIT = 1\n", "LIMIT = 1\n"),
        ("import pathlib\n", "import os\n"),
        ("import os as alias\n", "import pathlib as alias\n"),
        ("LIMIT = 1\n", "from pathlib import *\nLIMIT = 1\n"),
        ("LIMIT = 1\n", "LIMIT = 2\n"),
        ('"""Before."""\n', '"""After."""\n'),
        ("__all__ = ['one']\n", "__all__ = ['two']\n"),
        ("run(1)\n", "run(2)\n"),
        ("import os\nimport pathlib\n", "import pathlib\nimport os\n"),
        ("import os.path\n", "import os.path\nimport os\n"),
    ],
)
def test_other_module_changes_keep_the_original_reason(before, after):
    assert targets(record(before, after)) == (
        FunctionBehavior("sample", reason="unsupported signature"),
    )


def test_comment_only_module_change_has_no_entry():
    rec = record("LIMIT = 1  # before\n", "LIMIT = 1  # after\n")
    assert rec.graph.nodes_modified == ["sample"]
    assert targets(rec) == ()


def test_comment_only_class_body_change_has_no_entry():
    before = "class Box:\n    # before\n    size = 1\n"
    rec = record(before, before.replace("before", "after"))
    assert rec.graph.nodes_modified == ["sample:Box"]
    assert targets(rec) == ()


@pytest.mark.parametrize(
    "before,after",
    [
        ("class Box(Old): pass\n", "class Box(New): pass\n"),
        ("@old\nclass Box: pass\n", "@new\nclass Box: pass\n"),
        ("class Box(metaclass=Old): pass\n", "class Box(metaclass=New): pass\n"),
        ("class Box:\n    size = 1\n", "class Box:\n    size = 2\n"),
        ('class Box:\n    "Before."\n', 'class Box:\n    "After."\n'),
        ("class Box[T]: pass\n", "class Box[U]: pass\n"),
    ],
)
def test_changed_class_scope_keeps_the_original_reason(before, after):
    assert targets(record(before, after)) == (
        FunctionBehavior("sample:Box", reason="method or class (not probed yet)"),
    )


@pytest.mark.parametrize("side", ["base_files", "head_files"])
@pytest.mark.parametrize("kind", ["module", "class"])
def test_unparseable_recorded_source_keeps_the_original_reason(side, kind):
    before = "LIMIT = 1  # before\n" if kind == "module" else "class Box:\n    # before\n    pass\n"
    rec = record(before, before.replace("before", "after"))
    # Retained graph nodes can outlive a failed parse of the actual recorded source.
    getattr(rec, side)["sample.py"] = "def broken(:\n"
    node_id = "sample" if kind == "module" else "sample:Box"
    reason = "unsupported signature" if kind == "module" else "method or class (not probed yet)"
    assert targets(rec) == (FunctionBehavior(node_id, reason=reason),)


def test_nested_definitions_are_removed_from_own_scope_comparison():
    before = "if ready:\n    class Box:\n        size = 1  # before\n        def f(self): pass\n"
    after = before.replace("before", "after") + "    def added(): pass\n"
    found = targets(record(before, after))
    assert found == (FunctionBehavior("sample:added", reason="added (no base version)"),)


def test_duplicate_and_nested_classes_are_matched_on_each_side():
    before = "class Outer:\n    class Box:\n        # before\n        pass\nclass Outer: pass\n"
    after = "# Shifted lines\n" + before.replace("before", "after")
    rec = record(before, after)
    assert "sample:Outer.Box" in rec.graph.nodes_modified
    assert targets(rec) == ()


def test_added_and_removed_nodes_keep_their_reasons():
    rec = record("class Old: pass\ndef old(): pass\n", "class New: pass\ndef new(): pass\n")
    assert {item.node_id: item.reason for item in targets(rec)} == {
        "sample:New": "added (no base version)",
        "sample:Old": "removed (no proposed version)",
        "sample:new": "added (no base version)",
        "sample:old": "removed (no proposed version)",
    }


@pytest.fixture
def session_c() -> bool:
    return True


@pytest.mark.anyio
async def test_import_and_probed_function_edit_no_longer_list_module(h: Harness):
    h.manager._ctx.behavior_diff = True
    await h.run_agent(1, (PATH, "def value()", "from pathlib import Path\n\ndef value()"), EDIT)
    rec = h.manager._agents["agent-1"]
    assert "shop.mod" in rec.graph.nodes_modified
    [result] = rec.behavior_diffs[1].functions
    assert result.node_id == VALUE and result.reason is None
    assert result.compared == result.differing == 1
    [facts] = h.payloads(ChangeFacts)
    assert facts.behavior_diff is not None
    assert [row.node_id for row in facts.behavior_diff] == [VALUE]
