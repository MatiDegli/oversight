"""Keep repository module names unambiguous on case-insensitive file systems."""

import os
from collections import defaultdict
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = ("backend", "frontend", "tools", "contracts", "fixtures")
MODULE_EXTENSIONS = {".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs", ".py"}
BUILD_OUTPUT = {
    "node_modules",
    "dist",
    "build",
    "out",
    "coverage",
    "htmlcov",
    "playwright-report",
    "test-results",
    "__pycache__",
    ".git",
    ".venv",
    ".vite",
    ".cache",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".pyright",
    ".runtime",
    ".eval-output",
}


def case_collisions(root: Path) -> list[str]:
    """Return every ambiguous sibling pair without reading file contents or following links."""
    collisions: set[tuple[str, str]] = set()
    for directory, dirs, files in os.walk(root):
        entries: dict[str, list[str]] = defaultdict(list)
        modules: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for name in dirs + files:
            entries[name.casefold()].append(name)
        pairs = [pair for names in entries.values() for pair in combinations(names, 2)]
        for name in files:
            path = Path(name)
            # Strip only the last extension, so .d.ts is handled as .ts.
            if path.suffix.casefold() in MODULE_EXTENSIONS:
                modules[path.stem.casefold()].append((name, path.stem))
        for names in modules.values():
            pairs.extend(
                (left, right)
                for (left, left_stem), (right, right_stem) in combinations(names, 2)
                if left_stem != right_stem
            )
        for name in dirs:
            pairs.extend(
                (module, name) for module, stem in modules.get(name.casefold(), []) if stem != name
            )
        for left, right in pairs:
            left, right = sorted((left, right))
            parent = Path(directory).relative_to(root)
            collisions.add(((parent / left).as_posix(), (parent / right).as_posix()))
        dirs[:] = [name for name in dirs if name.casefold() not in BUILD_OUTPUT]
    return [f"{left} <-> {right}" for left, right in sorted(collisions)]


def test_repository_names_are_case_safe() -> None:
    # Case-insensitive file systems can resolve an import to the other file.
    collisions = [
        f"{source}: {pair}" for source in SOURCE_ROOTS for pair in case_collisions(ROOT / source)
    ]
    assert not collisions, "Case-insensitive name collisions:\n" + "\n".join(collisions)


def test_checker_catches_case_only_modules_and_allows_test_suffix(tmp_path: Path) -> None:
    (tmp_path / "foo.ts").touch()
    (tmp_path / "foo.test.ts").touch()
    assert case_collisions(tmp_path) == []
    (tmp_path / "Foo.tsx").touch()
    assert case_collisions(tmp_path) == ["Foo.tsx <-> foo.ts"]
