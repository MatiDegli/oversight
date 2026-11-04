"""Independent, static preparation for a future reviewed-preview mount boundary.

No install, fetch, launch declaration, settings load, mount or production launch lives here.
Inventories are recorded by the trusted external preparer, never learned from current bytes.
"""

import ast
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import tempfile
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import unquote, urlsplit

from oversight.workspace.runner import Spawn, run_process

MAX_ENTRIES = 100_000
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_CONTENT_BYTES = 8 * 1024**3
_ROLES = {"backend", "contracts", "demo-test", "frontend", "tool", "system"}
_TOOLS = {"python", "uv-python", "uv", "node", "pnpm"}


@dataclass(frozen=True)
class InventoryEntry:
    path: str
    kind: Literal["file", "directory", "symlink"]
    sha256: str = ""
    size: int = 0
    mode: int = 0o644
    target: str = ""


@dataclass(frozen=True)
class Inventory:
    name: str
    role: str
    root: Path
    entries: tuple[InventoryEntry, ...]
    digest: str
    owned: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", tuple(self.entries))


@dataclass(frozen=True)
class RootMapping:
    source: Path
    target: Path
    read_only: bool = True


@dataclass(frozen=True)
class ToolIdentity:
    name: str
    path: Path
    sha256: str


@dataclass(frozen=True)
class ProjectImport:
    site: Path
    original: Path
    replacement: Path
    packages: tuple[str, ...]
    import_subdirectory: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "packages", tuple(self.packages))


@dataclass(frozen=True)
class PreparationInputs:
    tree: Path
    excluded_roots: tuple[Path, ...]
    preview_directory: Path
    preparation_storage: Path
    inventories: tuple[Inventory, ...]
    tools: tuple[ToolIdentity, ...]
    system_entries: tuple[Path, ...]
    root_mappings: tuple[RootMapping, ...]
    imports: tuple[ProjectImport, ...]
    project_environment: Path
    demo_python: Path
    resources: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        for name in (
            "excluded_roots",
            "inventories",
            "tools",
            "system_entries",
            "root_mappings",
            "imports",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        object.__setattr__(self, "resources", tuple((k, v) for k, v in self.resources))


@dataclass(frozen=True)
class PreparedPreview:
    root: Path
    tree: Path
    state: Path
    root_mappings: tuple[RootMapping, ...]
    manifests: tuple[Inventory, ...]
    tools: tuple[ToolIdentity, ...]
    import_paths: tuple[Path, ...]
    path: tuple[Path, ...]
    project_environment: Path
    demo_python: Path
    resources: tuple[tuple[str, int], ...]
    build_log: Path

    def __post_init__(self) -> None:
        for name in ("root_mappings", "manifests", "tools", "import_paths", "path"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        object.__setattr__(self, "resources", tuple((k, v) for k, v in self.resources))


def inventory_digest(entries: Sequence[InventoryEntry]) -> str:
    """Canonical SHA-256 used by the external preparer; order is significant."""
    if len(entries) > MAX_ENTRIES:
        raise ValueError("prepared inventory exceeds entry bound")
    data = json.dumps(
        [asdict(e) for e in entries], sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    if len(data) > MAX_MANIFEST_BYTES:
        raise ValueError("prepared inventory exceeds manifest bound")
    return hashlib.sha256(data).hexdigest()


def _absolute(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("preparation requires normalized absolute paths")


def _overlap(a: Path, b: Path) -> bool:
    return a.is_relative_to(b) or b.is_relative_to(a)


def _directory(path: Path, *, create: bool = False) -> None:
    _absolute(path)
    current = Path("/")
    for part in path.parts[1:]:
        current /= part
        if create:
            current.mkdir(mode=0o700, exist_ok=True)
        if not stat.S_ISDIR(current.lstat().st_mode):
            raise ValueError("preparation directory contains a link or non-directory")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_CONTENT_BYTES:
            raise ValueError("unsafe or oversized prepared content")
        while data := stream.read(1024 * 1024):
            digest.update(data)
        after = os.fstat(stream.fileno())
        if (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_nlink,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ValueError("prepared content changed during hashing")
    return digest.hexdigest()


def _verify(inventory: Inventory) -> None:
    _directory(inventory.root)
    if not inventory.entries or inventory_digest(inventory.entries) != inventory.digest:
        raise ValueError("missing or truncated prepared inventory")
    expected = {e.path: e for e in inventory.entries}
    if len(expected) != len(inventory.entries):
        raise ValueError("duplicate prepared inventory entry")
    actual: set[str] = set()
    total = 0
    pending = [inventory.root]
    while pending:
        for path in pending.pop().iterdir():
            relative = path.relative_to(inventory.root).as_posix()
            entry = expected.get(relative)
            if entry is None or len(actual) >= MAX_ENTRIES:
                raise ValueError("unrecorded prepared entry")
            actual.add(relative)
            info = path.lstat()
            if info.st_mode & 0o7777 != entry.mode or info.st_mode & 0o6000:
                raise ValueError("prepared entry mode changed")
            if entry.kind == "directory" and stat.S_ISDIR(info.st_mode):
                pending.append(path)
            elif entry.kind == "symlink" and stat.S_ISLNK(info.st_mode):
                if os.readlink(path) != entry.target:
                    raise ValueError("prepared symlink changed")
            elif entry.kind == "file" and stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                total += info.st_size
                if (
                    total > MAX_CONTENT_BYTES
                    or info.st_size != entry.size
                    or _sha(path) != entry.sha256
                ):
                    raise ValueError("prepared content changed or exceeds bound")
            else:
                raise ValueError("unsupported prepared entry identity")
    if actual != set(expected):
        raise ValueError("prepared inventory entries are missing")


def _host(path: Path, mappings: Sequence[RootMapping]) -> Path:
    _absolute(path)
    matches = [m for m in mappings if path.is_relative_to(m.target)]
    if len(matches) != 1:
        raise ValueError("path has no unique allowlisted mapping")
    mapping = matches[0]
    return mapping.source / path.relative_to(mapping.target)


def _resolve(path: Path, mappings: Sequence[RootMapping]) -> Path:
    """Resolve symlinks using child-root semantics, never the host's absolute targets."""
    _absolute(path)
    pending = list(path.parts[1:])
    resolved = Path("/")
    links = 0
    while pending:
        part = pending.pop(0)
        if part == "..":
            if resolved == Path("/"):
                raise ValueError("symlink escapes child root")
            resolved = resolved.parent
            continue
        if part == ".":
            continue
        candidate = resolved / part
        # Parent mountpoint directories may not themselves have a mapping.
        if any(m.target != candidate and m.target.is_relative_to(candidate) for m in mappings):
            resolved = candidate
            continue
        host = _host(candidate, mappings)
        if host.is_symlink():
            links += 1
            if links > 40:
                raise ValueError("prepared symlink cycle")
            target = Path(os.readlink(host))
            if target.is_absolute():
                resolved = Path("/")
                pending = list(target.parts[1:]) + pending
            else:
                pending = list(target.parts) + pending
        else:
            host.lstat()
            resolved = candidate
    return resolved


def _executable(
    path: Path,
    mappings: Sequence[RootMapping],
    search: Sequence[Path],
    seen: frozenset[Path] = frozenset(),
) -> Path:
    if len(seen) > 40:
        raise ValueError("prepared interpreter chain exceeds bound")
    resolved = _resolve(path, mappings)
    if not any(resolved.is_relative_to(m.target) and m.read_only for m in mappings):
        raise ValueError("prepared interpreter/executable resolves into writable storage")
    host = _host(resolved, mappings)
    info = host.stat()
    if resolved in seen or not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o111:
        raise ValueError("invalid prepared executable")
    with host.open("rb") as stream:
        header = stream.read(4096)
        interpreter = None
        if header.startswith(b"#!"):
            words = header.split(b"\n", 1)[0][2:].decode("utf-8").split()
            if not words:
                raise ValueError("empty prepared shebang")
            interpreter = Path(words[0])
            if interpreter.name == "env":
                _executable(interpreter, mappings, search, seen | {resolved})
                if len(words) != 2 or not re.fullmatch(r"[\w.-]+", words[1]):
                    raise ValueError("unsupported prepared env shebang")
                interpreter = None
                for directory in search:
                    try:
                        _resolve(directory / words[1], mappings)
                    except FileNotFoundError:
                        continue
                    interpreter = directory / words[1]
                    break
                if interpreter is None:
                    raise ValueError("unresolved prepared shebang")
        elif header.startswith(b"\x7fELF"):
            if (
                len(header) < 64
                or header[4:6] not in (b"\x02\x01", b"\x01\x01")
                or header[6] != 1
                or struct.unpack_from("<H", header, 16)[0] not in {2, 3}
            ):
                raise ValueError("unsupported prepared ELF layout")
            wide = header[4] == 2
            offset = struct.unpack_from("<Q" if wide else "<I", header, 32 if wide else 28)[0]
            size, count = struct.unpack_from("<HH", header, 54 if wide else 42)
            if count > 128 or size != (56 if wide else 32):
                raise ValueError("unsupported prepared ELF headers")
            for index in range(count):
                stream.seek(offset + index * size)
                program = stream.read(size)
                if len(program) != size:
                    raise ValueError("truncated prepared ELF headers")
                if struct.unpack_from("<I", program)[0] == 3:
                    start = struct.unpack_from("<Q" if wide else "<I", program, 8 if wide else 4)[0]
                    stream.seek(start)
                    raw = stream.read(4096).split(b"\0", 1)[0]
                    interpreter = Path(raw.decode("utf-8"))
        else:
            raise ValueError("unsupported prepared executable format")
    if interpreter is not None:
        _executable(interpreter, mappings, search, seen | {resolved})
    return host


def _validate(inputs: PreparationInputs) -> None:
    roots = [i.root for i in inputs.inventories]
    if {i.role for i in inputs.inventories} != _ROLES or len(set(roots)) != len(roots):
        raise ValueError("missing or duplicate prepared dependency/tool/system inventory")
    if len({i.name for i in inputs.inventories}) != len(roots) or len(roots) > 64:
        raise ValueError("duplicate or excessive prepared inventory names")
    if not _TOOLS.issubset({t.name for t in inputs.tools}) or len(
        {t.name for t in inputs.tools}
    ) != len(inputs.tools):
        raise ValueError("missing or duplicate prepared tools")
    if (
        sum(len(i.entries) for i in inputs.inventories) > MAX_ENTRIES
        or sum(e.size for i in inputs.inventories for e in i.entries) > MAX_CONTENT_BYTES
        or len(inputs.tools) > 32
        or len(inputs.imports) > 128
        or len(inputs.system_entries) > 4096
    ):
        raise ValueError("prepared inputs exceed bounds")
    for path in (
        inputs.tree,
        inputs.preview_directory,
        inputs.preparation_storage,
        *inputs.excluded_roots,
        *roots,
    ):
        _absolute(path)
    if inputs.preview_directory.parent.name != "previews":
        raise ValueError("preview directory must be previews/<id>")
    if any(_overlap(inputs.tree, excluded) for excluded in inputs.excluded_roots):
        raise ValueError("T overlaps an excluded source")
    if _overlap(inputs.preview_directory, inputs.preparation_storage):
        raise ValueError("preparation storage must be separate from preview state")
    if _overlap(inputs.tree, inputs.preview_directory) or _overlap(
        inputs.tree, inputs.preparation_storage
    ):
        raise ValueError("preparation and preview storage must be outside T")
    for root in roots:
        if _overlap(root, inputs.preview_directory) or _overlap(root, inputs.tree):
            raise ValueError("prepared source overlaps mutable preview paths")
        if any(_overlap(root, excluded) for excluded in inputs.excluded_roots):
            raise ValueError("prepared source exposes an excluded root")
        if any(_overlap(root, other) for other in roots if other != root):
            raise ValueError("prepared sources overlap")
    if not inputs.system_entries or not inputs.imports:
        raise ValueError("missing recorded system/import inputs")
    mappings = inputs.root_mappings
    if len(mappings) != len(roots) + 1 or {m.source for m in mappings} != {*roots, inputs.tree}:
        raise ValueError("root mappings do not match prepared inventories and T")
    for mapping in mappings:
        _absolute(mapping.target)
        if mapping.target == Path("/") or any(
            _overlap(mapping.target, p)
            for p in (
                Path("/state"),
                Path("/tmp"),
                Path("/proc"),
                Path("/dev"),
                Path("/mnt"),
                Path("/run"),
            )
        ):
            raise ValueError("root mapping exposes a reserved destination")
        if mapping.read_only != (mapping.source != inputs.tree):
            raise ValueError("only T and child state may be writable")
        if any(_overlap(mapping.target, m.target) for m in mappings if m is not mapping):
            raise ValueError("root mapping targets overlap")


def _evict(inventory: Inventory, inputs: PreparationInputs) -> None:
    root = inventory.root
    if (
        inventory.owned
        and root != inputs.preparation_storage
        and root.is_relative_to(inputs.preparation_storage)
    ):
        _directory(root.parent)
        if root.is_symlink():
            root.unlink()
        elif root.exists():
            shutil.rmtree(root)


def _translated(raw: str, imports: Sequence[ProjectImport]) -> str:
    path = Path(raw)
    _absolute(path)
    matches = [i for i in imports if path.is_relative_to(i.original)]
    if len(matches) != 1:
        raise ValueError("unsupported editable import mapping")
    return str(matches[0].replacement / path.relative_to(matches[0].original))


def _metadata(path: Path) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("unsupported prepared metadata file")
        data = stream.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise ValueError("prepared metadata exceeds bound")
        return data.decode("utf-8")


def _configuration(path: Path, mappings: Sequence[RootMapping], search: Sequence[Path]) -> str:
    """Translate only known prepared roots in venv metadata, without running Python."""
    if path.stat().st_size > 16_384:
        raise ValueError("oversized prepared Python configuration")
    values: dict[str, str] = {}
    for line in _metadata(path).splitlines():
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or key in values:
            raise ValueError("unsupported prepared Python configuration")
        if key not in {
            "home",
            "executable",
            "implementation",
            "uv",
            "version_info",
            "version",
            "include-system-site-packages",
            "prompt",
        }:
            raise ValueError("unsupported prepared Python configuration key")
        if key in {"home", "executable"}:
            candidate = Path(value)
            _absolute(candidate)
            for mapping in mappings:
                if candidate.is_relative_to(mapping.source):
                    candidate = mapping.target / candidate.relative_to(mapping.source)
                    break
            resolved = _resolve(candidate, mappings)
            if not any(resolved.is_relative_to(m.target) and m.read_only for m in mappings):
                raise ValueError("prepared Python configuration resolves into writable storage")
            if key == "home":
                _directory(_host(resolved, mappings))
            else:
                _executable(candidate, mappings, search)
            value = str(candidate)
        values[key] = value
    if "home" not in values or values.get("include-system-site-packages") != "false":
        raise ValueError("prepared Python configuration permits unrecorded site packages")
    return "".join(f"{key} = {value}\n" for key, value in values.items())


def _sanitize_site(site: Path, imports: Sequence[ProjectImport]) -> None:
    _directory(site)
    if any(p.is_symlink() for p in site.rglob("*")):
        raise ValueError("unsupported link in prepared import environment")
    finders = {p.stem for p in site.glob("__editable__*.py")}
    for path in site.iterdir():
        if path.stem in {"sitecustomize", "usercustomize"} or path.suffix == ".egg-link":
            raise ValueError("unsupported prepared startup hook")
        if path.name.startswith("__editable__") and path.suffix == ".py":
            syntax = ast.parse(_metadata(path))
            found = False
            for node in syntax.body:
                targets = (
                    node.targets
                    if isinstance(node, ast.Assign)
                    else ([node.target] if isinstance(node, ast.AnnAssign) else [])
                )
                if any(isinstance(t, ast.Name) and t.id == "MAPPING" for t in targets):
                    if not isinstance(node, ast.Assign | ast.AnnAssign) or node.value is None:
                        raise ValueError("unsupported editable finder")
                    mapping: Any = ast.literal_eval(node.value)
                    if not isinstance(mapping, dict) or not mapping:
                        raise ValueError("unsupported editable finder")
                    for value in cast(dict[object, object], mapping).values():
                        if not isinstance(value, str):
                            raise ValueError("unsupported editable finder target")
                        _translated(value, imports)
                    found = True
            if not found:
                raise ValueError("unsupported editable finder")
            path.unlink()
        elif path.suffix == ".pth":
            lines: list[str] = []
            for line in _metadata(path).splitlines():
                if not line or line.startswith("#"):
                    continue
                if line.startswith(("import ", "import\t")):
                    match = re.fullmatch(r"import (__editable__\w+);\s*\1\.install\(\)", line)
                    if match is None or match[1] not in finders:
                        raise ValueError("unsupported prepared pth hook")
                else:
                    lines.append(_translated(line, imports))
            path.write_text("\n".join(lines) + "\n")
        elif path.name.endswith(".dist-info"):
            direct = path / "direct_url.json"
            if direct.exists():
                data: Any = json.loads(_metadata(direct))
                if not isinstance(data, dict):
                    raise ValueError("unsupported prepared editable metadata")
                data = cast(dict[str, Any], data)
                if data.get("dir_info", {}).get("editable"):
                    url = urlsplit(str(data["url"]))
                    if url.scheme != "file" or url.netloc or url.query or url.fragment:
                        raise ValueError("unsupported editable URL")
                    data["url"] = Path(_translated(unquote(url.path), imports)).as_uri()
                    direct.write_text(json.dumps(data))
    (site / "oversight_preview.pth").write_text(
        "\n".join(str(i.replacement / i.import_subdirectory) for i in imports) + "\n"
    )
    for group in imports:
        for name in group.packages:
            for installed in (site / name, site / (name + ".py")):
                if installed.is_dir():
                    shutil.rmtree(installed)
                elif installed.exists():
                    installed.unlink()
    for bytecode in site.rglob("__editable__*.pyc"):
        bytecode.unlink()


@contextmanager
def _layout(inputs: PreparationInputs) -> Generator[tuple[RootMapping, ...]]:
    """Retain private copies only after preparation succeeds; never change input inventories."""
    _directory(inputs.preparation_storage)
    output = Path(tempfile.mkdtemp(prefix="preview-", dir=inputs.preparation_storage))
    mappings: list[RootMapping] = []
    try:
        for mapping in inputs.root_mappings:
            groups = [i for i in inputs.imports if i.site.is_relative_to(mapping.target)]
            if not groups:
                mappings.append(mapping)
                continue
            destination = output / str(len(mappings))
            shutil.copytree(mapping.source, destination, symlinks=True)
            inventory = next(i for i in inputs.inventories if i.root == mapping.source)
            # Do not mint fresh evidence for bytes that changed between verification and copy.
            _verify(
                Inventory(
                    inventory.name, inventory.role, destination, inventory.entries, inventory.digest
                )
            )
            config = destination / "pyvenv.cfg"
            if config.exists():
                config.write_text(
                    _configuration(
                        config,
                        inputs.root_mappings,
                        tuple(dict.fromkeys(t.path.parent for t in inputs.tools)),
                    )
                )
            for site in dict.fromkeys(group.site for group in groups):
                _sanitize_site(
                    destination / site.relative_to(mapping.target),
                    [g for g in groups if g.site == site],
                )
            mappings.append(RootMapping(destination, mapping.target))
        yield tuple(mappings)
    except BaseException:
        shutil.rmtree(output)
        raise


def _effective_manifest(inventory: Inventory, root: Path) -> Inventory:
    if root == inventory.root:
        return inventory
    entries: list[InventoryEntry] = []
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        relative = path.relative_to(root).as_posix()
        mode = info.st_mode & 0o777
        if path.is_symlink():
            entries.append(InventoryEntry(relative, "symlink", mode=mode, target=os.readlink(path)))
        elif path.is_dir():
            entries.append(InventoryEntry(relative, "directory", mode=mode))
        else:
            entries.append(InventoryEntry(relative, "file", _sha(path), info.st_size, mode))
    return Inventory(
        inventory.name, inventory.role, root, tuple(entries), inventory_digest(entries), True
    )


def _verify_inputs(inputs: PreparationInputs, search: Sequence[Path]) -> None:
    for inventory in inputs.inventories:
        try:
            _verify(inventory)
            mapping = next(m for m in inputs.root_mappings if m.source == inventory.root)
            for entry in inventory.entries:
                if entry.kind == "symlink":
                    resolved = _resolve(mapping.target / entry.path, inputs.root_mappings)
                    if not any(
                        resolved.is_relative_to(m.target) and m.read_only
                        for m in inputs.root_mappings
                    ):
                        raise ValueError("prepared symlink resolves into writable storage")
                elif entry.kind == "file" and entry.mode & 0o111:
                    _executable(mapping.target / entry.path, inputs.root_mappings, search)
                if entry.path == "pyvenv.cfg":
                    if entry.kind != "file":
                        raise ValueError("prepared Python configuration must be a regular file")
                    _configuration(inventory.root / entry.path, inputs.root_mappings, search)
            for tool in inputs.tools:
                if tool.path.is_relative_to(mapping.target):
                    host = _executable(tool.path, inputs.root_mappings, search)
                    if _sha(host) != tool.sha256:
                        raise ValueError("stale prepared executable identity")
        except (ValueError, OSError):
            _evict(inventory, inputs)
            raise
    for tool in inputs.tools:
        if not any(
            tool.path.is_relative_to(m.target) and m.read_only for m in inputs.root_mappings
        ):
            raise ValueError("prepared tool is not in an immutable inventory")
    for inventory in inputs.inventories:
        if inventory.role in {"backend", "contracts", "demo-test"}:
            target = next(m.target for m in inputs.root_mappings if m.source == inventory.root)
            sites = {group.site for group in inputs.imports}
            if any(
                (target / e.path).parent not in sites
                for e in inventory.entries
                if Path(e.path).suffix in {".pth", ".egg-link"}
            ):
                raise ValueError("unmapped prepared import hook")
    for entry in inputs.system_entries:
        host = _host(_resolve(entry, inputs.root_mappings), inputs.root_mappings)
        if not any(i.role == "system" and host.is_relative_to(i.root) for i in inputs.inventories):
            raise ValueError("required system entry is not recorded")


def _validate_imports(inputs: PreparationInputs, search: Sequence[Path]) -> None:
    tree_target = next(m.target for m in inputs.root_mappings if m.source == inputs.tree)
    for group in inputs.imports:
        _absolute(group.original)
        _absolute(group.site)
        _absolute(group.replacement)
        relative = Path(group.import_subdirectory)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("unsupported project import subdirectory")
        if not group.replacement.is_relative_to(tree_target) or not group.packages:
            raise ValueError("project imports must target T")
        _directory(_host(group.site, inputs.root_mappings))
        if not any(
            group.site.is_relative_to(m.target) and m.read_only for m in inputs.root_mappings
        ):
            raise ValueError("prepared import sites must be immutable")
        if not any(group.original.is_relative_to(p) for p in inputs.excluded_roots):
            raise ValueError("original import project must be explicitly excluded")
        for package in group.packages:
            if not re.fullmatch(r"[A-Za-z_]\w*", package):
                raise ValueError("unsupported project package")
            root = _host(group.replacement / relative, inputs.root_mappings)
            _directory(root)
            initializer = root / package / "__init__.py"
            if (
                (root / package).is_symlink()
                or (root / (package + ".py")).is_symlink()
                or initializer.is_symlink()
            ):
                raise ValueError("project import witness is a link")
            if not ((root / (package + ".py")).is_file() or initializer.is_file()):
                raise ValueError("missing project import witness in T")
    environment = _host(inputs.project_environment, inputs.root_mappings)
    _directory(environment)
    if not any(
        i.role == "backend" and environment.is_relative_to(i.root) for i in inputs.inventories
    ):
        raise ValueError("project environment must belong to the prepared backend")
    _configuration(environment / "pyvenv.cfg", inputs.root_mappings, search)
    _executable(inputs.demo_python, inputs.root_mappings, search)


async def prepare_preview(
    inputs: PreparationInputs, *, spawn: Spawn = run_process
) -> PreparedPreview:
    """Verify on every call, prepare mountpoints, and return evidence without executing code.

    ``spawn`` is the existing runner seam; static verification currently needs no process.
    Returned mappings are proposals. Their actual immutable mounts require boundary wiring.
    Every call makes fresh import copies; callers retain them until verified child teardown.
    """
    _validate(inputs)
    _directory(inputs.tree)
    search = tuple(dict.fromkeys(t.path.parent for t in inputs.tools))
    _verify_inputs(inputs, search)
    _validate_imports(inputs, search)
    for part in (
        "state/home",
        "state/cache",
        "state/config",
        "state/data",
        "state/build",
        "metadata",
        "published",
        "logs",
        "root",
    ):
        _directory(inputs.preview_directory / part, create=True)
    with _layout(inputs) as copies:
        mappings = (
            *copies,
            RootMapping(inputs.preview_directory / "state", Path("/state"), False),
        )
        root = inputs.preview_directory / "root"
        for mapping in mappings:
            _directory(root / mapping.target.relative_to("/"), create=True)
        for part in ("tmp", "proc", "dev"):
            _directory(root / part, create=True)
        manifests = tuple(
            _effective_manifest(
                i,
                next(
                    m.source
                    for m in mappings
                    if m.target
                    == next(old.target for old in inputs.root_mappings if old.source == i.root)
                ),
            )
            for i in inputs.inventories
        )
        return PreparedPreview(
            root,
            inputs.tree,
            inputs.preview_directory / "state",
            mappings,
            manifests,
            inputs.tools,
            tuple(
                dict.fromkeys(
                    p
                    for i in inputs.imports
                    for p in (i.replacement / i.import_subdirectory, i.site)
                )
            ),
            search,
            inputs.project_environment,
            inputs.demo_python,
            inputs.resources,
            inputs.preview_directory / "logs/build.log",
        )


def verify_prepared_manifests(prepared: PreparedPreview) -> None:
    """Recheck effective immutable inventories before adapting them to boundary mounts."""
    for inventory in prepared.manifests:
        _verify(inventory)


def scrub_preview_env(env: Mapping[str, str], prepared: PreparedPreview) -> dict[str, str]:
    """Final allowlist after caller additions; complete PATH defeats runner inheritance.

    Offline environment flags are policy hints. Network enforcement belongs to the boundary.
    """
    clean = {"TERM": "xterm"} if env.get("TERM") == "xterm" else {}
    return clean | {
        "PATH": os.pathsep.join(map(str, prepared.path)),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "HOME": "/state/home",
        "TMPDIR": "/tmp",
        "TMP": "/tmp",
        "TEMP": "/tmp",
        "XDG_CACHE_HOME": "/state/cache",
        "XDG_CONFIG_HOME": "/state/config",
        "XDG_DATA_HOME": "/state/data",
        "UV_CACHE_DIR": "/state/cache/uv",
        "UV_OFFLINE": "1",
        "UV_NO_SYNC": "1",
        "UV_PROJECT_ENVIRONMENT": str(prepared.project_environment),
        "UV_PYTHON": str(next(t.path for t in prepared.tools if t.name == "uv-python")),
        "PYTHONPATH": os.pathsep.join(map(str, prepared.import_paths)),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPYCACHEPREFIX": "/state/cache/pycache",
        "npm_config_cache": "/state/cache/npm",
        "npm_config_offline": "true",
        "PNPM_HOME": "/state/cache/pnpm",
    }
