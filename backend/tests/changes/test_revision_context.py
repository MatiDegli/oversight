"""Revision context comes from the agent's current proposal, including its added callers."""

import pytest
from changes_helpers import EDIT, PATH, Harness, bounded
from decision_helpers import observed_target

from oversight_contracts.events import ChangeResolved

pytestmark = pytest.mark.anyio


@bounded
async def test_revision_uses_agent_graph_and_added_and_modified_functions(h: Harness) -> None:
    await h.open(1)
    assert await h.edit(1, *EDIT)
    assert await h.edit(
        1,
        PATH,
        "return value() + value()",
        "return value() + value()\n\n\ndef new_caller():\n    return value()",
    )
    assert await h.finish(1)
    comment = "  Check this.\nPreserve whitespace.  "
    revision = await h.manager.request_changes(
        "change-1", comment, **observed_target(h.manager, "change-1")
    )
    assert revision is not None and revision.note is None
    assert revision.comment.startswith(comment + "\n\nFunctions one call away")
    assert "value (shop/mod.py)\nCallers: new_caller (shop/mod.py; exact calls)" in revision.comment
    assert "new_caller (shop/mod.py)\nCallers: none\nSiblings: none" in revision.comment
    assert "new_caller" not in (await h.workspace.main.read_text(PATH)).content
    assert h.payloads(ChangeResolved)[-1].comment == comment
