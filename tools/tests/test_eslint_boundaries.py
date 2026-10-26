"""The frontend's module boundaries, as ESLint enforces them (docs/proposal.md, section 3).

Each case is a throwaway file under frontend/src/, linted with the real configuration and deleted
afterwards. Only the two boundary rules are read: an unresolved module also trips type-aware rules
that say nothing about boundaries. A probe is created exclusively under a name no existing file can
have, and only what the test created is removed.
"""

import contextlib
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
BOUNDARY_RULES = {"no-restricted-imports", "no-restricted-syntax"}

# (folder under src/, source, whether a boundary rule must report it)
CASES = [
    ("review", 'export const load = () => import("../graph/internal");', True),
    ("review", 'export const load = () => import("../graph/internal/deep.ts");', True),
    ("review", "export const load = () => import(`../graph/internal`);", True),
    ("review", 'export const load = () => import("../graph");', False),
    ("review", 'export const load = () => import("../graph/index.ts");', False),
    ("review", "export const load = () => import(`../graph`);", False),
    ("review", 'export const load = () => import("./panel");', False),
    ("review", 'export const load = () => import("../review/panel");', False),
    ("review/panels", 'export const load = () => import("../../graph/internal");', True),
    ("review/panels", 'export const load = () => import("../../graph");', False),
    ("review", 'export const load = () => import("cytoscape");', True),
    ("review", 'export const load = () => import("cytoscape-fcose/src");', True),
    ("graph", 'export const load = () => import("cytoscape");', False),
    ("", 'export const load = () => import("./graph/internal");', True),
    ("", 'export const load = () => import("./graph");', False),
    ("review", 'import "../graph/internal";', True),
    ("review", 'import "../graph";', False),
]


def case_id(case: tuple[str, str, bool]) -> str:
    return f"{case[0] or 'src'}: {case[1].removeprefix('export const load = () => ')}"


def lint(files: list[Path]) -> list[dict[str, object]]:
    pnpm = shutil.which("pnpm")
    assert pnpm, "pnpm is needed to lint the frontend: run `make install`"
    done = subprocess.run(
        [pnpm, "exec", "eslint", "--format", "json", *map(str, files)],
        cwd=FRONTEND,
        capture_output=True,
        text=True,
        timeout=300,
    )
    try:
        report: list[dict[str, object]] = json.loads(done.stdout)
    except json.JSONDecodeError:
        pytest.fail(f"eslint printed no report (exit {done.returncode}): {done.stderr}")
    return report


def make_directories(directory: Path, root: Path, created: list[Path]) -> None:
    """Create `directory` and its missing parents inside `root`, recording each one made."""
    inside = [d for d in [directory, *directory.parents] if d.is_relative_to(root)]
    for folder in reversed([d for d in inside if not d.exists()]):
        folder.mkdir()
        created.append(folder)


@pytest.fixture(scope="module")
def boundary_reports() -> Iterator[dict[int, bool]]:
    """Lint every case once; map each case's index to whether a boundary rule reported it."""
    src = FRONTEND / "src"
    created: list[Path] = []
    files: dict[int, Path] = {}
    try:
        for index, (folder, source, _) in enumerate(CASES):
            directory = src / folder
            make_directories(directory, src, created)
            # mkstemp creates the file exclusively, under a name that is not taken
            descriptor, name = tempfile.mkstemp(
                dir=directory, prefix="boundary_probe_", suffix=".ts"
            )
            files[index] = Path(name)  # recorded before the write: a failed write is still removed
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(source + "\n")
        by_file = {Path(str(entry["filePath"])): entry for entry in lint(list(files.values()))}
        reported: dict[int, bool] = {}
        for index, probe in files.items():
            messages = by_file[probe]["messages"]
            assert isinstance(messages, list)
            reported[index] = any(message["ruleId"] in BOUNDARY_RULES for message in messages)
        yield reported
    finally:
        for probe in files.values():
            probe.unlink(missing_ok=True)
        for directory in sorted(created, key=lambda path: len(path.parts), reverse=True):
            with contextlib.suppress(OSError):  # rmdir removes only an empty directory
                directory.rmdir()


@pytest.mark.parametrize("index", range(len(CASES)), ids=[case_id(case) for case in CASES])
def test_a_feature_folder_is_reached_only_through_its_index(
    boundary_reports: dict[int, bool], index: int
) -> None:
    assert boundary_reports[index] is CASES[index][2]
