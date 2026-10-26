"""The live diff: one `change.updated` per edit, with both versions of the file, sent before the
sink call returns, and a failed edit republished at the proposal."""

import asyncio

import pytest
from changes_helpers import ADD_THRICE, TEMPLATE, THRICE, VALUE, Harness, bounded

from oversight_contracts.events import ChangeUpdated, ErrorReport

pytestmark = pytest.mark.anyio


@bounded
async def test_edit_sends_both_versions_and_its_nodes(h: Harness) -> None:
    await h.open(1)
    assert await h.edit(1, "shop/mod.py", "return 1", "return 3")

    # Committed before file_edited returned, so it precedes the edit's focus and step.
    [updated] = h.payloads(ChangeUpdated)
    assert updated.author == "agent"
    assert updated.file.status == "modified"
    assert updated.file.original == TEMPLATE["shop/mod.py"]
    assert updated.file.modified == TEMPLATE["shop/mod.py"].replace("return 1", "return 3")
    assert (updated.file.lines_added, updated.file.lines_removed) == (1, 1)
    assert updated.node_ids == [VALUE]
    assert updated.graph.nodes_modified == [VALUE]
    assert (updated.files_changed, updated.lines_added, updated.lines_removed) == (1, 1, 1)


@bounded
async def test_added_function_is_in_nodes_added(h: Harness) -> None:
    await h.open(1)
    assert await h.edit(1, *ADD_THRICE)

    [updated] = h.payloads(ChangeUpdated)
    assert [n.id for n in updated.graph.nodes_added] == [THRICE]
    assert updated.graph.nodes_modified == []
    assert updated.node_ids == [THRICE]
    assert (updated.file.lines_added, updated.file.lines_removed) == (4, 0)


@bounded
async def test_new_file_is_added_with_no_original(h: Harness) -> None:
    tree = await h.open(1)
    await tree.write_text(
        "shop/extra.py", "def extra() -> int:\n    return 2\n", expected_hash=None
    )
    assert await h.manager.file_edited("agent-1", "shop/extra.py")

    [updated] = h.payloads(ChangeUpdated)
    assert (updated.file.status, updated.file.original) == ("added", None)
    assert updated.node_ids == ["shop.extra", "shop.extra:extra"]


@bounded
async def test_failed_edit_republished(h: Harness) -> None:
    tree = await h.open(1)
    target = tree.root / "shop" / "mod.py"
    await asyncio.to_thread(target.write_bytes, b"def value() -> int:\n    return '\xff'\n")
    assert await h.manager.file_edited("agent-1", "shop/mod.py") is False
    [error] = h.payloads(ErrorReport)
    assert (error.fatal, error.agent_id, error.change_id) == (False, "agent-1", "change-1")
    assert h.payloads(ChangeUpdated) == []

    fixed = TEMPLATE["shop/mod.py"].replace("return 1", "return 4")
    await asyncio.to_thread(target.write_text, fixed)  # a valid edit, with no sink call
    assert await h.finish(1)

    [updated] = h.payloads(ChangeUpdated)
    assert (updated.file.original, updated.file.modified) == (TEMPLATE["shop/mod.py"], fixed)
    kinds = h.types()
    assert kinds.index("change.updated") < kinds.index("change.proposed")
