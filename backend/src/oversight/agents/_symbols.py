"""Validated, bounded Python navigation through a disposable subprocess snapshot."""

import asyncio
import keyword
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from oversight.agents._harness import ToolError
from oversight.workspace import Tree, WorkspaceError, is_secret_path, run_process, secret_refusal

TIMEOUT_S = 20.0
_Kind = Literal[
    "class",
    "function",
    "method",
    "module",
    "statement",
    "param",
    "instance",
    "property",
    "reference",
]


class _Payload(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    matches: list[tuple[str, int, _Kind]]
    modules: list[str]
    definitions: int = Field(ge=0)
    references: int = Field(ge=0)


@dataclass(frozen=True)
class SymbolMatch:
    path: str
    line: int
    kind: str
    text: str


@dataclass(frozen=True)
class SymbolResult:
    matches: list[SymbolMatch]
    modules: list[str]
    definitions: int
    references: int


def valid_name(name: str) -> bool:
    return all(part.isidentifier() and not keyword.iskeyword(part) for part in name.split("."))


async def find_symbol(tree: Tree, name: str, under: str | None, cap: int) -> SymbolResult:
    if not valid_name(name):
        raise ToolError("name must be a Python identifier or dotted name")
    if under is not None:
        target = await asyncio.to_thread(tree.resolve, under)
        if is_secret_path(under):
            raise ToolError(secret_refusal(under))
        if not target.is_dir() and target.suffix not in {".py", ".pyi"}:
            raise ToolError("find_symbol covers Python; use search")
    with TemporaryDirectory(prefix="oversight-symbols-") as directory:
        temporary = Path(directory)
        snapshot = temporary / "source"
        snapshot.mkdir()
        for path in await tree.list_files():
            if is_secret_path(path) or Path(path).suffix not in {".py", ".pyi"}:
                continue
            try:
                file = await tree.read_text(path)
            except (OSError, WorkspaceError):
                continue
            destination = snapshot / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(file.content, encoding="utf-8")
        result = await run_process(
            [
                sys.executable,
                "-I",
                "-m",
                "oversight.agents._symbol_worker",
                str(tree.root),
                str(snapshot),
                name,
                under or "",
                str(cap),
            ],
            cwd=temporary,
            timeout_s=TIMEOUT_S,
            env={"HOME": directory},
        )
        if result.timed_out:
            raise ToolError("find_symbol reached its time limit; narrow the name or path")
        if result.returncode != 0 or result.truncated:
            raise ToolError("find_symbol could not analyze Python; use search")
        return await _validated(tree, result.stdout, cap)


async def _validated(tree: Tree, output: str, cap: int) -> SymbolResult:
    """Treat worker paths as untrusted and obtain displayed source through Tree again."""
    try:
        payload = _Payload.model_validate_json(output)
        matches: list[SymbolMatch] = []
        for path, number, kind in payload.matches[:cap]:
            if is_secret_path(path) or number < 1:
                continue
            try:
                await asyncio.to_thread(tree.resolve, path)
                file = await tree.read_text(path)
            except (OSError, WorkspaceError):
                continue
            lines = file.content.splitlines()
            if number <= len(lines):
                matches.append(SymbolMatch(path, number, kind, lines[number - 1]))
        modules = [module for module in payload.modules[:cap] if valid_name(module)]
        return SymbolResult(matches, modules, payload.definitions, payload.references)
    except (ValueError, TypeError, KeyError, IndexError):
        raise ToolError("find_symbol returned unreadable results; use search") from None
