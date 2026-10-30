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
        "import os as path\n",
        "from os import path\n",
        "import os, pathlib\n",
        "from pathlib import Path, PurePath as P\n",
        "from uuid import uuid4\n",
        "from uuid import uuid4 as new_id\n",
    ],
)
def test_added_fresh_stdlib_import_has_no_module_entry(added):
    rec = record("LIMIT = 1\n", added + "LIMIT = 1\n")
    assert rec.graph.nodes_modified == ["sample"]
    assert targets(rec) == ()


@pytest.mark.parametrize("docstring", ['"""Module documentation."""', '""'])
def test_allowlisted_import_before_existing_docstring_keeps_module_entry(docstring):
    before = docstring + "\ndef f(): return __doc__\n"
    rec = record(before, "from pathlib import Path\n" + before)
    assert rec.graph.nodes_modified == ["sample"]
    assert targets(rec) == (FunctionBehavior("sample", reason="unsupported signature"),)


@pytest.mark.parametrize("docstring", ['"""Module documentation."""', '""'])
def test_allowlisted_import_after_existing_docstring_has_no_module_entry(docstring):
    before = docstring + "\ndef f(): return __doc__\n"
    after = docstring + "\nfrom pathlib import Path\ndef f(): return __doc__\n"
    rec = record(before, after)
    assert rec.graph.nodes_modified == ["sample"]
    assert targets(rec) == ()


def test_docstring_expression_must_stay_identical_even_when_cleaned_text_matches():
    rec = record('"""Documentation."""\n', '"""  Documentation."""\nimport pathlib\n')
    assert targets(rec) == (FunctionBehavior("sample", reason="unsupported signature"),)


@pytest.mark.parametrize(
    "before",
    [
        "snapshot = globals()\n",
        "def f(): return vars()\n",
        "f = lambda: locals()\n",
        "class Box:\n    names = dir()\n",
        "modules = [__import__(name) for name in names]\n",
        "def f(): return namespace.__dict__\n",
        "globals = 1\n",
        "def f(): return eval('Path').__name__\n",
        "def f(): return eval(code)\n",
        'value = getattr(x, "Path")\n',
        "def f(): return getattr(x, name)\n",
        "def f(): exec(code)\n",
        "def f(): return compile(code, filename, mode)\n",
        "f = lambda: function.__globals__\n",
        "def f(): return frame.f_globals\n",
        "def f(): return sys.modules\n",
        "frame = sys._getframe()\n",
        "import importlib\n",
        "import inspect as inspection\n",
        "def f():\n    import importlib.util as loader\n",
        "from inspect import signature as sig\n",
        "from importlib.util import find_spec\n",
        "from . import inspect\n",
    ],
)
@pytest.mark.parametrize("edit", ["import", "comment"])
def test_base_namespace_access_keeps_module_entry(before, edit):
    added = "from pathlib import Path\n" if edit == "import" else "# New comment\n"
    rec = record(before, added + before)
    assert rec.graph.nodes_modified == ["sample"]
    assert targets(rec) == (FunctionBehavior("sample", reason="unsupported signature"),)


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
        "def f(): return Path('data')\n",
        "def f():\n    def nested(): return Path('data')\n",
        "class Box:\n    def f(self): return Path('data')\n",
        "def f():\n    Path = 1\n",
        "def f():\n    del Path\n",
        "class Box:\n    Path = 1\n",
        "[Path for Path in items]\n",
        "(Path(item) for item in items)\n",
        "f = lambda Path: Path\n",
    ],
)
def test_base_identifier_in_any_scope_blocks_a_fresh_import(before):
    assert targets(record(before, before + "from pathlib import Path\n")) == (
        FunctionBehavior("sample", reason="unsupported signature"),
    )


def test_unused_parameter_does_not_count_as_name_attribute_or_string():
    before = "def f(Path): pass\n"
    assert targets(record(before, before + "from pathlib import Path\n")) == ()


@pytest.mark.parametrize(
    "before,added",
    [
        ("value = obj.Path\n", "from pathlib import Path\n"),
        ("value = 'Path'\n", "from pathlib import Path\n"),
        ("value = 'prefix_Path_suffix'\n", "from pathlib import Path\n"),
        ("value = f'prefix Path {counter}'\n", "from pathlib import Path\n"),
        ("value = 'Pat' 'h'\n", "from pathlib import Path\n"),
        ("def f(): return 'uuid4'\n", "from uuid import uuid4\n"),
        ("value = f'{counter:uuid4}'\n", "from uuid import uuid4\n"),
        ("value = 'prefixPsuffix'\n", "from pathlib import Path as P\n"),
    ],
)
def test_base_attribute_or_string_blocks_added_bound_name(before, added):
    assert targets(record(before, added + before)) == (
        FunctionBehavior("sample", reason="unsupported signature"),
    )


@pytest.mark.parametrize("before", ["value = obj.Path\n", "value = 'Path'\n"])
def test_attribute_and_string_checks_use_bound_alias_rather_than_imported_name(before):
    assert targets(record(before, "from pathlib import Path as NewPath\n" + before)) == ()


@pytest.mark.parametrize(
    "added",
    [
        "from os import open\n",
        "import os as open\n",
        "from os import path as len\n",
        "from .helpers import helper\n",
        "from . import helper\n",
        "from .pathlib import Path\n",
        "import shop\n",
        "from shop.models import Product\n",
        "import pytest\n",
        "from pytest import mark\n",
        "import os, pytest\n",
        "import this\n",
        "import antigravity\n",
        "from this import d\n",
        "from antigravity import geohash\n",
        "from __future__ import annotations\n",
        "import os.path\n",
        "import os.path as path\n",
        "import os.__oversight_missing_stdlib_child__\n",
        "from os.path import basename\n",
        "from pathlib import NoSuchName\n",
        "from pathlib import Path, NoSuchName\n",
        "import xml\n",
    ],
)
def test_disallowed_added_import_keeps_the_module_entry(added):
    assert targets(record("LIMIT = 1\n", added + "LIMIT = 1\n")) == (
        FunctionBehavior("sample", reason="unsupported signature"),
    )


def test_fresh_import_checks_the_bound_alias_against_base_identifiers():
    before = "def f(): return P('data')\n"
    assert targets(record(before, "from pathlib import Path as P\n" + before)) == (
        FunctionBehavior("sample", reason="unsupported signature"),
    )


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
