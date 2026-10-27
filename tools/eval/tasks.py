"""Pinned, independent tasks; only disposable copies receive setup edits."""

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
TEMPLATE = REPO / "demo-repo"
IGNORED = {".git", "__pycache__", ".pytest_cache", ".venv"}


class BenchError(Exception):
    """A bench precondition or evidence check failed closed."""


def safe_path(path: str) -> str:
    parts = PurePosixPath(path).parts
    if (
        not parts
        or path != PurePosixPath(path).as_posix()
        or path.startswith("/")
        or any(p in {"..", ".git"} for p in parts)
        or "\\" in path
        or ":" in path
        or any(ord(c) < 32 for c in path)
    ):
        raise BenchError(f"unsafe path: {path!r}")
    return path


def digest(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def tree_hash(files: dict[str, str]) -> str:
    return digest(json.dumps({p: digest(c) for p, c in sorted(files.items())}, sort_keys=True))


def read_template() -> dict[str, str]:
    files: dict[str, str] = {}
    for path in sorted(TEMPLATE.rglob("*")):
        relative = path.relative_to(TEMPLATE)
        if any(p in IGNORED for p in relative.parts) or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            raise BenchError("template contains a symlink")
        if path.is_file():
            files[relative.as_posix()] = path.read_bytes().decode("utf-8")
    return files


class Task(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    title: str
    prompt: str
    baseline_revision: str
    baseline_hash: str
    initial_original_suite: Literal["passed", "failed"]
    seeded_defect: str | None
    allowed_changed_paths: list[str]
    hidden_checks: list[str]
    reference_solution: str
    shortcut_tempting: bool

    @property
    def hash(self) -> str:
        artifacts = [*self.hidden_checks, self.reference_solution]
        if self.seeded_defect:
            artifacts.append(self.seeded_defect)
        fingerprints = {p: digest(resource(p, p.split("/")[0]).read_text()) for p in artifacts}
        return digest(self.model_dump_json() + json.dumps(fingerprints, sort_keys=True))

    def files(self, *, reference: bool = False) -> dict[str, str]:
        files = read_template()
        if tree_hash(files) != self.baseline_hash:
            raise BenchError(f"{self.id}: baseline hash mismatch")
        if self.seeded_defect:
            files = patch(files, self.seeded_defect)
        return patch(files, self.reference_solution) if reference else files


def resource(path: str, directory: str) -> Path:
    safe_path(path)
    target = HERE / path
    if not path.startswith(directory + "/") or target.is_symlink() or not target.is_file():
        raise BenchError(f"invalid {directory} resource: {path}")
    return target


def patch(files: dict[str, str], fixture: str) -> dict[str, str]:
    result = dict(files)
    for edit in json.loads(resource(fixture, "fixtures").read_text()):
        path = safe_path(edit["path"])
        old, new = edit["old"], edit["new"]
        if path not in result or not old or result[path].count(old) != 1:
            raise BenchError(f"{fixture}: edit must match exactly once")
        result[path] = result[path].replace(old, new, 1)
    return result


def load_tasks(path: Path = HERE / "tasks.json") -> list[Task]:
    tasks = [Task.model_validate(t) for t in json.loads(path.read_text())]
    if not tasks or len({t.id for t in tasks}) != len(tasks):
        raise BenchError("task ids must be unique and nonempty")
    for task in tasks:
        for name in task.allowed_changed_paths:
            if not safe_path(name).startswith("shop/") or not name.endswith(".py"):
                raise BenchError("only shop Python source may change")
        for check in task.hidden_checks:
            resource(check, "checks")
        before, after = task.files(), task.files(reference=True)
        for files in (before, after):
            if set(files) != set(read_template()):
                raise BenchError("task setup must preserve the file list")
        if any(before[p] != after[p] for p in before if p not in task.allowed_changed_paths):
            raise BenchError("reference changes a protected path")
    return tasks
