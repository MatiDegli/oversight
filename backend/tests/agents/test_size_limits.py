"""Large files stay accessible by range; huge lines never enter memory in full."""

import json

import pytest

from oversight.agents.tools import MAX_READ_CHARS, Toolbox
from oversight.limits import MAX_FILE_BYTES

pytestmark = pytest.mark.anyio


async def test_large_file_is_readable_by_range_and_huge_line_is_cut(small_tree) -> None:
    workspace, tree = small_tree
    target = tree.root / "big.txt"
    huge = "x" * (MAX_FILE_BYTES + 1)
    target.write_text("first\n" + huge + "\nlast\n")
    tools = Toolbox(workspace, tree, test_timeout_s=60)
    whole = await tools.call("read_file", json.dumps({"path": "big.txt"}))
    assert not whole.ok and whole.content.startswith("Error:")
    assert "start_line" in whole.content
    cut = await tools.call(
        "read_file",
        json.dumps(
            {
                "path": "big.txt",
                "start_line": 2,
                "end_line": 2,
            }
        ),
    )
    assert cut.ok and cut.content.startswith("    2| xxx")
    assert len(cut.content) < MAX_READ_CHARS + 100
    assert f"line 2 cut, {len(huge)} characters in all" in cut.content
    last = await tools.call("read_file", json.dumps({"path": "big.txt", "start_line": 3}))
    assert last.ok and last.content.startswith("    3| last")
    target.write_text("short\n" * 60_000)
    ranged = await tools.call(
        "read_file",
        json.dumps(
            {
                "path": "big.txt",
                "start_line": 59_999,
                "end_line": 60_000,
            }
        ),
    )
    assert ranged.ok and "59999| short\n60000| short" in ranged.content
