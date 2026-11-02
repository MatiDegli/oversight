"""Real tool calls on injected NT own-source worktrees; hashes always name raw bytes."""

import json

import pytest

from oversight.agents.tools import Toolbox
from oversight.workspace import Tree, Workspace, content_hash

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    ("raw", "old", "new", "expected"),
    [
        ("é\r\na\r\nb\r\nlast", "a\nb\n", "c\nd\n", "é\r\nc\r\nd\r\nlast"),
        ("lf\ncr\r\na\r\nb\nend\r\n", "a\nb", "c\nd", "lf\ncr\r\nc\r\nd\nend\r\n"),
        ("a\r\nlast", "last", "new\nend", "a\r\nnew\r\nend"),
        ("élast", "last", "new\nend", "énew\nend"),
        ("a\r\nb", "a\n", "x\n", "x\r\nb"),
        ("a\nb\r\n", "a", "x\ny", "x\ny\nb\r\n"),
    ],
)
async def test_local_style_and_repeated_raw_hashes(
    small_tree: tuple[Workspace, Tree], raw: str, old: str, new: str, expected: str
) -> None:
    workspace, tree = small_tree
    workspace.nt_source_bytes = True  # File-only injection; no runtime Git before policy install.
    path = "note.txt"
    await tree.write_text(path, raw, expected_hash=None)
    tools = Toolbox(workspace, tree, test_timeout_s=10)
    assert (await tools.call("read_file", json.dumps({"path": path}))).ok
    for before, after in [(old, new), (new, "done")]:
        outcome = await tools.call(
            "edit_file", json.dumps({"path": path, "old_text": before, "new_text": after})
        )
        assert outcome.ok, outcome.content
        data = (tree.root / path).read_bytes()
        assert (await tree.read_text(path)).hash == content_hash(data)
        if before == old:
            assert data == expected.encode()


@pytest.mark.parametrize(
    ("raw", "old", "reason"),
    [
        ("banana\r\n", "ana", "more than once"),
        ("a\r\na\n", "a\n", "more than once"),
        ("a\r\nb", "missing", "does not appear"),
    ],
)
async def test_missing_duplicate_and_stale(
    small_tree: tuple[Workspace, Tree], raw: str, old: str, reason: str
) -> None:
    workspace, tree = small_tree
    workspace.nt_source_bytes = True
    await tree.write_text("note.txt", raw, expected_hash=None)
    tools = Toolbox(workspace, tree, test_timeout_s=10)
    request = json.dumps({"path": "note.txt", "old_text": old, "new_text": "x"})
    assert "before editing" in (await tools.call("edit_file", request)).content
    await tools.call("read_file", '{"path":"note.txt"}')
    assert reason in (await tools.call("edit_file", request)).content
    seen = await tree.read_text("note.txt")
    await tree.write_text("note.txt", raw + "user\r\n", expected_hash=seen.hash)
    assert "changed since" in (await tools.call("edit_file", request)).content


async def test_default_policy_keeps_exact_matching(small_tree: tuple[Workspace, Tree]) -> None:
    workspace, tree = small_tree
    assert not tree.nt_source_bytes
    await tree.write_text("note.txt", "a\r\nb\r\n", expected_hash=None)
    tools = Toolbox(workspace, tree, test_timeout_s=10)
    await tools.call("read_file", '{"path":"note.txt"}')
    outcome = await tools.call("edit_file", '{"path":"note.txt","old_text":"a\\nb","new_text":"x"}')
    assert not outcome.ok and "does not appear" in outcome.content


async def test_raw_cas_refuses_write_race(
    small_tree: tuple[Workspace, Tree], monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, tree = small_tree
    workspace.nt_source_bytes = True
    await tree.write_text("note.txt", "a\r\nb\r\n", expected_hash=None)
    tools = Toolbox(workspace, tree, test_timeout_s=10)
    await tools.call("read_file", '{"path":"note.txt"}')
    original = Tree.write_text

    async def raced(self: Tree, path: str, content: str, *, expected_hash: str | None) -> str:
        (self.root / path).write_bytes(b"user\r\n")
        return await original(self, path, content, expected_hash=expected_hash)

    monkeypatch.setattr(Tree, "write_text", raced)
    outcome = await tools.call("edit_file", '{"path":"note.txt","old_text":"a\\nb","new_text":"x"}')
    assert not outcome.ok and "changed since" in outcome.content
    assert (tree.root / "note.txt").read_bytes() == b"user\r\n"
