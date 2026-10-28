"""The six tools on a real worktree: the edit_file rules, read caps and ranges, search focus, and
paths that try to leave the worktree."""

import asyncio
import json
import os
import threading
from pathlib import Path
from typing import Literal

import pytest
from agent_helpers import write_tree

from oversight.agents.tools import MAX_READ_CHARS, MAX_READ_LINES, Toolbox, ToolOutcome
from oversight.graph import WorkspaceGraph
from oversight.workspace import Tree, Workspace, WorkspaceError

pytestmark = pytest.mark.anyio

Opened = tuple[Workspace, Tree]


async def toolbox(opened: Opened) -> Toolbox:
    workspace, tree = opened
    tools = Toolbox(workspace, tree, test_timeout_s=60)
    await tools.refresh_graph()
    return tools


async def call(tools: Toolbox, name: str, **arguments: object) -> ToolOutcome:
    return await tools.call(name, json.dumps(arguments))


def disk(opened: Opened, path: str) -> str:
    return (opened[1].root / path).read_text()


EDIT = {"path": "shop/mod.py", "old_text": "return 1", "new_text": "return 2"}


# --- edit_file ---------------------------------------------------------------------------------


async def test_edit_needs_a_read_first(small_tree: Opened) -> None:
    tools = await toolbox(small_tree)
    outcome = await call(tools, "edit_file", **EDIT)
    assert not outcome.ok and outcome.content == "Error: read shop/mod.py before editing it"
    assert "return 1" in disk(small_tree, "shop/mod.py")


async def test_edit_old_text_must_appear_exactly_once(small_tree: Opened) -> None:
    tools = await toolbox(small_tree)
    await call(tools, "read_file", path="shop/mod.py")
    missing = await call(tools, "edit_file", path="shop/mod.py", old_text="nope", new_text="x")
    assert not missing.ok and "does not appear" in missing.content
    many = await call(tools, "edit_file", path="shop/mod.py", old_text="value()", new_text="v()")
    assert not many.ok and "appears more than once" in many.content
    empty = await call(tools, "edit_file", path="shop/mod.py", old_text="", new_text="x")
    assert not empty.ok and "must not be empty" in empty.content
    assert disk(small_tree, "shop/mod.py").count("value()") == 3
    once = await call(tools, "edit_file", **EDIT)
    assert once.ok and once.edited_path == "shop/mod.py"
    assert "return 2" in disk(small_tree, "shop/mod.py")


async def test_edit_counts_overlapping_matches(small_tree: Opened) -> None:
    _, tree = small_tree
    await tree.write_text("shop/fruit.py", "FRUIT = 'banana'\n", expected_hash=None)
    tools = await toolbox(small_tree)
    await call(tools, "read_file", path="shop/fruit.py")
    # "ana" is in "banana" twice, overlapping: str.count sees it once.
    twice = await call(tools, "edit_file", path="shop/fruit.py", old_text="ana", new_text="ANA")
    assert not twice.ok and "appears more than once" in twice.content
    assert disk(small_tree, "shop/fruit.py") == "FRUIT = 'banana'\n"


async def test_edit_fails_if_the_file_changed_since_the_read(small_tree: Opened) -> None:
    _, tree = small_tree
    tools = await toolbox(small_tree)
    await call(tools, "read_file", path="shop/mod.py")
    seen = await tree.read_text("shop/mod.py")
    # The user edits the file in review after the agent read it.
    await tree.write_text("shop/mod.py", seen.content + "# user\n", expected_hash=seen.hash)
    stale = await call(tools, "edit_file", **EDIT)
    assert not stale.ok and "changed since you last read it" in stale.content
    assert "return 1" in disk(small_tree, "shop/mod.py")
    await call(tools, "read_file", path="shop/mod.py")
    fresh = await call(tools, "edit_file", **EDIT)
    assert fresh.ok and disk(small_tree, "shop/mod.py").endswith("# user\n")


async def test_own_edits_need_no_new_read(small_tree: Opened) -> None:
    tools = await toolbox(small_tree)
    await call(tools, "read_file", path="shop/mod.py")
    assert (await call(tools, "edit_file", **EDIT)).ok
    second = await call(
        tools, "edit_file", path="shop/mod.py", old_text="return 2", new_text="return 3"
    )
    assert second.ok
    assert second.node_ids == ["shop.mod:value"] and second.focus == "edit"


# --- read_file and search ----------------------------------------------------------------------


async def test_read_numbers_lines_caps_and_ranges(small_tree: Opened) -> None:
    _, tree = small_tree
    body = "".join(f"x{n} = {n}\n" for n in range(1, 1001))
    await tree.write_text("shop/big.py", body, expected_hash=None)
    tools = await toolbox(small_tree)
    capped = await call(tools, "read_file", path="shop/big.py")
    rows = capped.content.splitlines()
    assert rows[0] == "    1| x1 = 1" and len(rows) == MAX_READ_LINES + 1
    assert rows[-1] == f"[lines 1 to {MAX_READ_LINES} of 1000; read more with start_line]"
    ranged = await call(tools, "read_file", path="shop/big.py", start_line=500, end_line=502)
    assert ranged.content.splitlines()[:3] == [
        "  500| x500 = 500",
        "  501| x501 = 501",
        "  502| x502 = 502",
    ]
    assert ranged.ranges[0].start_line == 500 and ranged.ranges[0].end_line == 502
    whole = await call(tools, "read_file", path="shop/mod.py")
    assert whole.ranges[0].start_line is None and whole.summary == "Read mod.py"
    assert whole.node_ids == ["shop.mod:value", "shop.mod", "shop.mod:twice"]
    past = await call(tools, "read_file", path="shop/mod.py", start_line=99)
    assert not past.ok and past.content == "Error: shop/mod.py has 6 lines"


async def test_a_line_over_the_cap_is_shown_cut_and_never_skipped(small_tree: Opened) -> None:
    _, tree = small_tree
    wide = "X = '" + "a" * MAX_READ_CHARS + "'"
    await tree.write_text("shop/wide.py", f"A = 1\n{wide}\nB = 2\n", expected_hash=None)
    tools = await toolbox(small_tree)
    whole = await call(tools, "read_file", path="shop/wide.py")
    assert whole.content.splitlines() == [
        "    1| A = 1",
        "[lines 1 to 1 of 3; read more with start_line]",
    ]
    # Line 2 alone is over the cap: every way to reach it shows its start, never range 2 to 1.
    for arguments in ({"start_line": 2}, {"start_line": 2, "end_line": 2}):
        cut = await call(tools, "read_file", path="shop/wide.py", **arguments)
        assert cut.ok and (cut.ranges[0].start_line, cut.ranges[0].end_line) == (2, 2)
        row, footer = cut.content.splitlines()
        assert row.startswith("    2| X = 'aaa") and len(row) < MAX_READ_CHARS
        assert row.endswith(f" [line 2 cut, {len(wide)} characters in all]")
        assert footer == "[lines 2 to 2 of 3; read more with start_line]"
    last = await call(tools, "read_file", path="shop/wide.py", start_line=3)
    assert last.content.splitlines()[0] == "    3| B = 2"


async def test_search_focuses_at_most_five_nodes(small_tree: Opened) -> None:
    _, tree = small_tree
    body = "".join(f"def f{n}() -> str:\n    return 'marker'\n\n\n" for n in range(8))
    await tree.write_text("shop/many.py", body, expected_hash=None)
    tools = await toolbox(small_tree)
    outcome = await call(tools, "search", query="marker")
    assert len(outcome.content.splitlines()) == 8
    assert outcome.node_ids == [f"shop.many:f{n}" for n in range(5)]
    assert [r.start_line for r in outcome.ranges] == [2, 6, 10, 14, 18]
    under = await call(tools, "search", query="value", path="shop/notes.txt")
    assert under.content == "shop/notes.txt:1: value\nshop/notes.txt:2: value"
    assert under.node_ids == [] and len(under.ranges) == 1


async def test_unknown_tools_and_bad_arguments_are_errors(small_tree: Opened) -> None:
    tools = await toolbox(small_tree)
    unknown = await tools.call("shell", "{}")
    assert not unknown.ok and "unknown tool 'shell'" in unknown.content
    assert unknown.summary == "Called an unknown tool, 'shell'"
    broken = await tools.call("read_file", '{"path": ')
    assert broken.content == "Error: arguments are not valid JSON"
    typed = await tools.call("read_file", '{"path": 3}')
    assert typed.content == (
        "Error: argument 'path' must be string; expected read_file(path, start_line, end_line)"
    )


# --- paths -------------------------------------------------------------------------------------

ESCAPES = ["../outside.py", "/etc/passwd", ".git/config", "shop/../../outside.py", "link.py"]


@pytest.mark.parametrize("path", ESCAPES)
async def test_paths_outside_the_worktree_are_refused(
    small_tree: Opened, tmp_path: Path, path: str
) -> None:
    _, tree = small_tree
    outside = tmp_path / "outside-secret.py"
    await asyncio.to_thread(outside.write_text, "SECRET = 'outside-only'\n")
    await asyncio.to_thread(os.symlink, outside, tree.root / "link.py")
    tools = await toolbox(small_tree)
    for name, arguments in (
        ("read_file", {"path": path}),
        ("search", {"query": "SECRET", "path": path}),
        ("edit_file", {"path": path, "old_text": "SECRET", "new_text": "X"}),
    ):
        outcome = await call(tools, name, **arguments)
        assert not outcome.ok, (name, path)
        assert "outside-only" not in outcome.content and str(tmp_path) not in outcome.content
    whole = await call(tools, "search", query="outside-only")
    assert whole.content == "No matches."
    assert outside.read_text() == "SECRET = 'outside-only'\n"


# --- the graph scan ----------------------------------------------------------------------------


async def test_the_scan_refuses_a_root_swapped_for_a_link(
    small_tree: Opened, tmp_path: Path
) -> None:
    _, tree = small_tree
    tools = await toolbox(small_tree)
    outside = write_tree(
        tmp_path / "outside", {"shop/mod.py": "def secret() -> int:\n    return 7\n"}
    )
    await asyncio.to_thread(os.rename, tree.root, tmp_path / "moved-worktree")
    await asyncio.to_thread(os.symlink, outside, tree.root)
    with pytest.raises(WorkspaceError, match="invalid workspace root"):
        await tools.refresh_graph()


async def test_a_cancelled_scan_keeps_the_lock_until_its_thread_ends(
    small_tree: Opened, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, tree = small_tree
    tools = Toolbox(workspace, tree, test_timeout_s=60)
    entered, release, ended = threading.Event(), threading.Event(), threading.Event()
    real_scan = WorkspaceGraph.scan

    def held_scan(graph: WorkspaceGraph, commit: str) -> object:
        entered.set()
        release.wait(60)
        try:
            return real_scan(graph, commit)
        finally:
            ended.set()

    monkeypatch.setattr(WorkspaceGraph, "scan", held_scan)
    seen_ended: list[bool] = []

    async def discard() -> None:
        # What discard_worktree takes after Stop.
        async with workspace.locks(main=True, agent_id=tree.agent_id):
            seen_ended.append(ended.is_set())

    scan = asyncio.create_task(tools.refresh_graph())
    try:
        assert await asyncio.to_thread(entered.wait, 60)
        assert not workspace.operations.idle  # the tree's lock is held while the scan runs
        scan.cancel()  # Stop
        other = asyncio.create_task(discard())
        for _ in range(10):  # loop turns, not time: the cancellation and the discard get to run
            await asyncio.sleep(0)
        assert not scan.done() and not other.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await scan
    await other
    assert seen_ended == [True]


@pytest.mark.parametrize("outcome", ["passed", "failed", "timeout", "error"])
async def test_suite_summary_precedes_large_output(
    small_tree: Opened,
    monkeypatch: pytest.MonkeyPatch,
    outcome: Literal["passed", "failed", "timeout", "error"],
) -> None:
    from oversight.workspace import SuiteRun
    from oversight_contracts.events import SuiteResult

    async def run_tests(self: Workspace, tree: Tree, *, timeout_s: float) -> SuiteRun:
        return SuiteRun(
            result=SuiteResult(
                outcome=outcome,
                passed=3,
                failed=2,
                skipped=1,
                duration_ms=5,
                failed_tests=["tests/test_a.py::test_first", "tests/test_b.py::test_second"],
            ),
            output="early traceback\n" + "x" * 5000 + "end of traceback",
        )

    monkeypatch.setattr(Workspace, "run_tests", run_tests)
    result = await call(await toolbox(small_tree), "run_tests")
    assert result.ok  # running the tool succeeded even when the suite failed
    assert result.suite_run is not None and result.suite_run.result == result.tests
    assert result.suite_run.output.startswith("early traceback\n")
    assert result.content.startswith(f"Suite outcome: {outcome}; 3 passed, 2 failed, 1 skipped")
    assert result.content.index("tests/test_a.py::test_first") < result.content.index(
        "Output truncated"
    )
    assert "tests/test_b.py::test_second" in result.content
    assert "early traceback" not in result.content and result.content.endswith("end of traceback")


@pytest.mark.parametrize("output_size, truncated", [(3999, False), (4000, False), (4001, True)])
async def test_suite_output_truncation_boundary(
    small_tree: Opened,
    monkeypatch: pytest.MonkeyPatch,
    output_size: int,
    truncated: bool,
) -> None:
    from oversight.workspace import SuiteRun
    from oversight_contracts.events import SuiteResult

    async def run_tests(self: Workspace, tree: Tree, *, timeout_s: float) -> SuiteRun:
        return SuiteRun(
            result=SuiteResult(
                outcome="passed", passed=1, failed=0, skipped=0, duration_ms=5, failed_tests=[]
            ),
            output="x" * output_size,
        )

    monkeypatch.setattr(Workspace, "run_tests", run_tests)
    result = await call(await toolbox(small_tree), "run_tests")
    expected = "Suite outcome: passed; 1 passed, 0 failed, 0 skipped\nFailed tests: none reported"
    if truncated:
        expected += "\nOutput truncated: showing the last 4000 characters."
    expected += "\n\n" + "x" * min(output_size, 4000)
    assert result.ok and result.content == expected


async def test_zero_match_error_explains_exact_text_recovery(small_tree: Opened) -> None:
    tools = await toolbox(small_tree)
    await call(tools, "read_file", path="shop/mod.py")
    result = await call(tools, "edit_file", path="shop/mod.py", old_text="absent", new_text="x")
    assert not result.ok
    assert "read the file again and copy the exact text without line numbers" in result.content


@pytest.mark.parametrize(
    "name, args",
    [
        ("read_file", {"path": "shop/mod.py", "start_line": None}),
        ("search", {"query": "value", "path": None}),
        ("finish", {}),
        ("read_file", {"path": "shop/mod.py", "end_line": True}),
    ],
)
async def test_advertised_types_and_required_fields_are_enforced(
    small_tree: Opened, name: str, args: dict[str, object]
) -> None:
    result = await call(await toolbox(small_tree), name, **args)
    assert not result.ok and "expected " + name + "(" in result.content


@pytest.mark.parametrize(
    "declaration",
    [
        {"files": ["shop/mod.py"], "tests_run": True, "tests": ["tests/test_mod.py::test_value"]},
        {"files": [], "tests_run": False, "tests": []},
        {"files": None, "tests_run": None, "tests": None},
    ],
)
async def test_finish_accepts_advertised_declaration_types(
    small_tree: Opened, declaration: dict[str, object]
) -> None:
    result = await call(await toolbox(small_tree), "finish", summary="Done.", **declaration)
    assert result.ok and result.finish_summary == "Done."
    assert result.declaration is not None and result.declaration.model_dump() == declaration


@pytest.mark.parametrize(
    "declaration",
    [
        {"files": "shop/mod.py"},
        {"files": [123]},
        {"tests_run": 1},
        {"tests_run": "true"},
        {"tests": "tests/test_mod.py::test_value"},
        {"tests": [123]},
    ],
)
async def test_finish_rejects_invalid_declaration_types(
    small_tree: Opened, declaration: dict[str, object]
) -> None:
    result = await call(await toolbox(small_tree), "finish", summary="Done.", **declaration)
    assert not result.ok and result.content.startswith("Error:")
    assert result.finish_summary is None and result.declaration is None
