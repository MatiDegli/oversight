"""The six agent tools: list_files, read_file, search, edit_file, run_tests and finish.

Every path goes through the agent's `Tree`, which refuses anything outside its worktree. What a
tool returns to the model is plain text with no extra metadata; the rest of `ToolOutcome` (the
summary, the focus, the test result) feeds events only.
"""

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Literal, cast

from oversight.graph import WorkspaceGraph
from oversight.workspace import FileRead, Tree, Workspace, WorkspaceError
from oversight_contracts.events import FocusRange, SuiteResult

MAX_READ_LINES = 400
MAX_READ_CHARS = 40_000
MAX_SEARCH_MATCHES = 50
MAX_SEARCH_LINE = 200
SEARCH_FOCUS_NODES = 5
MAX_TEST_OUTPUT = 4_000

TOOL_NAMES = ("list_files", "read_file", "search", "edit_file", "run_tests", "finish")


def _function(
    name: str, description: str, properties: dict[str, object], required: list[str]
) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


_PATH = {"type": "string", "description": "Relative to the repository root, such as shop/cart.py."}
TOOL_SCHEMAS: list[dict[str, object]] = [
    _function("list_files", "List every file in the repository.", {}, []),
    _function(
        "read_file",
        f"Read a file with numbered lines, at most {MAX_READ_LINES} lines per call. Read long "
        "files by line range; prefer one read of a whole function to several small ones.",
        {
            "path": _PATH,
            "start_line": {"type": "integer", "description": "First line, from 1."},
            "end_line": {"type": "integer", "description": "Last line, included."},
        },
        ["path"],
    ),
    _function(
        "search",
        "Find lines that contain a literal text, in the whole repository or under one path.",
        {"query": {"type": "string"}, "path": _PATH},
        ["query"],
    ),
    _function(
        "edit_file",
        "Replace old_text with new_text in a file you have read. old_text must appear exactly "
        "once, copied without the line numbers. Fails if the file changed since you last read it.",
        {"path": _PATH, "old_text": {"type": "string"}, "new_text": {"type": "string"}},
        ["path", "old_text", "new_text"],
    ),
    _function("run_tests", "Run the repository's test suite and get the result.", {}, []),
    _function(
        "finish",
        "Finish the task and hand the change to the reviewer, with a short note on what changed.",
        {"summary": {"type": "string"}},
        ["summary"],
    ),
]


class ToolError(Exception):
    """A failed call: its message goes back to the model."""


@dataclass(frozen=True)
class ToolOutcome:
    content: str
    """Exactly what the model receives."""
    ok: bool
    summary: str
    focus: Literal["read", "edit"] | None = None
    ranges: list[FocusRange] = field(default_factory=lambda: list[FocusRange]())
    node_ids: list[str] = field(default_factory=lambda: list[str]())
    edited_path: str | None = None
    tests: SuiteResult | None = None
    finish_summary: str | None = None


def _name(path: str) -> str:
    return PurePosixPath(path).name


def _string(arguments: dict[str, object], key: str, *, required: bool = True) -> str | None:
    value = arguments.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise ToolError(f"argument {key!r} must be a string")
    return value


def _line(arguments: dict[str, object], key: str) -> int | None:
    value = arguments.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ToolError(f"argument {key!r} must be a line number from 1")
    return value


class Toolbox:
    """One agent's tools over its worktree, and the graph of its own copy."""

    def __init__(self, workspace: Workspace, tree: Tree, *, test_timeout_s: float) -> None:
        self._workspace = workspace
        self._tree = tree
        self._test_timeout_s = test_timeout_s
        self._graph = WorkspaceGraph(tree.root)
        self._read_hashes: dict[str, str] = {}
        """The hash of each file as the agent last saw it: read, or written by its own edit."""

    async def refresh_graph(self) -> None:
        commit = self._tree.base_commit or self._tree.branch
        await asyncio.to_thread(self._graph.scan, commit)

    def _nodes_over(self, path: str, start: int, end: int) -> list[str]:
        found: dict[str, None] = {}
        for line in range(start, end + 1):
            node = self._graph.node_at(path, line)
            if node is not None:
                found[node] = None
        return list(found)

    def _where(self, verb: str, path: str, nodes: list[str], lines: tuple[int, int] | None) -> str:
        named = [node.split(":", 1)[1].split("#")[0] for node in nodes if ":" in node]
        where = f"{named[0]} in {_name(path)}" if len(named) == 1 else _name(path)
        if lines is None:
            return f"{verb} {where}"
        return f"{verb} {where}, lines {lines[0]} to {lines[1]}"

    async def _read(self, path: str) -> FileRead:
        try:
            return await self._tree.read_text(path)
        except FileNotFoundError:
            raise ToolError(f"no such file: {path}") from None
        except IsADirectoryError:
            raise ToolError(f"{path} is a directory") from None

    async def call(self, name: str, raw_arguments: str) -> ToolOutcome:
        try:
            parsed = json.loads(raw_arguments or "{}")
            if not isinstance(parsed, dict):
                raise ToolError("arguments must be a JSON object")
            arguments = cast(dict[str, object], parsed)
            match name:
                case "list_files":
                    return await self._list_files()
                case "read_file":
                    return await self._read_file(arguments)
                case "search":
                    return await self._search(arguments)
                case "edit_file":
                    return await self._edit_file(arguments)
                case "run_tests":
                    return await self._run_tests()
                case "finish":
                    return self._finish(arguments)
                case _:
                    raise ToolError(f"unknown tool {name!r}; the tools are {', '.join(TOOL_NAMES)}")
        except json.JSONDecodeError:
            message = "arguments are not valid JSON"
        except (ToolError, WorkspaceError) as error:
            message = self._workspace.redact(str(error))
        except OSError as error:
            message = f"cannot access the file: {error.strerror or type(error).__name__}"
        return ToolOutcome(
            content=f"Error: {message}", ok=False, summary=_failed(name, raw_arguments)
        )

    async def _list_files(self) -> ToolOutcome:
        files = await self._tree.list_files()
        return ToolOutcome(content="\n".join(files), ok=True, summary=f"Listed {len(files)} files")

    async def _read_file(self, arguments: dict[str, object]) -> ToolOutcome:
        path = cast(str, _string(arguments, "path"))
        start, end = _line(arguments, "start_line"), _line(arguments, "end_line")
        file = await self._read(path)
        lines = file.content.splitlines()
        total = len(lines)
        first = start or 1
        if total and first > total:
            raise ToolError(f"{path} has {total} lines")
        last = min(end or total, total, first + MAX_READ_LINES - 1)
        if last < first and total:
            raise ToolError("end_line is before start_line")
        shown: list[str] = []
        size = 0
        for number in range(first, last + 1):
            row = f"{number:>5}| {lines[number - 1]}"
            size += len(row) + 1
            if size > MAX_READ_CHARS:
                last = number - 1
                break
            shown.append(row)
        self._read_hashes[path] = file.hash
        whole = first == 1 and last >= total
        if not whole:
            shown.append(f"[lines {first} to {last} of {total}; read more with start_line]")
        nodes = self._nodes_over(path, first, max(last, first)) if total else []
        span = None if whole else (first, last)
        return ToolOutcome(
            content="\n".join(shown),
            ok=True,
            summary=self._where("Read", path, nodes, span),
            focus="read",
            ranges=[
                FocusRange(
                    path=path,
                    start_line=None if span is None else span[0],
                    end_line=None if span is None else span[1],
                )
            ],
            node_ids=nodes,
        )

    async def _search(self, arguments: dict[str, object]) -> ToolOutcome:
        query = cast(str, _string(arguments, "query"))
        under = _string(arguments, "path", required=False)
        if not query.strip() or "\n" in query:
            raise ToolError("query must be one non-empty line")
        if under is not None:
            await asyncio.to_thread(self._tree.resolve, under)
        files = [
            f
            for f in await self._tree.list_files()
            if under is None or f == under or f.startswith(under.rstrip("/") + "/")
        ]
        matches: list[tuple[str, int, str]] = []
        for path in files:
            try:
                content = (await self._tree.read_text(path)).content
            except (WorkspaceError, OSError):
                continue  # binary or unreadable: not searchable text
            for number, text in enumerate(content.splitlines(), start=1):
                if query in text:
                    matches.append((path, number, text))
        ranges: list[FocusRange] = []
        nodes: list[str] = []
        seen: set[str] = set()
        for path, number, _ in matches:
            if len(seen) == SEARCH_FOCUS_NODES:
                break
            node = self._graph.node_at(path, number)
            key = node if node is not None else f"file:{path}"
            if key in seen:
                continue
            seen.add(key)
            ranges.append(FocusRange(path=path, start_line=number, end_line=number))
            if node is not None:
                nodes.append(node)
        rows = [
            f"{p}:{n}: {t.strip()[:MAX_SEARCH_LINE]}" for p, n, t in matches[:MAX_SEARCH_MATCHES]
        ]
        if len(matches) > MAX_SEARCH_MATCHES:
            rows.append(f"[{len(matches) - MAX_SEARCH_MATCHES} more matches; narrow the search]")
        in_files = len({p for p, _, _ in matches})
        return ToolOutcome(
            content="\n".join(rows) if rows else "No matches.",
            ok=True,
            summary=f"Searched for {query[:40]!r}: {len(matches)} matches in {in_files} files",
            focus="read" if ranges else None,
            ranges=ranges,
            node_ids=nodes,
        )

    async def _edit_file(self, arguments: dict[str, object]) -> ToolOutcome:
        path = cast(str, _string(arguments, "path"))
        old = cast(str, _string(arguments, "old_text"))
        new = cast(str, _string(arguments, "new_text"))
        if not old:
            raise ToolError("old_text must not be empty")
        if old == new:
            raise ToolError("old_text and new_text are the same")
        seen = self._read_hashes.get(path)
        if seen is None:
            raise ToolError(f"read {path} before editing it")
        current = await self._read(path)
        if current.hash != seen:
            raise ToolError(f"{path} changed since you last read it; read it again")
        count = current.content.count(old)
        if count != 1:
            found = "does not appear" if count == 0 else f"appears {count} times"
            raise ToolError(f"old_text {found} in {path}; it must appear exactly once")
        index = current.content.index(old)
        content = current.content[:index] + new + current.content[index + len(old) :]
        self._read_hashes[path] = await self._tree.write_text(
            path, content, expected_hash=current.hash
        )
        await self.refresh_graph()
        start = current.content.count("\n", 0, index) + 1
        end = start + max(new.count("\n") - (1 if new.endswith("\n") else 0), 0)
        nodes = self._nodes_over(path, start, end)
        return ToolOutcome(
            content=f"Edited {path}.",
            ok=True,
            summary=self._where("Edited", path, nodes, (start, end)),
            focus="edit",
            ranges=[FocusRange(path=path, start_line=start, end_line=end)],
            node_ids=nodes,
            edited_path=path,
        )

    async def _run_tests(self) -> ToolOutcome:
        run = await self._workspace.run_tests(self._tree, timeout_s=self._test_timeout_s)
        result = run.result
        if result.outcome == "timeout":
            headline = f"Ran tests: timed out after {self._test_timeout_s:g} s"
        elif result.outcome == "error" and result.passed + result.failed == 0:
            headline = "Ran tests: the suite could not run"
        else:
            headline = f"Ran tests: {result.passed} of {result.passed + result.failed} pass"
        output = run.output[-MAX_TEST_OUTPUT:]
        return ToolOutcome(
            content=f"{headline}\n\n{output}", ok=True, summary=headline, tests=result
        )

    def _finish(self, arguments: dict[str, object]) -> ToolOutcome:
        note = cast(str, _string(arguments, "summary"))
        return ToolOutcome(
            content="Finished.", ok=True, summary="Finished the task", finish_summary=note
        )


def _failed(name: str, raw_arguments: str) -> str:
    """A failed call's summary, from the tool and its path argument only."""
    try:
        parsed = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError:
        parsed = None
    path = cast(dict[str, object], parsed).get("path") if isinstance(parsed, dict) else None
    labels = {"read_file": "read", "edit_file": "edit", "search": "search", "list_files": "list"}
    verb = labels.get(name, name.replace("_", " "))
    if name not in TOOL_NAMES:
        return f"Called an unknown tool, {name[:40]!r}"
    if isinstance(path, str) and path:
        return f"Could not {verb} {_name(path)[:60]}"
    return f"Could not {verb}"
