"""Read frozen task/diff artifacts without importing the construction key."""

import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE.parents[2] / "demo-repo"
FROZEN_MANIFEST_SHA256 = "5fe04cae2491380078c1a2a9a34059f574bb590e38bc8c8b3d503bc590dfcc30"
IGNORED = {".git", "__pycache__", ".pytest_cache", ".venv"}


class FreezeError(ValueError):
    """Frozen inputs no longer match the reviewed manifest."""


@dataclass(frozen=True)
class Patch:
    id: str
    set: str
    sha256: str
    task: str
    diff: str
    path: Path


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def content_hash(task: str, diff: str) -> str:
    content = json.dumps(
        {"task.txt": task, "change.diff": diff},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return sha256(content.encode("utf-8"))


def read_manifest(root: Path = HERE) -> dict:
    raw = (root / "manifest.json").read_bytes()
    if sha256(raw) != FROZEN_MANIFEST_SHA256:
        raise FreezeError("frozen manifest changed")
    return json.loads(raw)


def load_patches(root: Path = HERE) -> list[Patch]:
    manifest = read_manifest(root)
    patches = []
    for entry in manifest["patches"]:
        pid, group = entry["id"], entry["set"]
        prefix = {"bench": "b", "dev": "d"}.get(group)
        if set(entry) != {"id", "set", "sha256"} or not prefix:
            raise FreezeError("invalid public manifest entry")
        if not re.fullmatch(prefix + r"\d{2}", pid):
            raise FreezeError("invalid patch id")
        directory = root / "patches" / group / pid
        paths = [directory / "task.txt", directory / "change.diff"]
        if directory.is_symlink() or any(p.is_symlink() for p in paths):
            raise FreezeError(f"{pid}: symlink artifact")
        task, diff = (p.read_bytes().decode("utf-8") for p in paths)
        if content_hash(task, diff) != entry["sha256"]:
            raise FreezeError(f"{pid}: frozen content hash changed")
        patches.append(Patch(pid, group, entry["sha256"], task, diff, paths[1]))
    ids = {p.id for p in patches}
    if len(ids) != len(patches):
        raise FreezeError("duplicate patch id")
    for group, count in (("bench", 20), ("dev", 4)):
        selected = {p.id for p in patches if p.set == group}
        invalid_count = len(selected) != count if group == "bench" else len(selected) < count
        if invalid_count:
            raise FreezeError(f"invalid {group} count")
        if {p.name for p in (root / "patches" / group).iterdir()} != selected:
            raise FreezeError(f"unlisted {group} artifact")
    return patches


def check_baseline(template: Path = TEMPLATE, root: Path = HERE) -> None:
    files = {}
    for path in sorted(template.rglob("*")):
        relative = path.relative_to(template)
        if any(p in IGNORED for p in relative.parts) or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            raise FreezeError("baseline contains a symlink")
        if path.is_file():
            files[relative.as_posix()] = sha256(path.read_bytes())
    digest = sha256(json.dumps(files, sort_keys=True).encode("utf-8"))
    if digest != read_manifest(root)["baseline_sha256"]:
        raise FreezeError("demo-repo baseline hash changed")


def main() -> int:
    try:
        check_baseline()
        patches = load_patches()
    except (OSError, ValueError) as error:
        print(f"Freeze check refused: {error}", file=sys.stderr)
        return 1
    bench = sum(p.set == "bench" for p in patches)
    print(f"Frozen hashes OK: {bench} bench, {len(patches) - bench} development patches.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
