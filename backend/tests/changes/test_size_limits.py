"""Proposal collection and accepted boundary saves share workspace file admission."""

import pytest
from changes_helpers import PATH, Harness

from oversight.limits import MAX_FILE_BYTES

pytestmark = pytest.mark.anyio


async def test_exact_limit_saves_on_main_and_review(h: Harness) -> None:
    original = await h.manager.read_file(None, PATH)
    content = original.content + "#" * (MAX_FILE_BYTES - len(original.content.encode()))
    saved = await h.manager.save_main(PATH, content, original.hash)
    assert (await h.manager.read_file(None, PATH)).hash == saved.hash
    tree = await h.run_agent(1)
    before = await tree.read_text(PATH)
    modified = content[:-1] + "!"
    saved_hash = await h.manager.save_for_review("agent-1", PATH, modified, before.hash)
    assert (await h.manager.read_file("agent-1", PATH)).hash == saved_hash


async def test_proposal_collection_skips_oversized_files(h: Harness) -> None:
    from oversight.changes._proposal import read_files

    main = h.workspace.main
    (main.root / "huge.py").write_bytes(b"#" * (MAX_FILE_BYTES + 1))
    files = await read_files(h.manager._ctx, main)
    assert "huge.py" not in files
    assert files[PATH].startswith("def value")
