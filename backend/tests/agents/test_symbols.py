"""Real isolated symbol navigation, with fake-model wiring and hostile path fixtures."""

import json
import os
import sys
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from agent_helpers import Recorder, make_run, script

from oversight.agents import _symbols
from oversight.agents._symbols import SymbolMatch, SymbolResult
from oversight.agents.tools import (
    MAX_SEARCH_LINE,
    MAX_SEARCH_MATCHES,
    Toolbox,
    tool_names,
    tool_schemas,
)
from oversight.workspace import run_process

pytestmark = pytest.mark.anyio

ITEM = "class Item:\n    def total(self):\n        return 1\n\nitem = Item()\na = item.total()\n"


async def call(tools, name="Item.total", path=None):
    arguments = {"name": name}
    if path is not None:
        arguments["path"] = path
    return await tools.call("find_symbol", json.dumps(arguments))


async def test_python_definitions_references_focus_and_scoping(small_tree):
    _, tree = small_tree
    await tree.write_text("shop/items.py", ITEM + "b = item.total()\n", expected_hash=None)
    await tree.write_text(
        "shop/other.py", ITEM + "b = item.total()\nc = item.total()\n", expected_hash=None
    )
    tools = Toolbox(*small_tree, test_timeout_s=60, harness_pack=True)
    await tools.refresh_graph()
    result = await call(tools)
    assert result.ok, result.content
    assert result.summary == "Found Item.total: 2 definitions, 5 references"
    assert "shop/items.py:2: method: def total(self):" in result.content
    assert "shop/items.py:6: reference: a = item.total()" in result.content
    assert result.focus == "read" and result.ranges[0].start_line == 2
    assert "shop.items:Item.total" in result.node_ids
    scoped = await call(tools, path="shop/items.py")
    assert scoped.summary == "Found Item.total: 1 definitions, 2 references"
    assert "other.py" not in scoped.content
    folder = await call(tools, path="shop")
    assert folder.summary == result.summary
    qualified = await call(tools, "shop.items.Item.total")
    assert qualified.ok, qualified.content
    assert qualified.summary == "Found shop.items.Item.total: 1 definitions, 2 references"
    assert "other.py" not in qualified.content


async def test_standard_library_definition_is_module_only(small_tree):
    _, tree = small_tree
    await tree.write_text(
        "shop/library.py",
        'from pathlib import Path\nx = Path("x")\nimport pathlib\ny = pathlib.Path("y")\n',
        expected_hash=None,
    )
    tools = Toolbox(*small_tree, test_timeout_s=60, harness_pack=True)
    result = await call(tools, "Path")
    assert result.ok, result.content
    assert "External definition: pathlib" in result.content
    assert "shop/library.py:2: reference:" in result.content
    assert "pathlib.py" not in result.content and "class Path" not in result.content
    assert str(Path(sys.base_prefix)) not in result.content
    dotted = await call(tools, "pathlib.Path")
    assert dotted.ok, dotted.content
    assert "External definition: pathlib" in dotted.content
    assert "shop/library.py:4: reference:" in dotted.content
    assert "pathlib.py" not in dotted.content


async def test_links_secrets_and_worktree_shadow_modules_are_not_read(small_tree, tmp_path):
    _, tree = small_tree
    outside = tmp_path / "outside.py"
    outside.write_text("def hidden():\n    return 'outside content'\n")
    (tree.root / "linked.py").symlink_to(outside)
    (tree.root / "linked_dir").symlink_to(tmp_path, target_is_directory=True)
    # A Python-suffixed identity and a directory secret both match the secret policy.
    for path in ("id_rsa.py", ".env-private/module.py"):
        await tree.write_text(
            path, "def hidden():\n    return 'secret content'\n", expected_hash=None
        )
    marker = tmp_path / "executed"
    await tree.write_text(
        "jedi.py", f"open({str(marker)!r}, 'w').write('executed')\n", expected_hash=None
    )
    tools = Toolbox(*small_tree, test_timeout_s=60, harness_pack=True)
    result = await call(tools, "hidden")
    assert result.ok and result.content == "No matches."
    assert not marker.exists()
    refused = await call(tools, "hidden", "linked.py")
    assert not refused.ok and "symbolic link" in refused.content
    refused = await call(tools, "hidden", ".env-private")
    assert not refused.ok and "Cannot access" in refused.content


async def test_returned_paths_are_revalidated_before_reading(small_tree, tmp_path):
    tree = small_tree[1]
    outside = tmp_path / "outside.py"
    outside.write_text("NEVER RETURN\n")
    (tree.root / "linked.py").symlink_to(outside)
    payload = {
        "matches": [[path, 1, "function"] for path in ("../outside.py", "linked.py", "id_rsa.py")],
        "modules": ["pathlib", "/outside/path.py"],
        "definitions": 3,
        "references": 0,
    }
    result = await _symbols._validated(tree, json.dumps(payload), 50)
    assert result.matches == [] and result.modules == ["pathlib"]


@pytest.mark.parametrize(
    "name,path", [("Item", "shop/notes.txt"), ("bad()", None), ("class", None)]
)
async def test_non_python_and_invalid_names(small_tree, name, path):
    tools = Toolbox(*small_tree, test_timeout_s=60, harness_pack=True)
    result = await call(tools, name, path)
    assert not result.ok
    assert (
        "find_symbol covers Python; use search" if path else "Python identifier"
    ) in result.content


async def test_pack_off_has_exactly_six_tools_and_unknown_message_lists_active_tools(small_tree):
    names = ("list_files", "read_file", "search", "edit_file", "run_tests", "finish")
    assert tool_names(False) == names
    assert (
        tuple(cast(dict[str, Any], tool["function"])["name"] for tool in tool_schemas(False))
        == names
    )
    assert tool_names(True) == (*names, "find_symbol")
    tools = Toolbox(*small_tree, test_timeout_s=60, harness_pack=False)
    result = await call(tools)
    assert not result.ok and result.error_kind == "unknown_tool"
    assert result.content.endswith(", ".join(names))
    enabled = Toolbox(*small_tree, test_timeout_s=60, harness_pack=True)
    unknown = await enabled.call("shell", "{}")
    assert unknown.content.endswith(", ".join((*names, "find_symbol")))
    failed = await call(enabled, path="shop/notes.txt")
    assert failed.summary == "Could not find symbol notes.txt"


async def test_pack_on_advertises_symbol_tool_to_scripted_model(small_tree):
    model = script([("find_symbol", {"name": "value"})], [("finish", {"summary": "Found it."})])
    end = await make_run(small_tree, model, Recorder(), harness_pack=True).run()
    assert end.state == "ready_for_review"
    assert any(
        cast(dict[str, Any], tool["function"])["name"] == "find_symbol"
        for tool in model.tool_requests[0]
    )


async def test_symbol_rows_and_focus_are_bounded(small_tree, monkeypatch):
    matches = [SymbolMatch("shop/mod.py", 1, "function", "x" * 1000)] * MAX_SEARCH_MATCHES
    monkeypatch.setattr(
        "oversight.agents.tools.find_symbol",
        AsyncMock(return_value=SymbolResult(matches, [], 100, 0)),
    )
    tools = Toolbox(*small_tree, test_timeout_s=60, harness_pack=True)
    result = await call(tools)
    assert len(result.content.splitlines()) == MAX_SEARCH_MATCHES + 1
    assert all(len(row) <= MAX_SEARCH_LINE for row in result.content.splitlines()[:-1])
    assert len(result.ranges) <= 5


async def test_symbol_subprocess_timeout_kills_the_process(small_tree, monkeypatch, tmp_path):
    pid_file = tmp_path / "child.pid"

    async def sleeping(argv, **kwargs):
        assert argv[:4] == [sys.executable, "-I", "-m", "oversight.agents._symbol_worker"]
        assert argv[4] == str(small_tree[1].root)
        assert not kwargs["cwd"].is_relative_to(small_tree[1].root)
        code = (
            "import os,time; from pathlib import Path; "
            f"Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(60)"
        )
        return await run_process([sys.executable, "-I", "-c", code], **kwargs)

    monkeypatch.setattr(_symbols, "run_process", sleeping)
    monkeypatch.setattr(_symbols, "TIMEOUT_S", 0.5)
    tools = Toolbox(*small_tree, test_timeout_s=60, harness_pack=True)
    result = await call(tools)
    assert not result.ok and "time limit" in result.content
    pid = int(pid_file.read_text())
    if sys.platform != "win32":
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
