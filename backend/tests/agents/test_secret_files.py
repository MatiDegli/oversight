"""Tracked canaries never reach model tools; people and example files keep their reads."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from agent_helpers import open_tree, write_tree

from oversight.agents import LeadTools
from oversight.agents.tools import Toolbox, ToolOutcome
from oversight.workspace import SECRET_FILE_REFUSAL, Tree, Workspace
from oversight_contracts.commands import FileContent, TreeFile, TreeResponse
from oversight_contracts.graph import GraphSnapshot
from oversight_contracts.plans import PlanZone
from oversight_contracts.queue import QueueSnapshot

pytestmark = pytest.mark.anyio
CANARIES = {
    ".env": "CANARY_ENV",
    "sub/.env.local": "CANARY_LOCAL",
    "server.key": "CANARY_KEY",
    "sub/.ENV/ordinary.py": "CANARY_DIRECTORY",
    ".envs/app.example": "CANARY_DIRECTORY_EXAMPLE",
}
PUBLIC = {".env.example": "example_setting=sample\n", "README.md": "Read this documentation.\n"}


@pytest.fixture
async def secret_tools(tmp_path: Path, started: list[Workspace]) -> tuple[Toolbox, Tree]:
    template = write_tree(
        tmp_path / "template",
        PUBLIC | {path: f"sensitive_setting={canary}\n" for path, canary in CANARIES.items()},
    )
    workspace, tree = await open_tree(started, tmp_path / "runtime", template)
    tools = Toolbox(workspace, tree, test_timeout_s=60)
    await tools.refresh_graph()
    return tools, tree


async def call(tools: Toolbox, name: str, **arguments: object) -> ToolOutcome:
    return await tools.call(name, json.dumps(arguments))


def assert_no_focus(outcome: ToolOutcome) -> None:
    assert outcome.focus is None and outcome.ranges == [] and outcome.node_ids == []
    assert outcome.edited_path is None


@pytest.mark.parametrize("path", CANARIES)
@pytest.mark.parametrize("ranged", [False, True])
async def test_read_and_edit_refuse_before_io(
    secret_tools: tuple[Toolbox, Tree], path: str, ranged: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools, tree = secret_tools
    before = (tree.root / path).read_bytes()
    read, read_range, write = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(tree, "read_text", read)
    monkeypatch.setattr(tree, "read_range", read_range)
    monkeypatch.setattr(tree, "write_text", write)
    arguments = dict(start_line=1, end_line=1) if ranged else {}
    outcome = await call(tools, "read_file", path=path, **arguments)
    expected = "Error: " + SECRET_FILE_REFUSAL + path.rsplit("/", 1)[-1]
    assert not outcome.ok and outcome.content == expected
    assert CANARIES[path] not in outcome.content + outcome.summary
    assert_no_focus(outcome)
    edit = await call(tools, "edit_file", path=path, old_text="sensitive_setting", new_text="x")
    assert not edit.ok and edit.content == expected
    assert "before editing it" not in edit.content
    assert CANARIES[path] not in edit.content + edit.summary
    assert_no_focus(edit)
    assert (tree.root / path).read_bytes() == before
    read.assert_not_awaited()
    read_range.assert_not_awaited()
    write.assert_not_awaited()


@pytest.mark.parametrize("path", CANARIES)
async def test_search_skips_secret_bytes_in_every_scope(
    secret_tools: tuple[Toolbox, Tree], path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools, tree = secret_tools
    read = AsyncMock(wraps=tree.read_text)
    monkeypatch.setattr(tree, "read_text", read)
    for query in (CANARIES[path], "sensitive_setting"):
        for scope in (None, path, path.rpartition("/")[0] or "."):
            # Root scope is expressed by omitting path, as required by path validation.
            arguments = {} if scope in (None, ".") else {"path": scope}
            outcome = await call(tools, "search", query=query, **arguments)
            assert outcome.ok and outcome.content == "No matches."
            assert outcome.summary.endswith("0 matches in 0 files")
            assert_no_focus(outcome)
    assert all(awaited.args[0] not in CANARIES for awaited in read.await_args_list)
    example = await call(tools, "search", query="example_setting")
    assert example.ok and ".env.example:1:" in example.content
    assert example.focus == "read" and len(example.ranges) == 1


async def test_names_are_listed_and_public_files_are_read(
    secret_tools: tuple[Toolbox, Tree],
) -> None:
    tools, tree = secret_tools
    listed = await call(tools, "list_files")
    assert listed.ok and set(CANARIES) <= set(listed.content.splitlines())
    for path, content in PUBLIC.items():
        outcome = await call(tools, "read_file", path=path)
        assert outcome.ok and content.strip() in outcome.content
    # The shared workspace read used by the person's file view remains unrestricted.
    assert CANARIES[".env"] in (await tree.read_text(".env")).content


async def test_refusal_is_bounded_and_does_not_disclose_existence_or_absolute_path(
    secret_tools: tuple[Toolbox, Tree], monkeypatch: pytest.MonkeyPatch
) -> None:
    tools, tree = secret_tools
    read = AsyncMock()
    monkeypatch.setattr(tree, "read_text", read)
    write, read_range = AsyncMock(), AsyncMock()
    monkeypatch.setattr(tree, "write_text", write)
    monkeypatch.setattr(tree, "read_range", read_range)
    basename = ".env" + "x" * 100
    for path in (basename, "/absolute/private/" + basename, "../.env", "sub/../../.env"):
        outcome = await call(tools, "read_file", path=path)
        assert not outcome.ok
        expected = "Error: " + SECRET_FILE_REFUSAL + path.rsplit("/", 1)[-1][:60]
        assert outcome.content == expected
        edit = await call(tools, "edit_file", path=path, old_text="x", new_text="y")
        assert not edit.ok and edit.content == expected
        assert len(outcome.content) < 200 and "/absolute/private" not in outcome.content
    read.assert_not_awaited()
    read_range.assert_not_awaited()
    write.assert_not_awaited()


@pytest.mark.parametrize("path", [*CANARIES, "/absolute/private/.env", "../.env", ".env.missing"])
async def test_lead_refuses_before_read_and_still_reads_public_files(
    path: str, refusal: str | None = None
) -> None:
    read = AsyncMock(
        return_value=FileContent(
            path="README.md", content=PUBLIC["README.md"], hash="h", agent_id=None
        )
    )
    tree = AsyncMock(
        return_value=TreeResponse(
            run_id="run-20261031-170000",
            agent_id=None,
            directories=[],
            files=[TreeFile(path=".env", hash=None)],
            graph_version="main",
            truncated=False,
        )
    )
    tools = LeadTools(
        graph=lambda: GraphSnapshot(
            version="main", commit="base", nodes=[], edges=[], parse_errors=[], positions={}
        ),
        tree=tree,
        read=read,
        queue=lambda: QueueSnapshot(run_id="run-20261031-170000", seq=0, tasks=[]),
        zone=lambda: PlanZone(node_ids=[], paths=[]),
        propose=AsyncMock(),
        slots=2,
    )
    for arguments in (dict(path=path), dict(path=path, start_line=1, end_line=1)):
        outcome = await tools.call("read_file", json.dumps(arguments))
        assert not outcome.ok
        assert outcome.content == (refusal or SECRET_FILE_REFUSAL + path.rsplit("/", 1)[-1][:60])
        assert len(outcome.content) < 200
    read.assert_not_awaited()
    listed = await tools.call("list_files", "{}")
    assert listed.ok and listed.content == ".env"
    public = await tools.call("read_file", json.dumps(dict(path="README.md")))
    assert public.ok and PUBLIC["README.md"].strip() in public.content
    read.assert_awaited_once_with("README.md")


@pytest.mark.parametrize(
    "path",
    [
        ".ENV",
        ".env::$DATA",
        ".env.",
        ".env /x",
        ".ENV/private.py",
        "KEY.PEM/x.py",
        "private.KEY/a.py",
        "ID_RSA/a.py",
        "\u0131d_rsa",
        "GIT~1",
        "plain:stream",
        "plain.",
        "CON/x.py",
        ".GIT",
    ],
)
async def test_nt_worker_and_lead_zero_content_io(secret_tools, monkeypatch, path):
    from types import SimpleNamespace

    from oversight.workspace import secret_paths

    monkeypatch.setattr(secret_paths, "sys", SimpleNamespace(platform="win32"))
    tools, tree = secret_tools
    read, ranged, write = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(tree, "read_text", read)
    monkeypatch.setattr(tree, "read_range", ranged)
    monkeypatch.setattr(tree, "write_text", write)
    assert secret_paths.is_secret_path(path)
    ambiguous = path in {
        ".env::$DATA",
        ".env.",
        ".env /x",
        "GIT~1",
        "plain:stream",
        "plain.",
        "CON/x.py",
        ".GIT",
    }
    refusal = (
        f"invalid path {path!r}: ambiguous Windows segment"
        if ambiguous
        else SECRET_FILE_REFUSAL + path.rsplit("/", 1)[-1]
    )
    for arguments in (dict(path=path), dict(path=path, start_line=1, end_line=1)):
        outcome = await call(tools, "read_file", **arguments)
        assert not outcome.ok and outcome.content == "Error: " + refusal
    outcome = await call(tools, "edit_file", path=path, old_text="x", new_text="y")
    assert not outcome.ok and outcome.content == "Error: " + refusal
    await test_lead_refuses_before_read_and_still_reads_public_files(path, refusal)
    read.assert_not_awaited()
    ranged.assert_not_awaited()
    write.assert_not_awaited()
    assert not secret_paths.is_secret_path(".env.example")
    assert not secret_paths.is_secret_path("id_utils.py")


async def test_nt_search_scopes_and_parents_never_open_secrets(secret_tools, monkeypatch):
    from types import SimpleNamespace

    from oversight.workspace import files, secret_paths

    tools, tree = secret_tools
    for path in ("KEY.PEM/a.py", "ID_RSA/a.py", "\u0131d_ed25519/a.py"):
        target = tree.root / path
        target.parent.mkdir()
        target.write_text("CANARY_NT")
    monkeypatch.setattr(secret_paths, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(files, "sys", SimpleNamespace(platform="win32"))
    read = AsyncMock(wraps=tree.read_text)
    monkeypatch.setattr(tree, "read_text", read)
    for scope in (None, "KEY.PEM", "ID_RSA", "\u0131d_ed25519", ".ENV", "parent:ads", "ID_RSA "):
        arguments = {} if scope is None else {"path": scope}
        outcome = await call(tools, "search", query="CANARY", **arguments)
        assert "CANARY_" not in outcome.content
    assert all(not secret_paths.is_secret_path(c.args[0]) for c in read.await_args_list)
    # The person's direct read still goes through guarded NT I/O and remains unrestricted.
    assert CANARIES[".env"] in (await tree.read_text(".env")).content
