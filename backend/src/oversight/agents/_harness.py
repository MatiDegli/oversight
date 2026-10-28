"""Opt-in safeguards and compact search observations; the six tool schemas stay fixed."""

import ast
from collections import Counter
from dataclasses import dataclass
from pathlib import PurePosixPath


class ToolError(Exception):
    """A failed call: its message goes back to the model."""


@dataclass
class HarnessState:
    enabled: bool
    needs_tests: bool = False
    reminded: bool = False

    def check_syntax(self, path: str, before: str, after: str) -> None:
        if not self.enabled or PurePosixPath(path).suffix != ".py":
            return
        try:
            ast.parse(before, filename=path)
        except SyntaxError:
            return  # Preserve edits to files that were already broken.
        try:
            ast.parse(after, filename=path)
        except SyntaxError as error:
            raise ToolError(f"{path}: line {error.lineno}: {error.msg}") from None

    def edited(self) -> None:
        self.needs_tests = True
        self.reminded = False

    def tested(self) -> None:
        self.needs_tests = False

    def finish(self) -> None:
        if self.enabled and self.needs_tests and not self.reminded:
            self.reminded = True
            raise ToolError("run the tests first")


def search_rows(
    matches: list[tuple[str, int, str]], cap: int, line_cap: int, pack: bool
) -> list[str]:
    if pack and len(matches) > cap:
        counts = Counter(path for path, _, _ in matches)
        # Bound both file count and path length in the observation, even on deep trees.
        rows = [f"{path[:line_cap]}: {count} matches" for path, count in list(counts.items())[:cap]]
        if len(counts) > cap:
            rows.append(f"[{len(counts) - cap} more files]")
        rows.append(f"[{len(matches)} matches; narrow the query or path]")
        return rows
    rows = [f"{p}:{n}: {t.strip()[:line_cap]}" for p, n, t in matches[:cap]]
    if len(matches) > cap:
        rows.append(f"[{len(matches) - cap} more matches; narrow the search]")
    return rows
