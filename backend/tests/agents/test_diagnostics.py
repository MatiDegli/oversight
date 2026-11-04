"""Static diagnostics are advisory, isolated and limited to new problems."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from oversight.agents import _diagnostics
from oversight.agents.tools import Toolbox

pytestmark = pytest.mark.anyio


async def edit(tools, path, before, after):
    await tools.call("read_file", json.dumps({"path": path}))
    return await tools.call(
        "edit_file", json.dumps({"path": path, "old_text": before, "new_text": after})
    )


async def test_only_new_undefined_names_are_reported_despite_line_moves(small_tree):
    _, tree = small_tree
    before = "def value():\n    return existing\n"
    after = "\n\ndef value():\n    return existing + introduced\n"
    await tree.write_text("shop/diagnostics.py", before, expected_hash=None)
    # Worktree ruff configuration must never suppress the diagnostic.
    await tree.write_text("ruff.toml", 'lint.ignore = ["F821"]\n', expected_hash=None)
    tools = Toolbox(*small_tree, test_timeout_s=60, harness_pack=True)
    outcome = await edit(tools, "shop/diagnostics.py", before, after)
    assert outcome.ok and "F821 Undefined name `introduced`" in outcome.content
    assert "existing" not in outcome.content
    assert "before running the tests" in outcome.content
    assert (await tree.read_text("shop/diagnostics.py")).content == after


@pytest.mark.parametrize("path,pack", [("shop/diagnostics.py", False), ("notes.txt", True)])
async def test_off_arm_and_non_python_edits_do_not_run_ruff(small_tree, monkeypatch, path, pack):
    _, tree = small_tree
    await tree.write_text(path, "before\n", expected_hash=None)
    ruff = AsyncMock(side_effect=AssertionError("ruff must not run"))
    monkeypatch.setattr(_diagnostics, "_ruff", ruff)
    tools = Toolbox(*small_tree, test_timeout_s=60, harness_pack=pack)
    outcome = await edit(tools, path, "before", "after")
    assert outcome.ok and outcome.content == f"Edited {path}."
    ruff.assert_not_called()


@pytest.mark.parametrize("failure", ["missing", "timeout", "garbage"])
async def test_unavailable_ruff_keeps_the_ordinary_edit_result(
    small_tree, monkeypatch, caplog, failure
):
    process = AsyncMock()
    process.returncode = 0
    process.communicate.return_value = (b"garbage output", b"")
    spawn = AsyncMock(return_value=process)
    if failure == "missing":
        spawn.side_effect = FileNotFoundError()
    elif failure == "timeout":
        process.communicate.side_effect = TimeoutError()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    tools = Toolbox(*small_tree, test_timeout_s=60, harness_pack=True)
    outcome = await edit(tools, "shop/mod.py", "return 1", "return 2")
    assert outcome.ok and outcome.content == "Edited shop/mod.py."
    assert "return 2" in (await small_tree[1].read_text("shop/mod.py")).content
    assert "Static diagnostics unavailable" in caplog.text


async def test_diagnostics_are_bounded_and_compared_by_code_and_message(monkeypatch):
    monkeypatch.setattr(
        _diagnostics,
        "_ruff",
        AsyncMock(side_effect=[[], [("F821", "x" * 300, n, 1) for n in range(30)]]),
    )
    result = await _diagnostics.new_diagnostics("file.py", "before", "after", 200)
    assert len(result.splitlines()) == 22
    assert all(len(row) <= 200 for row in result.splitlines()[2:])


async def test_ruff_timeout_kills_and_reaps_the_child(monkeypatch):
    process = AsyncMock()
    process.returncode = None
    process.kill = lambda: setattr(process, "returncode", -9)

    async def slow(_):
        await asyncio.sleep(60)
        return b"", b""

    process.communicate.side_effect = slow
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    monkeypatch.setattr(_diagnostics, "TIMEOUT_S", 0.01)
    assert await _diagnostics.new_diagnostics("file.py", "before", "after", 200) == ""
    assert process.returncode == -9
    process.wait.assert_awaited_once()
