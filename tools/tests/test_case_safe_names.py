"""Keep repository module names unambiguous on case-insensitive file systems."""

import os
from collections import defaultdict
from itertools import combinations
from pathlib import Path

import pytest

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


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        pytest.param(("foo/", "Foo/"), ["Foo <-> foo"], id="directories"),
        pytest.param(("foo/", "Foo.py"), ["Foo.py <-> foo"], id="module-directory"),
        pytest.param(("Foo/", "foo.py"), ["Foo <-> foo.py"], id="directory-module"),
        pytest.param(("foo/", "foo.py"), [], id="same-case-module-directory"),
        pytest.param(("foo.py", "Foo.ts"), ["Foo.ts <-> foo.py"], id="cross-extension"),
        pytest.param(("foo.py", "foo.ts"), [], id="same-case-cross-extension"),
        pytest.param(("foo.py", "Foo.TS"), ["Foo.TS <-> foo.py"], id="uppercase-extension"),
        pytest.param(("foo.d.py", "Foo.d.ts"), ["Foo.d.ts <-> foo.d.py"], id="declaration-stem"),
        pytest.param(("foo.d.py", "foo.d.ts"), [], id="same-case-declaration-stem"),
        pytest.param(("foo.py", "Foo.d.ts"), [], id="declaration-retains-dot-d"),
        pytest.param(("readme.md", "README.md"), ["README.md <-> readme.md"], id="non-module-name"),
        pytest.param(("readme.md", "README.txt"), [], id="non-module-extensions"),
        pytest.param(
            ("parent/foo/", "parent/Foo.py"),
            ["parent/Foo.py <-> parent/foo"],
            id="nested-parent-path",
        ),
        pytest.param(
            ("z/foo.py", "z/Foo.py", "a/foo.ts", "a/Foo.ts"),
            ["a/Foo.ts <-> a/foo.ts", "z/Foo.py <-> z/foo.py"],
            id="sorted-parents",
        ),
        pytest.param(
            ("foo.tsx", "foo.ts", "Foo.ts"),
            ["Foo.ts <-> foo.ts", "Foo.ts <-> foo.tsx"],
            id="sorted-deduplicated-pairs",
        ),
    ],
)
def test_checker_sibling_pairs(tmp_path: Path, paths: tuple[str, ...], expected: list[str]) -> None:
    for relative in paths:
        path = tmp_path / relative
        if relative.endswith("/"):
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()

    assert case_collisions(tmp_path) == expected


@pytest.mark.parametrize("output", ["node_modules", "dist", "__pycache__", "Node_Modules"])
def test_checker_prunes_build_output(tmp_path: Path, output: str) -> None:
    generated = tmp_path / output
    generated.mkdir()
    for name in ("foo.py", "Foo.py"):
        (generated / name).touch()
        (tmp_path / name).touch()

    assert case_collisions(generated) == ["Foo.py <-> foo.py"]
    assert case_collisions(tmp_path) == ["Foo.py <-> foo.py"]


@pytest.mark.parametrize(
    ("left", "right"),
    [("Node_Modules", "node_modules"), ("DIST", "dist"), ("__PYCACHE__", "__pycache__")],
)
def test_checker_reports_build_output_siblings(tmp_path: Path, left: str, right: str) -> None:
    for name in (right, left):
        output = tmp_path / name
        output.mkdir()
        (output / "Foo.py").touch()
        (output / "foo.py").touch()

    assert case_collisions(tmp_path) == [f"{left} <-> {right}"]


def test_checker_does_not_follow_directory_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "Foo.py").touch()
    (target / "foo.py").touch()
    root = tmp_path / "scan"
    root.mkdir()
    (root / "linked").symlink_to(target, target_is_directory=True)

    assert case_collisions(target) == ["Foo.py <-> foo.py"]
    assert case_collisions(root) == []
